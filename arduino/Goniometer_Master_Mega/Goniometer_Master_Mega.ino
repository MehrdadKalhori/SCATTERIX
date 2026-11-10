/*
 GONIOMETER MASTER -- Arduino Mega 2560, motor-only firmware, protocol v1.
 Upload ONCE for this STEP/DIR hardware; Python controls all scan settings.

 WIRING (as agreed for the present setup):
   Mega D3  -> STEP on stepper driver
   Mega D7  -> DIR  on stepper driver
   Mega GND -> signal GND on driver (NOT motor supply voltage)
   Motor driver + motor have their own appropriately rated power supply.
   No driver enable, home/limit switches, or encoder is assumed.

 Host: USB Serial 115200 baud; ASCII newline ('\\n') terminated.
   PING,id                           -> OK,id,PING,GONIO_MASTER_1
   HELP,id                           -> OK,id,HELP,SEE_README
   STATUS,id                         -> STATUS,id,deg,steps,targetDeg,moving,zeroed,speed,accel,stepsRev,min,max,invert
   ZERO,id                           -> OK,id,ZERO,0
   MOVE,absoluteDeg,id               -> ACK,id,MOVE,requestedDeg; DONE,id,actualEstimatedDeg,steps
   JOG,relativeDeg,id                -> ACK,id,MOVE,requestedDeg; DONE,id,actualEstimatedDeg,steps
   MOVE_STEPS,relativeSteps,id       -> ACK,...; DONE,...
   STOP,id                           -> ABORT,activeId,STOP,deg (if moving); OK,id,STOP,deg
   SPEED,stepsPerSecond,id           -> OK,id,SPEED,value
   ACCEL,stepsPerSecSquared,id       -> OK,id,ACCEL,value
   STEPS_REV,stepsPerRev,id          -> OK,id,STEPS_REV,value
   DIRINV,0_or_1,id                  -> OK,id,DIRINV,value
   LIMITS,minDeg,maxDeg,id           -> OK,id,LIMITS,min,max
   SAVE,id                           -> OK,id,SAVE
   LOAD,id                           -> OK,id,LOAD (resets ZERO state)
   DEFAULTS,id                       -> OK,id,DEFAULTS (not saved until SAVE)
 Errors: ERR,id,description. Startup: READY,GONIO_MASTER_1,ZERO_REQUIRED.

 VERY IMPORTANT:
   - ZERO establishes the manually aligned angle reference. No home sensor exists.
   - DONE confirms the commanded pulses finished; it does NOT measure shaft angle.
   - STOP is an immediate SOFTWARE stop, not a hardware emergency stop; any
     interrupted move invalidates zero and requires manual re-zeroing.
   - The position is not restored on reboot. ZERO is mandatory each startup.
   - Soft limits DO NOT replace mechanical stops or a hardware emergency switch.
   - No detector voltage or USB-4704 functionality is handled in this firmware.
*/
#include <Arduino.h>
#include <EEPROM.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>

const byte PIN_STEP = 3;
const byte PIN_DIR = 7;
const unsigned long SERIAL_BAUD = 115200UL;
const unsigned int STEP_HIGH_US = 6;    // typical STEP/DIR driver timing
const unsigned int DIR_SETUP_US = 12;
const uint32_t CONFIG_MAGIC = 0x474D5354UL; // "GMST"
const uint16_t CONFIG_VER = 1;

struct Config {
  uint32_t magic;
  uint16_t version;
  float stepsPerRev;
  float maxSpeed;  // step pulses / second
  float accel;     // step pulses / second^2
  float minAngle;
  float maxAngle;
  uint8_t invertDir;
  uint16_t checksum;
};
Config cfg;

long posSteps = 0;
long targetSteps = 0;
unsigned long activeMoveId = 0;
unsigned long nextStepAtUs = 0;
float speedNow = 0.0f;
bool moving = false;
bool zeroed = false;
int8_t motionSign = 1;

char rx[110];
uint8_t rxLen = 0;
bool rxOverflow = false;

uint16_t calcChecksum(const Config &c) {
  const uint8_t *b = (const uint8_t *)&c;
  uint16_t h = 0xAB19;
  for (size_t i = 0; i < offsetof(Config, checksum); ++i) {
    h ^= b[i];
    h = (uint16_t)((h << 5) | (h >> 11));
    h = (uint16_t)(h + 37U);
  }
  return h;
}

bool validFloat(float x) { return !(isnan(x) || isinf(x)); }

bool validConfig(const Config &c) {
  return c.magic == CONFIG_MAGIC && c.version == CONFIG_VER &&
    c.checksum == calcChecksum(c) &&
    validFloat(c.stepsPerRev) && c.stepsPerRev >= 200 && c.stepsPerRev <= 200000 &&
    validFloat(c.maxSpeed) && c.maxSpeed >= 10 && c.maxSpeed <= 2500 &&
    validFloat(c.accel) && c.accel >= 10 && c.accel <= 15000 &&
    validFloat(c.minAngle) && validFloat(c.maxAngle) &&
    c.minAngle >= -360 && c.maxAngle <= 360 && c.minAngle < c.maxAngle &&
    c.invertDir <= 1;
}

void defaults() {
  memset(&cfg, 0, sizeof(cfg));
  cfg.magic = CONFIG_MAGIC;
  cfg.version = CONFIG_VER;
  cfg.stepsPerRev = 1600.0f;   // initial driver microstep assumption
  cfg.maxSpeed = 350.0f;
  cfg.accel = 400.0f;
  cfg.minAngle = -170.0f;
  cfg.maxAngle = 170.0f;
  cfg.invertDir = 0;
  cfg.checksum = calcChecksum(cfg);
}

void loadConfig() {
  Config copy;
  EEPROM.get(0, copy);
  if (validConfig(copy)) cfg = copy;
  else defaults();
}

float stepsToDeg(long s) {
  return ((float)s * 360.0f) / cfg.stepsPerRev;
}

bool degToSteps(float deg, long &s) {
  if (!validFloat(deg) || deg < -360.001f || deg > 360.001f) return false;
  const float f = deg * cfg.stepsPerRev / 360.0f;
  s = (long)(f >= 0.0f ? f + 0.5f : f - 0.5f);
  return true;
}

bool withinLimits(long s) {
  const float d = stepsToDeg(s);
  const float roundingTolerance = 180.0f / cfg.stepsPerRev + 0.0002f;
  return d >= cfg.minAngle - roundingTolerance && d <= cfg.maxAngle + roundingTolerance;
}

bool parseFloat(const char *s, float &result) {
  if (!s || !*s) return false;
  char *end = NULL;
  double x = strtod(s, &end); // AVR-safe: unlike strtof in some AVR toolchains
  if (end == s || !end || *end != '\0') return false;
  result = (float)x;
  return validFloat(result);
}

bool parseUnsigned(const char *s, unsigned long &result) {
  if (!s || !*s || *s == '-') return false;
  char *end = NULL;
  unsigned long n = strtoul(s, &end, 10);
  if (end == s || !end || *end != '\0') return false;
  result = n;
  return true;
}

bool parseSigned(const char *s, long &result) {
  if (!s || !*s) return false;
  char *end = NULL;
  long n = strtol(s, &end, 10);
  if (end == s || !end || *end != '\0') return false;
  result = n;
  return true;
}

void error(unsigned long id, const __FlashStringHelper *reason) {
  Serial.print(F("ERR,")); Serial.print(id); Serial.print(','); Serial.println(reason);
}

void ok(unsigned long id, const __FlashStringHelper *cmd) {
  Serial.print(F("OK,")); Serial.print(id); Serial.print(','); Serial.println(cmd);
}

void printStatus(unsigned long id) {
  Serial.print(F("STATUS,")); Serial.print(id);
  Serial.print(','); Serial.print(stepsToDeg(posSteps), 5);
  Serial.print(','); Serial.print(posSteps);
  Serial.print(','); Serial.print(stepsToDeg(targetSteps), 5);
  Serial.print(','); Serial.print(moving ? 1 : 0);
  Serial.print(','); Serial.print(zeroed ? 1 : 0);
  Serial.print(','); Serial.print(cfg.maxSpeed, 2);
  Serial.print(','); Serial.print(cfg.accel, 2);
  Serial.print(','); Serial.print(cfg.stepsPerRev, 2);
  Serial.print(','); Serial.print(cfg.minAngle, 2);
  Serial.print(','); Serial.print(cfg.maxAngle, 2);
  Serial.print(','); Serial.println(cfg.invertDir);
}

void printDone(unsigned long id) {
  Serial.print(F("DONE,")); Serial.print(id);
  Serial.print(','); Serial.print(stepsToDeg(posSteps), 5);
  Serial.print(','); Serial.println(posSteps);
}

void stopMotion(unsigned long requestId) {
  if (moving) {
    moving = false;
    targetSteps = posSteps;
    speedNow = 0.0f;
    digitalWrite(PIN_STEP, LOW);
    zeroed = false;  // after an abrupt stop, true physical position is uncertain
    Serial.print(F("ABORT,")); Serial.print(activeMoveId);
    Serial.print(F(",STOP,")); Serial.println(stepsToDeg(posSteps), 5);
  }
  Serial.print(F("OK,")); Serial.print(requestId);
  Serial.print(F(",STOP,")); Serial.println(stepsToDeg(posSteps), 5);
}

void startMotion(long dest, unsigned long id) {
  targetSteps = dest;
  activeMoveId = id;
  Serial.print(F("ACK,")); Serial.print(id);
  Serial.print(F(",MOVE,")); Serial.println(stepsToDeg(targetSteps), 5);
  if (targetSteps == posSteps) {
    printDone(id);
    return;
  }
  motionSign = (targetSteps > posSteps) ? 1 : -1;
  const bool level = (motionSign > 0) != (cfg.invertDir != 0);
  digitalWrite(PIN_DIR, level ? HIGH : LOW);
  speedNow = 0.0f;
  nextStepAtUs = micros() + DIR_SETUP_US;
  moving = true;
}

void serviceMotor() {
  if (!moving) return;
  const unsigned long now = micros();
  if ((long)(now - nextStepAtUs) < 0) return; // overflow-safe for intervals < 35 min

  const long remaining = labs(targetSteps - posSteps);
  if (remaining <= 0) {
    moving = false;
    printDone(activeMoveId);
    return;
  }

  // Trapezoidal-ish motion with an acceleration/deceleration envelope.
  // Incremental v^2 = v0^2 +/- 2*a*one_step; no blocking move loops.
  const float v2 = speedNow * speedNow;
  const float stopDistance = v2 / (2.0f * cfg.accel);
  const float newV2 = (remaining <= stopDistance + 1.0f) ?
      (v2 - 2.0f * cfg.accel) : (v2 + 2.0f * cfg.accel);
  float newSpeed = sqrtf(newV2 > 0.0f ? newV2 : (2.0f * cfg.accel));
  if (newSpeed > cfg.maxSpeed) newSpeed = cfg.maxSpeed;
  // At very low speeds, ensure there is a sensible max pulse interval.
  if (newSpeed < 5.0f) newSpeed = 5.0f;
  speedNow = newSpeed;

  digitalWrite(PIN_STEP, HIGH);
  delayMicroseconds(STEP_HIGH_US);
  digitalWrite(PIN_STEP, LOW);
  posSteps += motionSign;
  if (posSteps == targetSteps) {
    moving = false;
    speedNow = 0.0f;
    printDone(activeMoveId);
    return;
  }
  unsigned long period = (unsigned long)(1000000.0f / speedNow + 0.5f);
  if (period < STEP_HIGH_US + 10U) period = STEP_HIGH_US + 10U;
  nextStepAtUs = micros() + period;
}

void handle(char *input) {
  // Split retaining empty arguments to reject malformed commands cleanly.
  char *part[5];
  uint8_t n = 1;
  part[0] = input;
  for (char *p = input; *p; ++p) {
    if (*p == ',') {
      if (n >= 5) { error(0, F("TOO_MANY_ARGS")); return; }
      *p = '\0'; part[n++] = p + 1;
    }
  }
  const char *cmd = part[0];
  unsigned long id = 0;
  if (!parseUnsigned(part[n - 1], id)) { error(0, F("BAD_ID")); return; }

  if (strcmp(cmd, "PING") == 0 && n == 2) {
    Serial.print(F("OK,")); Serial.print(id); Serial.println(F(",PING,GONIO_MASTER_1"));
    return;
  }
  if (strcmp(cmd, "HELP") == 0 && n == 2) { ok(id, F("HELP,SEE_README")); return; }
  if (strcmp(cmd, "STATUS") == 0 && n == 2) { printStatus(id); return; }
  if (strcmp(cmd, "STOP") == 0 && n == 2) { stopMotion(id); return; }

  if (moving) { error(id, F("BUSY")); return; }

  if (strcmp(cmd, "ZERO") == 0 && n == 2) {
    posSteps = 0; targetSteps = 0; zeroed = true;
    ok(id, F("ZERO,0")); return;
  }
  if ((strcmp(cmd, "MOVE") == 0 || strcmp(cmd, "JOG") == 0) && n == 3) {
    if (!zeroed) { error(id, F("ZERO_REQUIRED")); return; }
    float degrees = 0;
    if (!parseFloat(part[1], degrees)) { error(id, F("BAD_ANGLE")); return; }
    const float destination = strcmp(cmd, "MOVE") == 0 ? degrees : stepsToDeg(posSteps) + degrees;
    long steps = 0;
    if (!degToSteps(destination, steps) || !withinLimits(steps)) {
      error(id, F("SOFT_LIMIT")); return;
    }
    startMotion(steps, id); return;
  }
  if (strcmp(cmd, "MOVE_STEPS") == 0 && n == 3) {
    if (!zeroed) { error(id, F("ZERO_REQUIRED")); return; }
    long delta = 0;
    if (!parseSigned(part[1], delta) || delta < -200000L || delta > 200000L) {
      error(id, F("BAD_STEPS")); return;
    }
    const long dest = posSteps + delta;
    if (!withinLimits(dest)) { error(id, F("SOFT_LIMIT")); return; }
    startMotion(dest, id); return;
  }
  if ((strcmp(cmd, "SPEED") == 0 || strcmp(cmd, "ACCEL") == 0 ||
       strcmp(cmd, "STEPS_REV") == 0) && n == 3) {
    float v = 0;
    if (!parseFloat(part[1], v)) { error(id, F("BAD_VALUE")); return; }
    if (strcmp(cmd, "SPEED") == 0) {
      if (v < 10 || v > 2500) { error(id, F("SPEED_RANGE_10_2500")); return; }
      cfg.maxSpeed = v;
    } else if (strcmp(cmd, "ACCEL") == 0) {
      if (v < 10 || v > 15000) { error(id, F("ACCEL_RANGE_10_15000")); return; }
      cfg.accel = v;
    } else {
      if (v < 200 || v > 200000) { error(id, F("STEPS_REV_RANGE_200_200000")); return; }
      cfg.stepsPerRev = v;
      posSteps = 0; targetSteps = 0; zeroed = false;
    }
    Serial.print(F("OK,")); Serial.print(id); Serial.print(',');
    Serial.print(cmd); Serial.print(','); Serial.println(v, 3);
    return;
  }
  if (strcmp(cmd, "DIRINV") == 0 && n == 3) {
    if (strcmp(part[1], "0") != 0 && strcmp(part[1], "1") != 0) {
      error(id, F("DIRINV_EXPECTS_0_OR_1")); return;
    }
    cfg.invertDir = (uint8_t)(part[1][0] - '0');
    zeroed = false;
    Serial.print(F("OK,")); Serial.print(id);
    Serial.print(F(",DIRINV,")); Serial.println(cfg.invertDir);
    return;
  }
  if (strcmp(cmd, "LIMITS") == 0 && n == 4) {
    float lo = 0, hi = 0;
    if (!parseFloat(part[1], lo) || !parseFloat(part[2], hi) ||
        lo < -360 || hi > 360 || lo >= hi) {
      error(id, F("BAD_LIMITS")); return;
    }
    if (zeroed) {
      float deg = stepsToDeg(posSteps);
      if (deg < lo || deg > hi) { error(id, F("POSITION_OUTSIDE_NEW_LIMITS")); return; }
    }
    cfg.minAngle = lo; cfg.maxAngle = hi;
    Serial.print(F("OK,")); Serial.print(id); Serial.print(F(",LIMITS,"));
    Serial.print(lo, 3); Serial.print(','); Serial.println(hi, 3);
    return;
  }
  if (strcmp(cmd, "SAVE") == 0 && n == 2) {
    cfg.checksum = calcChecksum(cfg);
    EEPROM.put(0, cfg); ok(id, F("SAVE")); return;
  }
  if (strcmp(cmd, "LOAD") == 0 && n == 2) {
    loadConfig(); posSteps = targetSteps = 0; zeroed = false;
    ok(id, F("LOAD")); return;
  }
  if (strcmp(cmd, "DEFAULTS") == 0 && n == 2) {
    defaults(); posSteps = targetSteps = 0; zeroed = false;
    ok(id, F("DEFAULTS")); return;
  }
  error(id, F("UNKNOWN_OR_BAD_ARGUMENTS"));
}

void serviceSerial() {
  while (Serial.available() > 0) {
    const char ch = (char)Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (rxOverflow) {
        error(0, F("LINE_TOO_LONG"));
      } else if (rxLen > 0) {
        rx[rxLen] = '\0'; handle(rx);
      }
      rxLen = 0; rxOverflow = false;
    } else if (!rxOverflow) {
      if (rxLen < sizeof(rx) - 1) rx[rxLen++] = ch;
      else rxOverflow = true;
    }
  }
}

void setup() {
  pinMode(PIN_STEP, OUTPUT); pinMode(PIN_DIR, OUTPUT);
  digitalWrite(PIN_STEP, LOW); digitalWrite(PIN_DIR, LOW);
  loadConfig();
  // Position/zero validity is never persisted across reboots.
  posSteps = targetSteps = 0; moving = false; zeroed = false;
  Serial.begin(SERIAL_BAUD);
  Serial.println(F("READY,GONIO_MASTER_1,ZERO_REQUIRED"));
}

void loop() {
  serviceSerial();
  serviceMotor();
}

// Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠
