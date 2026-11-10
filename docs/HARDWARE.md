# Hardware and optical setup

## Components assumed by this software version

- Arduino Mega 2560 and a STEP/DIR motor driver connected to a mechanically constrained optical goniometer.
- Laser source and suitable laser safety controls.
- Photodetector/transimpedance amplifier (e.g. Thorlabs PDA200C) and an Advantech USB-4704 data-acquisition device.
- Windows computer, Arduino IDE, Python and a compatible vendor DAQ SDK.

## Firmware connections

| Arduino Mega | Firmware role |
|---|---|
| D3 | Motor driver's STEP signal |
| D7 | Motor driver's DIR signal |
| GND | Driver's signal return, subject to the driver's wiring instructions |
| USB serial | Python motor-command interface, **115200 baud** |

The **DAQ uses its own analog input** to read detector voltage in V10, rather than using Mega A8. Do not connect the PDA200C output to the USB-4704 without verifying the maximum output voltage (including over-range behavior), DAQ allowable common-mode and differential voltage ranges, grounding and input protection. A reported amplifier output exceeding 16 V must never be assumed safe for a 0–5 V ADC or a lower-range DAQ input.

The motor driver power supply is separate from the logic signals and must be wired according to its data sheet. No home switch, angle encoder or hardware end stop is presently assumed by the firmware. Implement real emergency stop and physical guards for unattended or high-angle movement.

## Angular calibration

Optically identify a reproducible 0° propagation axis; mark the actual mechanical zero only after visual/optical alignment. Check the angular direction convention and motor gear ratio. 1600 pulses/revolution is a **configured assumption**, not evidence of actual output shaft angle. Check backlash and missed-step behavior empirically.

The sample-to-aperture distance, aperture diameter, detector distance and finite receiver acceptance angle need to be documented per run. The specific geometry determines the angular response, especially around 0°, 90° and the unmeasured rear sector. Changing detector or sample geometry can invalidate direct comparison of angular intensity.

## Blank, dark and gain control

Measure dark voltage with the laser blocked; measure reference/empty-cuvette response under the same optical geometry and gain. Keep amplifier range, attenuation, DAQ calibration and illumination conditions stable between blood and reference. Record any clipping/saturation, gain changes, sample preparation, sample thickness and wavelength.

## Laser and biological safety

Use wavelength-appropriate eye protection, enclosures and alignment practices. Handle human blood as potentially infectious material following institutional biosafety procedures. Do not upload identifying human-sample metadata.

---

*Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠*
