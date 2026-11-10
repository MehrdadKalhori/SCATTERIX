# -*- coding: utf-8 -*-
"""Goniometer Lab 10.0: responsive UI, streamed acquisition journals and ONE Excel paper report.
Single Python file, usable from VS Code Jupyter. Motor DONE is not encoder feedback.
"""

import math
import csv
import os
import statistics
import sys
from pathlib import Path
import queue
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


# -----------------------------------------------------------------------------
# Protocol: matches Goniometer_Master_Mega.ino, baud 115200, newline commands
# -----------------------------------------------------------------------------
class MotorError(RuntimeError):
    pass


class MegaMotor:
    BAUD = 115200

    def __init__(self, port, log_callback=None):
        if serial is None:
            raise MotorError("کتابخانه pyserial نصب نیست. در Jupyter بنویسید: %pip install pyserial")
        self._log = log_callback or (lambda s: None)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._waiters = {}
        self._next_id = 0
        try:
            self._ser = serial.Serial(port=port, baudrate=self.BAUD,
                                      timeout=0.15, write_timeout=2)
        except Exception as exc:
            raise MotorError("بازکردن پورت ناموفق بود؛ Serial Monitor را ببندید. " + str(exc)) from exc
        # Mega normally resets on USB serial open: discard READY after startup.
        time.sleep(2.2)
        try:
            self._ser.reset_input_buffer()
        except Exception:
            self._ser.close()
            raise
        self._reader = threading.Thread(target=self._read_forever, daemon=True)
        self._reader.start()
        try:
            p = self._request("PING", expected="OK", timeout=5)
            if len(p) < 4 or p[2:4] != ["PING", "GONIO_MASTER_1"]:
                raise MotorError("Firmware سازگار نیست. باید GONIO_MASTER_1 روی Mega باشد.")
        except Exception:
            self.close()
            raise

    def _read_forever(self):
        while not self._closed.is_set():
            try:
                data = self._ser.readline()
                if not data:
                    continue
                line = data.decode("ascii", errors="replace").strip()
                if not line:
                    continue
                cells = line.split(",")
                if cells[0] in ("OK", "ERR", "ACK", "DONE", "ABORT", "STATUS"):
                    try:
                        number = int(cells[1])
                    except (IndexError, ValueError):
                        self._log("Malformed: " + line)
                        continue
                    with self._lock:
                        waiter = self._waiters.get(number)
                    if waiter is not None:
                        waiter.put(cells)
                    else:
                        self._log("Arduino: " + line)
                else:
                    self._log("Arduino: " + line)
            except Exception as exc:
                if not self._closed.is_set():
                    self._log("Serial error: " + str(exc))
                break
        with self._lock:
            all_waiters = list(self._waiters.values())
        for waiter in all_waiters:
            waiter.put(["ERR", "0", "SERIAL_DISCONNECTED"])

    def _send(self, cmd, *args):
        if self._closed.is_set():
            raise MotorError("اتصال سریال بسته شده است.")
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            inbox = queue.Queue()
            self._waiters[request_id] = inbox
        packet = ",".join([cmd] + [str(v) for v in args] + [str(request_id)]) + "\n"
        try:
            with self._write_lock:
                self._ser.write(packet.encode("ascii"))
                self._ser.flush()
        except Exception:
            self._finish(request_id)
            raise
        return request_id, inbox

    def _finish(self, request_id):
        with self._lock:
            self._waiters.pop(request_id, None)

    @staticmethod
    def _next(inbox, deadline):
        try:
            fields = inbox.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty as exc:
            raise MotorError("مهلت پاسخ آردوینو تمام شد (Timeout).") from exc
        if fields[0] == "ERR":
            raise MotorError("خطای آردوینو: " + ",".join(fields[2:]))
        if fields[0] == "ABORT":
            raise MotorError("حرکت متوقف شد: " + ",".join(fields[2:]))
        return fields

    def _request(self, cmd, *args, expected="OK", timeout=6):
        request_id, inbox = self._send(cmd, *args)
        try:
            deadline = time.monotonic() + timeout
            while True:
                reply = self._next(inbox, deadline)
                if reply[0] == expected:
                    return reply
        finally:
            self._finish(request_id)

    def status(self):
        f = self._request("STATUS", expected="STATUS", timeout=5)
        if len(f) != 13:
            raise MotorError("پاسخ STATUS با Firmware تطابق ندارد: " + str(f))
        return {
            "degrees": float(f[2]), "steps": int(f[3]),
            "target": float(f[4]), "moving": bool(int(f[5])),
            "zeroed": bool(int(f[6])), "speed": float(f[7]),
            "accel": float(f[8]), "steps_per_rev": float(f[9]),
            "min": float(f[10]), "max": float(f[11]),
            "inverted": bool(int(f[12])),
        }

    def zero(self):
        return self._request("ZERO")

    def set_speed(self, speed):
        return self._request("SPEED", speed)

    def set_accel(self, accel):
        return self._request("ACCEL", accel)

    def save_settings(self):
        return self._request("SAVE")

    def stop(self):
        return self._request("STOP", timeout=5)

    def _motion(self, command, argument, timeout=600):
        ident, inbox = self._send(command, argument)
        try:
            ack_deadline = time.monotonic() + 5
            while True:
                reply = self._next(inbox, ack_deadline)
                if reply[0] == "ACK":
                    break
            motion_deadline = time.monotonic() + timeout
            while True:
                reply = self._next(inbox, motion_deadline)
                if reply[0] == "DONE":
                    return {"id": ident, "degrees": float(reply[2]),
                            "steps": int(reply[3])}
        except MotorError as exc:
            if "Timeout" in str(exc):
                try:
                    self.stop()
                except Exception:
                    pass
            raise
        finally:
            self._finish(ident)

    def move(self, angle):
        return self._motion("MOVE", float(angle))

    def jog(self, delta):
        return self._motion("JOG", float(delta))

    def close(self):
        if not self._closed.is_set():
            self._closed.set()
            try:
                self._ser.close()
            except Exception:
                pass


def scan_angles(start, end, step):
    """Inclusive directed sweep. Include the requested endpoint once."""
    values = (start, end, step)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("مقادیر زاویه و گام باید عدد متناهی باشند.")
    if step <= 0:
        raise ValueError("گام زاویه‌ای باید بزرگ‌تر از صفر باشد.")
    if start == end:
        return [round(start, 6)]
    span = abs(end - start)
    n = int(math.floor(span / step + 1e-9))
    if n > 1000:
        raise ValueError("بازه بیش از ۱۰۰۰ گام دارد؛ گام را بزرگ‌تر کنید.")
    direction = 1 if end > start else -1
    points = [round(start + i * direction * step, 6) for i in range(n + 1)]
    if abs(points[-1] - end) > 1e-6:
        points.append(round(end, 6))
    return points


# -----------------------------------------------------------------------------
# Modern dark Tkinter UI - no external UI library required
# -----------------------------------------------------------------------------

DAQ_SDK_ROOT = r"C:\Advantech\DAQNavi\Examples\Python"
DAQ_DEFAULT_PROFILE = r"C:\Advantech\public\Navigator\USB4704_Diff20.xml"
DAQ_DEVICE = "USB-4704,BID#0"
CSV_FIELDS = [
    "timestamp", "source", "run", "requested_angle_deg", "done_estimated_angle_deg",
    "motor_steps", "samples", "sample_period_s", "raw_voltages_V", "mean_voltage_V",
    "median_voltage_V", "std_voltage_V", "min_voltage_V", "max_voltage_V",
    "daq_device", "daq_logical_channel", "offset_note"
]


class DAQError(RuntimeError):
    pass


class USB4704:
    """USB-4704 driver: InstantAI only for idle live display;
    WaveformAiCtrl exclusively for each high-rate, hardware-clocked angle window.

    Runtime-specific API support is checked explicitly. There is NO silent fallback
    to slow host polling if hardware buffering fails.
    """
    def __init__(self, profile, sdk_root=DAQ_SDK_ROOT):
        profile = Path(profile).expanduser()
        if not profile.is_file() or profile.suffix.lower() != '.xml':
            raise DAQError('فایل پروفایل XML واقعی USB-4704 پیدا نشد.')
        root = Path(sdk_root)
        if not (root/'Automation'/'BDaq').is_dir():
            raise DAQError('پوشه SDK پایتون Advantech پیدا نشد: '+str(root))
        if str(root) not in sys.path:sys.path.insert(0,str(root))
        try:
            from Automation.BDaq.InstantAiCtrl import InstantAiCtrl
            from Automation.BDaq.BDaqApi import BioFailed
        except Exception as exc:
            raise DAQError('درایور Python DAQNavi بارگذاری نشد: '+str(exc)) from exc
        self.InstantAiCtrl=InstantAiCtrl
        self._failed=BioFailed
        self.profile=str(profile)
        self.lock=threading.RLock()
        self.ctrl=None
        self.wave=None
        self.mode='idle'
        self._open_instant()
        self.read()

    def _check(self, ret, label):
        if self._failed(ret):
            number=getattr(ret,'value',ret)
            raise DAQError(f'{label} failed: error={number}')

    def _open_instant(self):
        with self.lock:
            obj=None
            try:
                obj=self.InstantAiCtrl(DAQ_DEVICE)
                obj.loadProfile=self.profile
                self.ctrl=obj
                self.mode='instant'
            except Exception:
                if obj is not None:
                    try:obj.dispose()
                    except Exception:pass
                self.ctrl=None
                raise

    def read(self):
        with self.lock:
            if self.mode != 'instant' or self.ctrl is None:
                raise DAQError('خواندن زنده در حالت Buffered فعال نیست.')
            ret, values=self.ctrl.readData(0,1)
            self._check(ret,'InstantAI.readData')
            if len(values)!=1 or not math.isfinite(float(values[0])):
                raise DAQError('قرائت لحظه‌ای DAQ نامعتبر است.')
            return float(values[0])

    def begin_buffered(self):
        """Mutually exclusive DAQ controller ownership. Call before the first MOVE.
        No motor step is started if WaveformAiCtrl cannot open.
        """
        with self.lock:
            if self.mode=='buffered':return
            if self.ctrl is not None:
                self.ctrl.dispose();self.ctrl=None
            try:
                from Automation.BDaq.WaveformAiCtrl import WaveformAiCtrl
                self.wave=WaveformAiCtrl(DAQ_DEVICE)
                self.wave.loadProfile=self.profile
                for name in ('conversion','record','prepare','start','stop'):
                    if not hasattr(self.wave,name):
                        raise DAQError('SDK فعلی WaveformAiCtrl فاقد '+name+' است؛ '
                            'نسخه SDK را بررسی کن، نرخ نمونه‌برداری به‌صورت ساختگی اعلام نمی‌شود.')
                if not (hasattr(self.wave,'getDataF64') or hasattr(self.wave,'getData')):
                    raise DAQError('SDK فعلی فاقد WaveformAiCtrl.getData است.')
                self.mode='buffered'
            except Exception as exc:
                if self.wave is not None:
                    try:self.wave.dispose()
                    except Exception:pass
                    self.wave=None
                self.mode='idle'
                try:self._open_instant()
                except Exception:pass
                raise DAQError('اتصال BufferedAI ناموفق: '+str(exc)) from exc

    def capture_buffered(self, count, rate_hz, timeout_s, cancel=None):
        """Finite, HW-clocked conversion; validates returned sample count.
        Sample times 0..(count-1)/Fs are reconstructed from configured clock,
        NOT individually measured host timestamps.
        """
        with self.lock:
            if self.mode!='buffered' or self.wave is None:
                raise DAQError('ابتدا جلسه BufferedAI را فعال کن.')
            if cancel is not None and cancel.is_set():
                raise InterruptedError('اسکن متوقف شده است.')
            if timeout_s <= 0 or count/rate_hz >= timeout_s:
                raise DAQError('زمان باقی‌مانده برای ثبت Buffered کافی نیست.')
            wf=self.wave
            t0=time.monotonic()
            try:
                wf.conversion.channelStart=0
                wf.conversion.channelCount=1
                wf.conversion.clockRate=float(rate_hz)
                # Section length may need to be >=256 depending on SDK/version.
                wf.record.sectionLength=max(256,int(count))
                wf.record.sectionCount=2
                wf.record.cycles=1
                self._check(wf.prepare(),'WaveformAI.prepare')
                self._check(wf.start(),'WaveformAI.start')
                hardware_start_estimate=time.monotonic()  # host estimate, NOT a hardware timestamp
                rem=timeout_s-(time.monotonic()-t0)
                if rem <= 0:
                    raise DAQError('آماده‌سازی DAQ پنجره زمانی مکث را مصرف کرد.')
                millis=max(1,int(rem*1000))
                if hasattr(wf,'getDataF64'):
                    ans=wf.getDataF64(int(count),millis)
                else:
                    ans=wf.getData(int(count),millis)
                if not isinstance(ans,(tuple,list)) or len(ans)<2:
                    raise DAQError('ساختار پاسخ getData SDK ناشناخته است؛ داده پذیرفته نمی‌شود.')
                self._check(ans[0],'WaveformAI.getData')
                if len(ans)>=3 and isinstance(ans[1],(int,float)):
                    returned=int(ans[1]); data=ans[2]
                else:
                    data=ans[1]; returned=len(data)
                vals=[float(v) for v in data]
                if returned != count or len(vals)<count:
                    raise DAQError(f'نمونه ناقص از سخت‌افزار: {returned}/{count}')
                vals=vals[:count]
                if not all(math.isfinite(v) for v in vals):
                    raise DAQError('نمونه غیرمتناهی از DAQ دریافت شد.')
                elapsed=time.monotonic()-t0
                if elapsed>timeout_s:
                    raise DAQError('نمونه‌برداری از مکث مجاز فراتر رفت؛ داده معتبر اعلام نمی‌شود.')
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('اسکن هنگام نمونه‌برداری متوقف شد.')
                return vals, elapsed, hardware_start_estimate
            except Exception:
                raise
            finally:
                try:self._check(wf.stop(),'WaveformAI.stop')
                except Exception:pass

    def end_buffered(self):
        with self.lock:
            if self.wave is not None:
                try:self.wave.stop()
                except Exception:pass
                try:self.wave.dispose()
                except Exception:pass
                self.wave=None
            if self.ctrl is None:
                self.mode='idle'
                self._open_instant()

    def close(self):
        with self.lock:
            if self.wave is not None:
                try:self.wave.stop()
                except Exception:pass
                try:self.wave.dispose()
                except Exception:pass
                self.wave=None
            if self.ctrl is not None:
                try:self.ctrl.dispose()
                except Exception:pass
                self.ctrl=None
            self.mode='closed'


# -----------------------------------------------------------------------------
# Research analysis: work in measured volts with the same amplifier gain.
# The source/reference is NEVER used in g fitting.
# -----------------------------------------------------------------------------

def phase_hg(theta_deg, g):
    theta = math.radians(theta_deg)
    return (1 - g*g) / (4*math.pi*(1+g*g-2*g*math.cos(theta))**1.5)


def filter_voltage_trace(raw, alpha=0.20, spike_sigma=6.0, lsb_V=40.0/16384.0):
    """Per-angle filtering; no data deletion. MAD-spikes are flags, never zero.

    Robust median of non-spikes is intensity for scientific analysis; even if
    all samples are suspicious, the original median is kept with LOW_CONFIDENCE.
    EMA is reset at each angle and is supplied for display/audit, not HG fit.
    """
    values=[float(x) for x in raw]
    if not values or not all(math.isfinite(x) for x in values):
        raise ValueError('داده DAQ نامعتبر است')
    if not (0 < alpha <= 1 and 3 <= spike_sigma <= 20):
        raise ValueError('پارامترهای فیلتر نامعتبرند')
    med=statistics.median(values)
    mad_sigma=1.4826*statistics.median(abs(x-med) for x in values)
    threshold=spike_sigma*max(mad_sigma,lsb_V)
    is_spike=[abs(x-med)>threshold for x in values]
    kept=[x for x,b in zip(values,is_spike) if not b]
    n=len(values);bad=len(values)-len(kept)
    insufficient=(len(kept)<max(3, int(0.5*n)))
    if insufficient:kept=values
    robust=statistics.median(kept)
    # EMA receives robust substitute for flagged impulse; the original is preserved.
    series=[];ema=None
    for x,b in zip(values,is_spike):
        inp=robust if (b and not insufficient) else x
        ema=inp if ema is None else (1-alpha)*ema+alpha*inp
        series.append(ema)
    # Mean of settled half; never use the first initial transient as end-point.
    ema_tail_mean=statistics.mean(series[len(series)//2:])
    return {'raw_median':med,'robust_median':robust,'mad_sigma':mad_sigma,
            'spikes':is_spike,'spike_count':bad,'insufficient':insufficient,
            'ema_trace':series,'ema_tail_mean':ema_tail_mean,'kept_count':len(kept)}


def symmetric_display_ema(values, alpha):
    """Only for plotting (zero-phase-like forward/backward EMA, not statistics)."""
    if not values:return []
    def once(a):
        y=[a[0]]
        for v in a[1:]:y.append((1-alpha)*y[-1]+alpha*v)
        return y
    return once(list(reversed(once(values))))[::-1]


def analyze_scattering(records, reference_v, thickness_cm, max_forward_deg=90.,
                       minimum_angle_deg=2., baseline_v=0.):
    """Audit-friendly estimators of angular scattering and apparent attenuation.

    g_paper: Eq. (6), Ziaee et al., Progress in Biomaterials 13 (2024), 132412;
             separate nonnegative/nonpositive 0..90 degree sectors, then their
             arithmetic mean if both exist. This replicates the user's published
             discrete estimator, WITHOUT a sin(theta) factor or cuvette reference.
    g_moment_partial: 3D solid-angle weighted moment over MEASURED polar coverage.
    g_HG: legacy V5 robust forward HG fit, not independently measured true g.
    No mu_s or mu_a is inferred from uncalibrated angular volts.
    """
    result = {
        'n_valid':0, 'g':None, 'r2_log':None, 'mu_t_app_cm_inv':None,
        'blood_max_V':None, 'fit_points':[], 'notes':[],
        'mu_a_cm_inv':None, 'mu_s_cm_inv':None,
        'g_paper_pos':None, 'g_paper_neg':None, 'g_paper_avg':None,
        'g_moment_partial':None, 'moment_coverage_deg':None,
        'moment_denominator_Vrad':None, 'moment_numerator_Vrad':None,
        'angular_integral_proxy_Vsr':None, 'optical_depth_app':None,
        'transmittance_app':None, 'penetration_depth_app_cm':None,
        'signed_angle_rows':[], 'paper_rows':[], 'moment_rows':[],
        'hg_input_rows':[], 'paper_pos_count':0, 'paper_neg_count':0,
        'paper_range_deg':90., 'hg_range_deg':(minimum_angle_deg,max_forward_deg),
    }
    good=[]
    for r in records:
        if r.get('status') not in ('OK','LOW_CONFIDENCE'):
            continue
        try:
            a=float(r['done_estimated_angle_deg'])
            v=float(r['intensity_proxy_V'] if 'intensity_proxy_V' in r else r['median_voltage_V'])
        except (ValueError,TypeError,KeyError):
            continue
        if math.isfinite(a) and math.isfinite(v):
            good.append((a,v))
    result['n_valid']=len(good)
    n_low=sum(r.get('status')=='LOW_CONFIDENCE' for r in records)
    if n_low:
        result['notes'].append(f'{n_low} پنجره کم‌اطمینان با مقدار اندازه‌گیری‌شده وارد شاخص مقاله شدند؛ برای استنباط فیزیکی به حساسیت‌سنجی، تکرار مستقل و ارزیابی زمینه نیاز است.')
    if not good:
        result['notes'].append('هیچ داده زاویه‌ای عددی و کامل برای تحلیل وجود ندارد. داده ساختگی جایگزین نشده است.')
        return result

    # One point per distinct measured signed angle; repeats are collapsed by median.
    signed={}
    for a,v in good:
        signed.setdefault(round(a,4),[]).append(v)
    points=[(a,statistics.median(vs),len(vs)) for a,vs in sorted(signed.items())]
    result['signed_angle_rows']=[{'theta_signed_deg':a,'intensity_used_V':v,'n_repeats':n,
                                  'source':'median of all complete repeats (V); quality audited separately'} for a,v,n in points]
    result['blood_max_V']=max(v for a,v,n in points)
    if any(v<0 for a,v,n in points):
        result['notes'].append('برخی مقادیر ولتاژ منفی‌اند؛ روش مقاله همان شدت خام را به کار می‌برد. تفسیر فیزیکی این مقادیر نیازمند بررسی زمینه است.')

    # The empty-cuvette reference enters ONLY the attenuation formula.
    if math.isfinite(reference_v) and math.isfinite(thickness_cm) and thickness_cm>0 and \
       math.isfinite(baseline_v) and reference_v>baseline_v and result['blood_max_V']>baseline_v:
        trans=(result['blood_max_V']-baseline_v)/(reference_v-baseline_v)
        result['transmittance_app']=trans
        tau=-math.log(trans)
        result['optical_depth_app']=tau
        result['mu_t_app_cm_inv']=tau/thickness_cm
        if result['mu_t_app_cm_inv']>0:
            result['penetration_depth_app_cm']=1/result['mu_t_app_cm_inv']
        if trans>1:
            result['notes'].append('بیشینه نمونه از مرجع بیشتر است؛ نسبت عبور > 1 و μt ظاهری منفی است.')
        result['notes'].append('μt ظاهری فقط از 16.37 V مرجع کووت خالی (یا مقدار واردشده)، V بیشینه خون و ضخامت محاسبه شده؛ اگر بیشینه منحنی بالستیک نباشد μt ذاتی نیست.')
    else:
        result['notes'].append('μt محاسبه نشد: مرجع، ضخامت، زمینه یا بیشینه خون نامعتبر است؛ محاسبات g مستقل ادامه می‌یابند.')

    # EXACT discrete paper estimator: sum(I*cos theta)/sum(I), separate +/-
    # theta from 0..90 degrees. No sin(theta), no baseline correction.
    pos=[(a,v) for a,v,n in points if 0<=a<=90]
    neg=[(a,v) for a,v,n in points if -90<=a<=0]
    def discrete(rows):
        den=sum(v for a,v in rows)
        num=sum(v*math.cos(math.radians(a)) for a,v in rows)
        return (num/den if abs(den)>1e-14 else None),num,den
    gpos,npos,dpos=discrete(pos)
    gneg,nneg,dneg=discrete(neg)
    result['paper_pos_count']=len(pos);result['paper_neg_count']=len(neg)
    result['g_paper_pos']=gpos if pos else None
    result['g_paper_neg']=gneg if neg else None
    if any(a>0 for a,v in pos) and any(a<0 for a,v in neg) and gpos is not None and gneg is not None:
        result['g_paper_avg']=(gpos+gneg)/2
    elif any(a>0 for a,v in pos):
        result['g_paper_avg']=gpos
    elif any(a<0 for a,v in neg):
        result['g_paper_avg']=gneg
    elif pos or neg:
        result['g_paper_avg']=gpos if gpos is not None else gneg
    for side,vals in (('positive',pos),('negative',neg)):
        for a,v in vals:
            result['paper_rows'].append({'side':side,'theta_deg':a,'intensity_V':v,
                                         'cos_theta':math.cos(math.radians(a)),
                                         'I_cos_V':v*math.cos(math.radians(a))})
    if result['g_paper_avg'] is None:
        result['notes'].append('g_paper: مخرج مجموع شدت در بازه ±90° صفر یا فاقد داده معتبر است.')
    if (pos and neg) and (len(pos)<3 or len(neg)<3):
        result['notes'].append('g_paper: در یکی از دو جهت تعداد زاویه معتبر کم است.')
    result['notes'].append('g_paper همان معادله (6) مقاله 2024 با داده‌های کامل (از جمله LOW_CONFIDENCE) و خلاصه‌سازی تکرارها به میانه است؛ نتایج به داده‌های زیر کف نویز حساس‌اند و به‌صورت شاخص تجربی گزارش شوند.')
    result['notes'].append('g_moment تنها با فرض تقارن سمتی-محوری پراکندگی و تناسب ولتاژ با شدت بر واحد زاویه فضایی قابل تفسیر فیزیکی است؛ پاسخ زاویه‌ای آشکارساز هنوز کالیبره نشده است.')

    # Solid-angle weighted polar moment over ACTUALLY OBSERVED range only.
    # Two signs at a given |theta| are averaged, without counting as extra solid angle.
    polar={}
    for a,v,n in points:
        theta=round(abs(a),4)
        if theta<=180:
            polar.setdefault(theta,[]).append(v-baseline_v)
    pol_points=[(t,statistics.median(vs)) for t,vs in sorted(polar.items())]
    # Negative baseline-corrected voltage cannot be interpreted as nonnegative radiance;
    # do not silently clip. Skip and mark that selection in the notes.
    rejected=sum(1 for t,v in pol_points if v<0)
    pol_points=[(t,v) for t,v in pol_points if math.isfinite(v) and v>=0]
    if rejected:
        result['notes'].append(f'g_moment: {rejected} زاویه با ولتاژ کمتر از زمینه کنار گذاشته شد؛ مقادیر خام دست‌نخورده باقی ماندند.')
    if len(pol_points)>=3:
        den=num=0.
        prev=None
        for t,v in pol_points:
            rad=math.radians(t)
            si=math.sin(rad);co=math.cos(rad)
            d=v*si;nn=d*co
            segment_den=segment_num=0.
            if prev is not None:
                tprev,dprev,nprev=prev
                dx=math.radians(t-tprev)
                segment_den=.5*(d+dprev)*dx
                segment_num=.5*(nn+nprev)*dx
                den+=segment_den;num+=segment_num
            result['moment_rows'].append({'theta_polar_deg':t,'intensity_minus_baseline_V':v,
                      'sin_theta':si,'cos_theta':co,'I_sin_V':d,'I_cos_sin_V':nn,
                      'segment_den_Vrad':segment_den,'segment_num_Vrad':segment_num})
            prev=(t,d,nn)
        if den>0:
            result['g_moment_partial']=num/den
            result['moment_denominator_Vrad']=den
            result['moment_numerator_Vrad']=num
            result['angular_integral_proxy_Vsr']=2*math.pi*den
            result['moment_coverage_deg']=(pol_points[0][0],pol_points[-1][0])
            if pol_points[0][0]>0.5 or pol_points[-1][0]<179.5:
                result['notes'].append(f'g_moment فقط بر بازه مشاهده‌شده {pol_points[0][0]:.2f}° تا {pol_points[-1][0]:.2f}° محاسبه شده؛ مقدار کامل 0..180° نیست.')
            gaps=[b[0]-a[0] for a,b in zip(pol_points,pol_points[1:])]
            if len(gaps)>=3 and max(gaps)>max(15,3*statistics.median(gaps)):
                result['notes'].append('g_moment: شکاف زاویه‌ای بزرگ در داده‌ها وجود دارد؛ انتگرال ذوزنقه‌ای از این شکاف عبور کرده و باید با احتیاط گزارش شود.')
        else:
            result['notes'].append('g_moment: انتگرال مخرج مثبت نیست.')
    else:
        result['notes'].append('g_moment: کمتر از ۳ زاویه قطبی معتبر وجود دارد.')

    # V5 Henyey-Greenstein fit retained unchanged: log residual, soft-L1 loss.
    by_abs={}
    for a,v,n in points:
        aa=abs(a)
        if minimum_angle_deg<=aa<=max_forward_deg and v-baseline_v>0:
            by_abs.setdefault(round(aa,4),[]).append(v-baseline_v)
    xx=sorted(by_abs)
    yy=[statistics.median(by_abs[t]) for t in xx]
    if len(xx)>=6 and xx[-1]-xx[0]>=25:
        try:
            import numpy as np
            from scipy.optimize import least_squares
            tx=np.asarray(xx,dtype=float)
            vy=np.asarray(yy,dtype=float)
            logy=np.log(vy)
            def residual(params):
                g=float(params[0]);logA=float(params[1])
                h=np.array([phase_hg(a,g) for a in tx])
                return logA+np.log(h)-logy
            fit=least_squares(residual,[0.75,float(np.median(logy))],
                 bounds=([0.001,-60],[0.999,60]),loss='soft_l1',f_scale=.2)
            g,logA=[float(q) for q in fit.x]
            modeled_log=np.array([logA+math.log(phase_hg(a,g)) for a in tx])
            ss=float(np.sum((modeled_log-logy)**2))
            variance=float(np.sum((logy-np.mean(logy))**2))
            result['g']=g
            result['r2_log']=1-ss/variance if variance>1e-12 else None
            result['fit_points']=[(float(a),float(math.exp(logA)*phase_hg(a,g)))
                                  for a in np.linspace(xx[0],xx[-1],150)]
            result['hg_input_rows']=[{'theta_polar_deg':float(a),'intensity_baseline_corrected_V':float(v),
                 'hg_prediction_V':float(math.exp(logA)*phase_hg(a,g)),
                 'residual_log':float(ml-lv)}
                 for a,v,ml,lv in zip(tx,vy,modeled_log,logy)]
            if g<=.005 or g>=.995:
                result['notes'].append('HG g نزدیک کران عددی برازش است.')
            if result['r2_log'] is not None and result['r2_log']<.8:
                result['notes'].append('برازش HG در مقیاس لگاریتمی کیفیت پایینی دارد.')
        except ImportError:
            result['notes'].append('برای برازش HG کتابخانه‌های numpy و scipy لازم‌اند؛ g_paper همچنان محاسبه می‌شود.')
        except Exception as exc:
            result['notes'].append('برازش HG ناموفق بود: '+str(exc))
    else:
        result['notes'].append('g_HG نیاز به حداقل ۶ زاویه یکتا با پهنای حداقل ۲۵° در بازه Forward انتخابی دارد.')

    # MEASURED-ONLY forward/backward angular integrals (NOT calibrated reflectance or mu_s).
    def sector_integral(lo, hi):
        sector = [(t,v) for t,v in pol_points if lo-1e-8 <= t <= hi+1e-8]
        if len(sector) < 2:
            return None, len(sector), None
        area = sum(0.5 * (v1*math.sin(math.radians(t1)) +
                            v2*math.sin(math.radians(t2))) * math.radians(t2-t1)
                   for (t1,v1),(t2,v2) in zip(sector,sector[1:]))
        return 2*math.pi*area, len(sector), (sector[0][0],sector[-1][0])
    fwd, nf, frange = sector_integral(0.0,90.0)
    back, nb, brange = sector_integral(90.0,180.0)
    result.update(forward_integral_proxy_Vsr=fwd,
                  backscatter_integral_proxy_Vsr=back,
                  back_to_forward_proxy=(back/fwd if fwd is not None and fwd>0 and back is not None else None),
                  backward_fraction_measured=(back/(fwd+back) if fwd is not None and back is not None and fwd+back>0 else None),
                  forward_coverage_deg=frange, backward_coverage_deg=brange,
                  forward_n_angles=nf,backward_n_angles=nb)
    backpts=[(a,v) for a,v,n in points if abs(a)>90 and abs(a)<=180]
    if backpts:
        mx=max(backpts,key=lambda z:z[1]);result['back_peak_V']=mx[1];result['back_peak_signed_angle_deg']=mx[0]
    else:
        result['back_peak_V']=None;result['back_peak_signed_angle_deg']=None
    result['notes'].append('F/B بر مبنای انتگرال ولتاژ زمینه‌کسرشده و sin(theta) در بازه واقعی اندازه‌گیری‌شده است؛ با بازه ناقص ±155°، نسبتِ کل نیم‌کره و ضریب بازتابندگی مطلق نیست.')
    result['notes'].append('μs، μa و μs\' از ولتاژ زاویه‌ای به‌تنهایی به دست نمی‌آیند. 2π∫V sinθdθ فقط شاخص سیگنال زاویه‌ای است، نه ضریب پراکندگی cm⁻¹.')
    return result


POINT_FIELDS = [
    'timestamp','source','run','requested_angle_deg','done_estimated_angle_deg',
    'motor_steps','status','samples_requested','samples_acquired', 'sample_period_s',
    'dwell_s','settle_s','sample_times_s','raw_voltages_V','mean_voltage_V',
    'median_voltage_V','intensity_proxy_V','std_voltage_V','standard_error_V','ci95_low_V','ci95_high_V','min_voltage_V','max_voltage_V','outlier_count',
    'mad_sigma_voltage_V','snr_single_sample','dark_noise_sd_V','acquisition_mode',
    'sample_rate_requested_hz','capture_elapsed_s','quality_note',
    'robust_intensity_V','ema_voltage_V','spike_flags','ema_series_V',
    'n_unflagged','ema_alpha','block_standard_error_V','acquisition_attempts'
]



# ============================================================================
# V10: the acquisition thread NEVER writes Excel or touches tkinter.
# Buffered raw readings are appended to crash-recovery journals on disk.
# A write-only XLSX is built AFTER acquisition in a separate worker thread.
# ============================================================================
import shutil
import tempfile
from tkinter import filedialog

RAW_FIELDS = ['point_id', 'timestamp', 'sample_id', 'run', 'theta_requested_deg',
              'theta_estimated_deg', 'index', 't_relative_s', 'raw_V',
              'ema_V', 'spike_flag']
SUMMARY_FIELDS = [
    'point_id','timestamp','sample_id','sample_kind','hb_g_dl','wavelength_nm',
    'run','requested_angle_deg','done_estimated_angle_deg','motor_steps',
    'status','quality_note','samples_requested','samples_acquired',
    'sample_rate_requested_hz','capture_elapsed_s','dwell_s','settle_s',
    'mean_voltage_V','median_voltage_V','intensity_proxy_V','ema_voltage_V',
    'std_voltage_V','mad_sigma_voltage_V','block_standard_error_V',
    'outlier_count','n_unflagged','snr_single_sample','dark_noise_sd_V']


def safe_float(text, label, low=None, high=None):
    try: value=float(text)
    except (ValueError,TypeError): raise ValueError(f'{label}: عدد نامعتبر است')
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f'{label}: خارج از محدوده مجاز')
    return value


def h_mean(vals):
    return statistics.mean(vals) if vals else None


class StreamJournal:
    """One open file per logical stream, fsync at each angle; no Excel during scan."""
    def __init__(self, output_folder, sample_id, meta):
        folder=Path(output_folder).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.stem=f'Goniometer_{stamp}'
        self.folder=folder/(self.stem+'.partial')
        self.folder.mkdir()
        self.target=folder/(self.stem+'.xlsx')
        self.rp=self.folder/'raw.csv'
        self.sp=self.folder/'points.csv'
        self.mp=self.folder/'settings.csv'
        with self.mp.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f);w.writerow(['parameter','value'])
            for k,v in meta.items(): w.writerow([k,v])
        self.rawfile=self.rp.open('w',newline='',encoding='utf-8-sig',buffering=128*1024)
        self.pointfile=self.sp.open('w',newline='',encoding='utf-8-sig',buffering=16384)
        self.rawwriter=csv.DictWriter(self.rawfile,fieldnames=RAW_FIELDS)
        self.pointwriter=csv.DictWriter(self.pointfile,fieldnames=SUMMARY_FIELDS)
        self.rawwriter.writeheader();self.pointwriter.writeheader()
        self.count=0
        self.closed=False

    def commit(self,rec,raw,trace,spikes):
        if self.closed:raise RuntimeError('Journal is closed')
        seq=self.count+1
        rec['point_id']=seq
        rate=float(rec['sample_rate_requested_hz'])
        sample_id=rec['sample_id']
        base={
            'point_id':seq,'timestamp':rec['timestamp'],'sample_id':sample_id,
            'run':rec['run'],'theta_requested_deg':rec['requested_angle_deg'],
            'theta_estimated_deg':rec['done_estimated_angle_deg'],
        }
        for i,v in enumerate(raw):
            self.rawwriter.writerow({**base,'index':i,'t_relative_s':round(i/rate,9),
                   'raw_V':float(v),'ema_V':float(trace[i]),'spike_flag':int(spikes[i])})
        self.pointwriter.writerow({key:rec.get(key,'') for key in SUMMARY_FIELDS})
        for f in (self.rawfile,self.pointfile):
            f.flush()
            os.fsync(f.fileno())
        self.count=seq

    def close(self):
        if not self.closed:
            for f in (self.rawfile,self.pointfile):
                try:f.close()
                except Exception:pass
            self.closed=True


def load_journal(folder):
    folder=Path(folder)
    if not folder.is_dir():raise ValueError('Recovery folder not found')
    with (folder/'settings.csv').open('r',encoding='utf-8-sig',newline='') as f:
        meta=dict(list(csv.reader(f))[1:])
    with (folder/'points.csv').open('r',encoding='utf-8-sig',newline='') as f:
        points=list(csv.DictReader(f))
    if not (folder/'raw.csv').exists():raise ValueError('Raw journal not found')
    return meta,points


def capture_point(daq, settings, done, angle, run, cancel, dark_noise_sd=None):
    """All buffered conversion happens between motor DONE and dwell deadline.
    The returned list of floats is NOT retained in the GUI model.
    """
    arrive=time.monotonic()
    deadline=arrive+settings['dwell']
    n=settings['n'];rate=settings['rate']
    if cancel.wait(settings['settle']):raise InterruptedError('اسکن متوقف شد')
    remaining=deadline-time.monotonic()-0.05
    if remaining < n/rate+0.10:
        raise DAQError('مکث زاویه برای نمونه‌برداری کافی نیست')
    values,elapsed,_=daq.capture_buffered(n,rate,remaining,cancel)
    # Scientifically, the raw stream is preserved; no interpolation or deletion.
    info=filter_voltage_trace(values,settings['alpha'],settings['spike_sigma'])
    N=len(values)
    sd=statistics.stdev(values) if N>1 else 0.0
    med=info['robust_median']
    blocks=max(8,int(rate*0.02))
    means=[statistics.mean(values[i:i+blocks]) for i in range(0,N,blocks) if len(values[i:i+blocks])>=max(4,blocks//2)]
    bse=statistics.stdev(means)/math.sqrt(len(means)) if len(means)>=2 else None
    baseline=settings['baseline']
    # Only a warning. Never replace the measured value with an invented one.
    noise=max(info['mad_sigma'],sd,float(dark_noise_sd or 0),40/16384)
    snr=abs(med-baseline)/noise if noise>0 else float('inf')
    bad_fraction=info['spike_count']/N
    quality='LOW_CONFIDENCE' if (snr<3 or bad_fraction>0.05 or info['insufficient']) else 'OK'
    detail='LOW_SIGNAL' if snr<3 else 'OK_SNR'
    if bad_fraction>0.05 or info['insufficient']: detail+=';TRANSIENTS'
    note=(f'Quality flags={detail}; raw samples retained; spike fraction={bad_fraction:.3f}; '
          'EMA for plot only; block SE diagnostic; acquisition rate requested, not calibrated')
    record=dict(timestamp=datetime.now().isoformat(timespec='milliseconds'),
      source='scan',run=run,requested_angle_deg=angle,
      done_estimated_angle_deg=float(done['degrees']),motor_steps=done['steps'],
      status=quality,quality_note=note,
      samples_requested=n,samples_acquired=N,sample_rate_requested_hz=rate,
      capture_elapsed_s=elapsed,dwell_s=settings['dwell'],settle_s=settings['settle'],
      mean_voltage_V=statistics.mean(values),median_voltage_V=statistics.median(values),
      intensity_proxy_V=med,ema_voltage_V=info['ema_tail_mean'],
      std_voltage_V=sd,mad_sigma_voltage_V=info['mad_sigma'],
      block_standard_error_V=bse,outlier_count=info['spike_count'],
      n_unflagged=info['kept_count'],snr_single_sample=snr,
      dark_noise_sd_V=dark_noise_sd)
    return record,values,info


def style_excel_sheet(ws, widths=None):
    # Called before writing for write-only sheets.
    if widths:
        from openpyxl.utils import get_column_letter
        for ix,w in enumerate(widths,1):ws.column_dimensions[get_column_letter(ix)].width=w
    ws.freeze_panes='A2'


def export_paper_workbook(journal_folder, outfile, status_note='COMPLETE', update=None):
    """STREAMING export: never load Raw_Samples into RAM. Safe atomic rename."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill, Alignment
    meta,pts=load_journal(journal_folder)
    if not pts:raise ValueError('هیچ نقطه کاملی برای خروجی وجود ندارد')
    wb=Workbook(write_only=True)
    def sheet(name,head,widths=None):
        ws=wb.create_sheet(name)
        style_excel_sheet(ws,widths)
        row=[]
        for h in head:
            c=WriteOnlyCell(ws,value=str(h));c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='12345B');c.alignment=Alignment(wrap_text=True)
            row.append(c)
        ws.append(row)
        return ws
    def n(v):
        if v is None or v=='':return None
        try:
            v=float(v)
            return v if math.isfinite(v) else None
        except (ValueError,TypeError):return v

    a=analyze_scattering(pts,n(meta.get('reference_v','16.37')),n(meta.get('thickness_cm','0.014')),
        n(meta.get('fit_max_deg','90')),n(meta.get('fit_min_deg','2')),n(meta.get('baseline_V','0')))
    paper=sheet('Paper_Summary',['Metric','Value','Unit','Method / limitation'],[36,23,18,105])
    kind=str(meta.get('sample_kind','')).strip().casefold()
    ref_only=kind in ('empty','blank','reference','empty cuvette','empty_cuvette','کووت خالی')
    bio_metric=lambda x:None if ref_only else x
    metrics=[
       ('sample_id',meta.get('sample_id'),'','User-provided label'),
       ('sample_kind',meta.get('sample_kind'),'','Empty cuvette != blood'),
       ('hemoglobin',n(meta.get('hb_g_dl')),'g/dL','Measured by OptoHb; enter original value'),
       ('scan_status',status_note,'','COMPLETE only if all requested angles finished'),
       ('wavelength',n(meta.get('wavelength_nm')),'nm','User-entered'),
       ('g_paper',bio_metric(a.get('g_paper_avg')),'dimensionless','Discrete Eq. 6; experimental angular index'),
       ('g_paper_positive',bio_metric(a.get('g_paper_pos')),'dimensionless','Positive sector'),
       ('g_paper_negative',bio_metric(a.get('g_paper_neg')),'dimensionless','Negative sector'),
       ('g_moment_partial',bio_metric(a.get('g_moment_partial')),'dimensionless','Partial polar moment, incomplete solid angle'),
       ('g_HG_fit',bio_metric(a.get('g')) if a.get('r2_log') is not None and a['r2_log']>=0.8 and a.get('g') is not None and 0.005<a['g']<0.995 else None,'dimensionless','Blank means poor fit or boundary value'),
       ('g_HG_fit_candidate',a.get('g'),'dimensionless','Candidate only, DO NOT call validated if poor fit'),
       ('HG_R2_log',a.get('r2_log'),'','Fit residual quality in log space'),
       ('mu_t_apparent',bio_metric(a.get('mu_t_app_cm_inv')),'cm^-1','From log(V_empty/V_blood_max)/thickness, ballistics NOT verified'),
       ('transmittance_apparent',bio_metric(a.get('transmittance_app')),'','max voltage ratio, same gain required'),
       ('optical_depth_apparent',bio_metric(a.get('optical_depth_app')),'','minus log(T_app)'),
       ('back_forward_proxy',bio_metric(a.get('back_to_forward_proxy')),'','Partial angular voltage ratio, NOT calibrated reflectance'),
       ('full_angular_points',len(pts),'','All complete measured points retained, including low SNR'),
       ('DAQ_requested_rate_hz',n(meta.get('rate_hz')),'Hz','SDK requested rate; not independently calibrated'),
       ('DAQ_samples_per_angle',n(meta.get('n_samples')),'','Per angle requested'),
       ('reference_voltage',n(meta.get('reference_v')),'V','User-entered empty-cuvette reference'),
       ('thickness',n(meta.get('thickness_cm')),'cm','User-entered path length'),
       ('mean_dark_noise_sd',n(meta.get('dark_noise_sd_V')),'V','Measured dark SD if available'),
    ]
    for m in metrics:paper.append([n(m[0]),n(m[1]),n(m[2]),m[3]])
    if ref_only:paper.append(['REFERENCE-ONLY',None,None,'Empty cuvette: computed curve diagnostics are NOT blood optical properties.'])
    for msg in a.get('notes',[]):paper.append(['NOTE',None,None,str(msg)])
    angle=sheet('Intensity_by_Angle',SUMMARY_FIELDS,[11,26,16,17,13,14,13,12,16,16,13,24]+[17]*30)
    for p in pts:angle.append([n(p.get(k)) for k in SUMMARY_FIELDS])
    pdata=sheet('g_Paper_Eq6',['side','theta_deg','intensity_V','cos_theta','I_cos_V'],[18,16,20,20,20])
    for r in a.get('paper_rows',[]):pdata.append([r[k] for k in ['side','theta_deg','intensity_V','cos_theta','I_cos_V']])
    mm=sheet('g_Moment',['theta_polar_deg','I_minus_baseline_V','sin_theta','cos_theta','I_sin_V','I_cos_sin_V','segment_den','segment_num'],[21]*8)
    for r in a.get('moment_rows',[]):mm.append([r[k] for k in ['theta_polar_deg','intensity_minus_baseline_V','sin_theta','cos_theta','I_sin_V','I_cos_sin_V','segment_den_Vrad','segment_num_Vrad']])
    hh=sheet('g_HG_Fit',['theta_deg','I_corrected_V','prediction_V','log_residual'],[20]*4)
    for r in a.get('hg_input_rows',[]):hh.append([r[k] for k in ['theta_polar_deg','intensity_baseline_corrected_V','hg_prediction_V','residual_log']])
    fig=sheet('Figure_Data',['theta_signed_deg','intensity_V','n_repeats','quality_note'],[24,20,18,85])
    for p in a.get('signed_angle_rows',[]):fig.append([p['theta_signed_deg'],p['intensity_used_V'],p['n_repeats'],p['source']])
    condition=sheet('Conditions',['parameter','value'],[40,110])
    for k,v in meta.items():condition.append([k,n(v)])
    condition.append(['scan_status',status_note])
    condition.append(['motor_angle_type','Commanded step-based estimate, NO encoder'])
    condition.append(['DAQ_conversion','WaveformAI buffered sampling, requested clock only'])
    condition.append(['filtering','Raw values preserved. EMA within angle only for display; g from robust medians'])
    condition.append(['uncertainty','SD of raw and block SE reported; no artificial smoothing of error bars'])

    row_head=['point_id','timestamp','sample_id','run','theta_requested_deg','theta_estimated_deg','index','t_relative_s','raw_V','ema_V','spike_flag']
    part=0;row_n=0;raw_ws=None
    with (Path(journal_folder)/'raw.csv').open('r',newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        for row in reader:
            if raw_ws is None or row_n>=750000:
                part+=1;raw_ws=sheet('Raw_Samples' if part==1 else f'Raw_Samples_{part}',row_head,[15,25,16,11,20,20,12,18,20,20,15]);row_n=0
            raw_ws.append([n(row.get(k)) for k in row_head]);row_n+=1
            if update and row_n%30000==0:update(f'Excel: نوشتن داده خام {part} / {row_n:,} سطر')
    dst=Path(outfile)
    dst.parent.mkdir(parents=True,exist_ok=True)
    temp=dst.with_name(dst.stem+'.writing.xlsx')
    try:
        wb.save(str(temp))
        os.replace(temp,dst)
    finally:
        if temp.exists():temp.unlink(missing_ok=True)
    return dst,a


class GoniometerStudio:
    BG='#F3F7FC';FG='#172C49';MUTED='#59738D';PANEL='#FFFFFF';BLUE='#236AB2';TEAL='#008C89';RED='#AE3145'
    def __init__(self):
        self.root=tk.Tk();self.root.title('Goniometer Studio V10 | Responsive Acquisition')
        self.root.geometry('1380x830');self.root.minsize(1030,640);self.root.configure(bg=self.BG)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.q=queue.Queue();self.cancel=threading.Event();self.live_pause=threading.Event()
        self.motor=None;self.daq=None;self.busy=False;self.is_scan=False
        self._closing=False;self.journal=None;self.rows=[];self.journal_folder=None
        self.last_report=None;self.angle=0.;self.target=0.;self.is_zero=False
        self.dark_sd=None;self._live_ema=None
        self._polling=False
        self.fields={}
        default={
          'port':'COM8','profile':DAQ_DEFAULT_PROFILE,'start':'90','end':'-90',
          'step':'5','loops':'1','settle':'0.30','dwell':'3.00',
          'rate':'1000','n':'500','alpha':'0.20','spike_sigma':'6',
          'jog':'1','go':'0','speed':'350','accel':'400',
          'sample_id':'blood_01','sample_kind':'blood','hb':'',
          'wavelength':'532','reference':'16.37','thickness':'0.014',
          'baseline':'0','fit_min':'2','fit_max':'90',
          'folder':str(Path.home()/'Documents'/'Goniometer_Records')}
        self.s={k:tk.StringVar(value=v) for k,v in default.items()}
        self.visual_smooth=tk.BooleanVar(value=True)
        self.motor_var=tk.StringVar(value='Motor disconnected')
        self.daq_var=tk.StringVar(value='DAQ disconnected')
        self.voltage_var=tk.StringVar(value='— V')
        self.angle_var=tk.StringVar(value='Angle: —')
        self.job_var=tk.StringVar(value='Ready')
        self.point_var=tk.StringVar(value='0 points')
        self._make_ui()
        self.root.after(80,self._drain)
        self.root.after(1200,self._poll_motor_status)

    def _label(self,where,s,bold=False,color=None,size=10):
        return tk.Label(where,text=s,bg=self.PANEL,fg=color or self.FG,
             font=('Segoe UI',size,'bold' if bold else 'normal'))
    def _panel(self,where,title):
        fr=tk.Frame(where,bg=self.PANEL,bd=0,highlightthickness=1,highlightbackground='#DCE6EF',padx=12,pady=10)
        fr.pack(fill='x',pady=5)
        self._label(fr,title,True,size=12).pack(anchor='e',pady=(0,7))
        return fr
    def _line(self,where,items):
        row=tk.Frame(where,bg=self.PANEL);row.pack(fill='x',pady=3)
        for label,key,width in items:
            fr=tk.Frame(row,bg=self.PANEL);fr.pack(side='right',padx=4,fill='x',expand=True)
            self._label(fr,label,size=9,color=self.MUTED).pack(anchor='e')
            e=tk.Entry(fr,textvariable=self.s[key],font=('Segoe UI',10),width=width,justify='center',relief='solid',bd=1)
            e.pack(fill='x',ipady=3)
            self.fields[key]=e
        return row
    def _button(self,where,label,fn,bg=None):
        return tk.Button(where,text=label,command=fn,relief='flat',bd=0,cursor='hand2',
                   font=('Segoe UI',10,'bold'),bg=bg or '#E8F3F8',fg='white' if bg else self.FG,padx=10,pady=8)
    def _buttonline(self,where,buttons):
        fr=tk.Frame(where,bg=self.PANEL);fr.pack(fill='x',pady=3)
        for text_,fn,bg in buttons:
            self._button(fr,text_,fn,bg).pack(side='right',padx=3)
        return fr
    def _make_ui(self):
        title=tk.Frame(self.root,bg=self.BG,pady=13);title.pack(fill='x',padx=13)
        tk.Label(title,text='GONIOMETER STUDIO   /   V10',bg=self.BG,fg=self.FG,font=('Segoe UI',20,'bold')).pack(side='left')
        tk.Label(title,text='Buffered DAQ • Motor • Paper Excel',bg=self.BG,fg=self.TEAL,font=('Segoe UI',10)).pack(side='right')
        statuses=tk.Frame(self.root,bg=self.BG);statuses.pack(fill='x',padx=13)
        for var in [self.motor_var,self.daq_var,self.angle_var,self.voltage_var]:
            tk.Label(statuses,textvariable=var,bg='white',fg=self.BLUE,
                     font=('Segoe UI',11,'bold'),padx=12,pady=10).pack(side='left',fill='x',expand=True,padx=3)
        main=tk.PanedWindow(self.root,orient='horizontal',sashwidth=8,bg=self.BG,bd=0)
        main.pack(fill='both',expand=True,padx=14,pady=(12,8))
        lbase=tk.Frame(main,bg=self.BG);rbase=tk.Frame(main,bg=self.BG)
        main.add(lbase,minsize=410,width=470);main.add(rbase,minsize=570)
        self.left_scroll=tk.Canvas(lbase,bg=self.BG,highlightthickness=0)
        sb=tk.Scrollbar(lbase,orient='vertical',command=self.left_scroll.yview)
        self.left_scroll.configure(yscrollcommand=sb.set)
        sb.pack(side='right',fill='y');self.left_scroll.pack(side='left',fill='both',expand=True)
        inside=tk.Frame(self.left_scroll,bg=self.BG)
        win=self.left_scroll.create_window((0,0),window=inside,anchor='nw')
        inside.bind('<Configure>',lambda ev:self.left_scroll.configure(scrollregion=self.left_scroll.bbox('all')))
        self.left_scroll.bind('<Configure>',lambda ev:self.left_scroll.itemconfigure(win,width=ev.width))
        p=self._panel(inside,'۱. اتصال دستگاه‌ها')
        self._line(p,[('پورت Arduino','port',9)])
        self._buttonline(p,[('اتصال / قطع موتور',self.toggle_motor,self.BLUE)])
        self._line(p,[('مسیر پروفایل USB-4704','profile',38)])
        self._buttonline(p,[('انتخاب XML',self.browse_profile,None),('اتصال / قطع DAQ',self.toggle_daq,self.BLUE)])
        p=self._panel(inside,'۲. کنترل موتور')
        self._line(p,[('حرکت دستی °','jog',8),('زاویه مقصد °','go',9)])
        self._buttonline(p,[('عقب',lambda:self.move_manual(-1),self.BLUE),('جلو',lambda:self.move_manual(1),self.BLUE),('برو به زاویه',self.move_target,self.TEAL)])
        self._buttonline(p,[('این موقعیت = صفر',self.zero_motor,self.TEAL),('STOP MOTOR',self.stop,self.RED)])
        self._line(p,[('سرعت step/s','speed',8),('شتاب','accel',8)])
        self._buttonline(p,[('اعمال سرعت/شتاب',self.apply_motion,None)])
        p=self._panel(inside,'۳. اسکن زاویه‌ای + DAQ')
        self._line(p,[('شروع °','start',8),('پایان °','end',8),('گام °','step',7),('تکرار','loops',6)])
        self._line(p,[('نرخ Hz','rate',8),('نمونه/زاویه','n',8)])
        self._line(p,[('مکث کل (s)','dwell',7),('پایدارسازی (s)','settle',7)])
        self._line(p,[('فیلتر EMA α','alpha',7),('آستانه پرت MAD','spike_sigma',8)])
        cb=tk.Checkbutton(p,text='نمایش منحنی هموارشده (فقط نمایشی)',variable=self.visual_smooth,
            command=self._draw_chart,bg=self.PANEL,fg=self.FG,anchor='e')
        cb.pack(anchor='e')
        self._buttonline(p,[('تست Buffered (بدون حرکت)',self.test_buffered,self.BLUE),('Dark test',self.test_dark,None)])
        self._buttonline(p,[('شروع اسکن و ثبت',self.start_scan,self.TEAL),('توقف اسکن',self.stop,self.RED)])
        p=self._panel(inside,'۴. مشخصات مقاله / نمونه')
        self._line(p,[('شناسه نمونه','sample_id',14),('نوع (blood/empty)','sample_kind',11)])
        self._line(p,[('Hb (g/dL)','hb',9),('طول موج nm','wavelength',9)])
        self._line(p,[('مرجع خالی V','reference',10),('ضخامت cm','thickness',10)])
        self._line(p,[('Dark Offset V','baseline',9),('حداقل HG °','fit_min',9),('حداکثر HG °','fit_max',9)])
        self._line(p,[('پوشه خروجی','folder',40)])
        self._buttonline(p,[('تبدیل آخرین ثبت موقت به Excel',self.recover_excel,None),('ساخت مجدد Excel',self.rebuild_excel,self.BLUE)])
        # Right: light, low-cost canvas instead of a Matplotlib re-render per angle.
        pr=self._panel(rbase,'موقعیت بازو (تخمینی از گام‌ها، بدون انکودر)')
        self.arm_canvas=tk.Canvas(pr,bg='#FCFEFF',height=180,highlightthickness=0)
        self.arm_canvas.pack(fill='x',expand=True)
        self.arm_canvas.bind('<Configure>',lambda ev:self._draw_arm())
        chart=self._panel(rbase,'شدت ثبت‌شده برحسب زاویه   |   V(θ)')
        self.plot=tk.Canvas(chart,bg='#FCFEFF',height=340,highlightthickness=0)
        self.plot.pack(fill='both',expand=True)
        self.plot.bind('<Configure>',lambda ev:self._draw_chart())
        self._label(chart,'خط پیوسته: شدت اندازه‌گیری‌شده | خط نقطه‌چین: EMA برای نمایش | هیچ نقطه‌ای حدس زده نمی‌شود',size=9,color=self.MUTED).pack(anchor='e')
        p=self._panel(rbase,'وضعیت و گزارش')
        tk.Label(p,textvariable=self.job_var,bg=self.PANEL,fg=self.TEAL,font=('Segoe UI',11,'bold')).pack(anchor='e')
        tk.Label(p,textvariable=self.point_var,bg=self.PANEL,fg=self.FG).pack(anchor='e')
        self.logbox=tk.Text(p,height=7,bg='#F9FCFF',fg=self.FG,wrap='word',font=('Consolas',9),relief='flat')
        self.logbox.pack(fill='both',expand=True)
        self._draw_arm();self._draw_chart()
        self._log('READY: DAQ/Mega اتصال واقعی را هنوز باید روی همین نسخه تأیید کنی.')

    def _log(self,s):
        self.logbox.insert('end',datetime.now().strftime('%H:%M:%S')+' | '+str(s)+'\n')
        self.logbox.see('end')
        if int(self.logbox.index('end-1c').split('.')[0])>250:self.logbox.delete('1.0','60.0')
    def _send(self,*m):self.q.put(m)
    def _run_task(self,name,fn,on_ok=None):
        if self.busy:
            self._log('عملیات دیگری در حال اجرا است');return
        self.busy=True;self.job_var.set(name)
        def task():
            try:self._send('task_ok',name,fn(),on_ok)
            except BaseException as exc:self._send('task_error',name,str(exc))
        threading.Thread(target=task,daemon=True).start()
    def _drain(self):
        if self._closing:return
        # Strict budget: never starve Tk event loop when worker generates bursts.
        start=time.monotonic();processed=0
        while processed<20 and time.monotonic()-start<0.012:
            try:m=self.q.get_nowait()
            except queue.Empty:break
            processed+=1
            op=m[0]
            if op=='task_ok':
                _,name,result,cb=m;self.busy=False;self.job_var.set('Finished: '+name)
                if cb:
                    try:cb(result)
                    except Exception as e:self._log('GUI update error: '+str(e))
            elif op=='task_error':
                self.busy=False;self.is_scan=False;self.live_pause.clear()
                self.job_var.set('ERROR: '+m[1]);self._log(m[2]);messagebox.showerror('Goniometer error',m[2])
            elif op=='log':self._log(m[1])
            elif op=='point':
                self.rows.append(m[1]);self.point_var.set(f'{len(self.rows)} saved points | {m[1]["status"]}')
                self.voltage_var.set(f'{m[1]["intensity_proxy_V"]:+.6f} V')
                self._draw_chart()
            elif op=='progress':self.job_var.set(m[1])
            elif op=='angle':self.angle=m[1];self.angle_var.set(f'Angle (estimated): {self.angle:+.3f}°');self._draw_arm()
            elif op=='target':self.target=m[1];self._draw_arm()
            elif op=='live':self.voltage_var.set(f'{m[1]:+.6f} V')
        self.root.after(80,self._drain)
    def _draw_arm(self):
        if not hasattr(self,'arm_canvas'):return
        c=self.arm_canvas;c.delete('all');w=max(380,c.winfo_width());h=max(180,c.winfo_height())
        cx=w*.56;cy=h*.53;r=min(65,h*.34)
        c.create_oval(cx-r,cy-r,cx+r,cy+r,outline='#9DBBD5',width=2)
        c.create_line(cx-r-12,cy,cx+r+30,cy,dash=(4,5),fill='#9CB9CD')
        for a in range(-180,180,30):
            x=cx+r*math.cos(math.radians(a));y=cy-r*math.sin(math.radians(a))
            c.create_oval(x-2,y-2,x+2,y+2,fill='#9CB9CD',outline='')
        for a,color,wd in [(self.target,'#CB8B3D',3),(self.angle,self.BLUE,5)]:
            t=math.radians(a)
            c.create_line(cx,cy,cx+r*math.cos(t),cy-r*math.sin(t),fill=color,width=wd,arrow=tk.LAST)
        c.create_text(cx,cy+r+21,text=f'{self.angle:+.2f}° estimated  |  target {self.target:+.2f}°',fill=self.FG,font=('Segoe UI',10))
    def _draw_chart(self):
        if not hasattr(self,'plot'):return
        c=self.plot;c.delete('all');w=max(450,c.winfo_width());h=max(260,c.winfo_height())
        left,top,right,bottom=63,26,w-22,h-36
        c.create_rectangle(left,top,right,bottom,outline='#B4C7D8')
        if not self.rows:
            c.create_text(w/2,h/2,text='Waiting for measurements',fill=self.MUTED,font=('Segoe UI',12));return
        ordered=sorted((float(r['done_estimated_angle_deg']),float(r['intensity_proxy_V']),float(r['std_voltage_V'])) for r in self.rows)
        xmin=min(x for x,_,_ in ordered);xmax=max(x for x,_,_ in ordered)
        ymin=min(y for _,y,_ in ordered);ymax=max(y for _,y,_ in ordered)
        if xmax-xmin<1e-6:xmin-=1;xmax+=1
        if ymax-ymin<1e-8:ymin-=.005;ymax+=.005
        pad=.1*(ymax-ymin);ymin-=pad;ymax+=pad
        for j in range(5):
            yy=top+(bottom-top)*j/4
            c.create_line(left,yy,right,yy,fill='#EBEFF5')
            val=ymax-j*(ymax-ymin)/4
            c.create_text(left-6,yy,anchor='e',text=f'{val:.3g}',fill=self.MUTED,font=('Segoe UI',8))
        def xy(t,v):return (left+(t-xmin)/(xmax-xmin)*(right-left),bottom-(v-ymin)/(ymax-ymin)*(bottom-top))
        # Raw robust points: plot is not a transformation of stored values.
        ps=[xy(t,v) for t,v,_ in ordered]
        if len(ps)>=2:c.create_line(*[e for p in ps for e in p],fill=self.TEAL,width=2)
        for xx,yy in ps:c.create_oval(xx-3,yy-3,xx+3,yy+3,fill=self.TEAL,outline='white')
        if self.visual_smooth.get() and len(ordered)>=4:
            alpha=float(self.s['alpha'].get() or 0.2)
            ys=symmetric_display_ema([v for _,v,_ in ordered],min(1,max(.01,alpha)))
            points=[xy(ordered[i][0],ys[i]) for i in range(len(ordered))]
            c.create_line(*[e for p in points for e in p],fill=self.BLUE,dash=(5,3),width=2)
        c.create_text((left+right)/2,h-12,text='Signed angle (degree)',fill=self.MUTED)
        c.create_text(14,14,text='V',fill=self.MUTED)
    def browse_profile(self):
        p=filedialog.askopenfilename(title='DAQ profile',filetypes=[('XML','*.xml')]);
        if p:self.s['profile'].set(p)
    def toggle_motor(self):
        if self.busy:return
        if self.motor:
            self.motor.close();self.motor=None;self.motor_var.set('Motor disconnected');return
        port=self.s['port'].get().strip()
        self._run_task('Connect Mega',lambda:MegaMotor(port,lambda x:self._send('log',x)),
            lambda m:(setattr(self,'motor',m),self.motor_var.set('Motor connected')))
    def toggle_daq(self):
        if self.busy:return
        if self.daq:
            d=self.daq;self.daq=None;self.live_pause.set()
            self._run_task('Disconnect DAQ',d.close,lambda _:self.daq_var.set('DAQ disconnected'))
            return
        path=self.s['profile'].get().strip()
        self._run_task('Connect USB-4704',lambda:USB4704(path),self._daq_ready)
    def _daq_ready(self,d):
        self.daq=d;self.daq_var.set('DAQ connected');self.live_pause.clear()
        threading.Thread(target=self._live_worker,daemon=True).start()
    def _live_worker(self):
        while self.daq is not None and not self._closing:
            if self.live_pause.is_set() or self.daq.mode!='instant':
                time.sleep(.15);continue
            try:
                v=self.daq.read();self._send('live',v)
            except Exception as e:
                self._send('log','DAQ live error: '+str(e));time.sleep(.5)
            time.sleep(.35)
    def _poll_motor_status(self):
        if self._closing:return
        if self.motor and not self.busy and not self._polling:
            self._polling=True
            def poll():
                try:
                    s=self.motor.status();self._send('angle',s['degrees'])
                except Exception:pass
                finally:self._polling=False
            threading.Thread(target=poll,daemon=True).start()
        self.root.after(1200,self._poll_motor_status)
    def _require_motor(self,zero=False):
        if not self.motor:raise MotorError('ابتدا موتور را وصل کن')
        s=self.motor.status()
        if zero and not s['zeroed']:raise MotorError('اسکن مطلق به صفر معتبر نیاز دارد')
        return s
    def zero_motor(self):
        if self.busy:return
        self._run_task('ZERO',lambda:(self._require_motor(),self.motor.zero(),self.motor.status()),
            lambda x:(setattr(self,'is_zero',True),self._send('angle',x[2]['degrees'])))
    def move_manual(self,sign):
        if self.busy:return
        try:d=safe_float(self.s['jog'].get(),'Jog',0.001,180.)
        except Exception as e:messagebox.showerror('Jog',str(e));return
        self._run_task('Jog',lambda:(self._require_motor(),self.motor.jog(sign*d)),
            lambda x:self._send('angle',x[1]['degrees']))
    def move_target(self):
        if self.busy:return
        try:a=safe_float(self.s['go'].get(),'Go angle',-180,180)
        except Exception as e:messagebox.showerror('Go',str(e));return
        self._send('target',a)
        self._run_task('MOVE',lambda:(self._require_motor(True),self.motor.move(a)),
            lambda x:self._send('angle',x[1]['degrees']))
    def apply_motion(self):
        if self.busy:return
        try:sp=safe_float(self.s['speed'].get(),'Speed',10,5000);acc=safe_float(self.s['accel'].get(),'Accel',10,8000)
        except Exception as e:messagebox.showerror('Settings',str(e));return
        self._run_task('Motor settings',lambda:(self._require_motor(),self.motor.set_speed(sp),self.motor.set_accel(acc)))
    def stop(self):
        self.cancel.set();self.job_var.set('STOP requested')
        if self.motor:
            threading.Thread(target=lambda:self._safe_stop(),daemon=True).start()
    def _safe_stop(self):
        try:self.motor.stop()
        except Exception as e:self._send('log','STOP motor: '+str(e))
    def _config(self):
        s=self.s
        n=safe_float(s['n'].get(),'Samples',10,50000)
        if int(n)!=n:raise ValueError('Samples must be integer')
        rate=safe_float(s['rate'].get(),'Rate',100,48000)
        dwell=safe_float(s['dwell'].get(),'Dwell',.3,120)
        settle=safe_float(s['settle'].get(),'Settle',0,120)
        alpha=safe_float(s['alpha'].get(),'EMA alpha',0.001,1)
        spike=safe_float(s['spike_sigma'].get(),'Spike threshold',3,20)
        if settle+int(n)/rate+.65>dwell:
            raise ValueError('Dwell must be >= settle + samples/rate + 0.65s margin')
        return dict(n=int(n),rate=rate,dwell=dwell,settle=settle,alpha=alpha,spike_sigma=spike,
                    baseline=safe_float(s['baseline'].get(),'Baseline',-20,20))
    def _meta(self,cfg):
        vals={k:v.get().strip() for k,v in self.s.items()}
        return dict(sample_id=vals['sample_id'],sample_kind=vals['sample_kind'],hb_g_dl=vals['hb'],
          wavelength_nm=vals['wavelength'],reference_v=vals['reference'],thickness_cm=vals['thickness'],
          baseline_V=vals['baseline'],fit_min_deg=vals['fit_min'],fit_max_deg=vals['fit_max'],
          profile_path=vals['profile'],port=vals['port'],rate_hz=cfg['rate'],
          n_samples=cfg['n'],settle_s=cfg['settle'],dwell_s=cfg['dwell'],
          ema_alpha=cfg['alpha'],spike_sigma=cfg['spike_sigma'],
          dark_noise_sd_V=self.dark_sd or '',firmware_angle_note='Step-based estimate only')
    def _full_config(self):
        if not self.motor or not self.daq:raise ValueError('Motor + DAQ must be connected')
        cfg=self._config()
        a=safe_float(self.s['start'].get(),'Start',-180,180)
        b=safe_float(self.s['end'].get(),'End',-180,180)
        step=safe_float(self.s['step'].get(),'Step',.01,180)
        l=safe_float(self.s['loops'].get(),'Repetitions',1,40)
        if int(l)!=l:raise ValueError('Loops must be integer')
        angles=scan_angles(a,b,step)
        if len(angles)>1000:raise ValueError('Too many angles')
        if not self.s['sample_id'].get().strip():raise ValueError('Sample ID required')
        if not self.s['sample_kind'].get().strip():raise ValueError('Sample kind required')
        safe_float(self.s['reference'].get(),'Reference V',0.001,20)
        safe_float(self.s['thickness'].get(),'Thickness cm',0.000001,100)
        safe_float(self.s['fit_min'].get(),'fit min',0,180)
        safe_float(self.s['fit_max'].get(),'fit max',0,180)
        # Motor I/O happens in the worker, never in the Tk main thread.
        # Precheck storage to avoid crashing on a very large scan.
        expected=len(angles)*int(l)*cfg['n']
        outdir=Path(self.s['folder'].get());outdir.mkdir(parents=True,exist_ok=True)
        needed=max(20_000_000,expected*100)
        if shutil.disk_usage(outdir).free<needed:
            raise OSError('فضای کافی برای Journal و Excel باقی نمانده')
        return cfg,angles,int(l),self._meta(cfg),outdir,expected
    def test_buffered(self):
        if not self.daq:
            messagebox.showerror('DAQ','DAQ متصل نیست');return
        try:cfg=self._config()
        except Exception as e:messagebox.showerror('DAQ',str(e));return
        self.cancel.clear();self.live_pause.set()
        def test():
            try:
                self.daq.begin_buffered()
                vals,elapsed,_=self.daq.capture_buffered(cfg['n'],cfg['rate'],cfg['n']/cfg['rate']+2.,self.cancel)
                return len(vals),statistics.mean(vals),statistics.stdev(vals),elapsed
            finally:
                try:self.daq.end_buffered()
                finally:self.live_pause.clear()
        self._run_task('Buffered test',test,lambda r:messagebox.showinfo('Buffered test',
           f'Samples: {r[0]}\nRate requested: {cfg["rate"]:.0f} Hz\nMean: {r[1]:.6f} V\nRaw SD: {r[2]:.6f} V\nDriver elapsed: {r[3]:.3f} s\nHardware clock frequency not independently calibrated'))
    def test_dark(self):
        if not self.daq:
            messagebox.showerror('DAQ','DAQ متصل نیست');return
        if not messagebox.askyesno('Dark','نور کاملاً مسدود است؟'):return
        try:cfg=self._config()
        except Exception as e:messagebox.showerror('DAQ',str(e));return
        self.cancel.clear();self.live_pause.set()
        def test():
            try:
                self.daq.begin_buffered()
                vals,_,_=self.daq.capture_buffered(cfg['n'],cfg['rate'],cfg['n']/cfg['rate']+2.,self.cancel)
                return statistics.mean(vals),statistics.stdev(vals)
            finally:
                try:self.daq.end_buffered()
                finally:self.live_pause.clear()
        def done(r):
            self.dark_sd=r[1];self._log(f'Dark mean {r[0]:.6f}V; measured SD={r[1]:.6f}V')
        self._run_task('Measure dark SD',test,done)
    def start_scan(self):
        if self.busy:return
        try:cfg,angles,loops,meta,outdir,expected=self._full_config()
        except Exception as exc:
            messagebox.showerror('Invalid scan',str(exc));return
        self.cancel.clear();self.live_pause.set();self.is_scan=True
        self.rows=[];self._draw_chart();self.point_var.set('0 points')
        self._log(f'SCAN: {len(angles)} angles × {loops} runs; {cfg["n"]} samples/angle at requested {cfg["rate"]:.0f}Hz; {expected:,} samples total')
        self._run_task('Scan',lambda:self._scan_worker(cfg,angles,loops,meta,outdir),self._scan_done)
    def _scan_worker(self,cfg,angles,loops,meta,outdir):
        journal=None;count=0;status='INCOMPLETE';problem=None
        try:
            motor=self.motor;daq=self.daq
            ms=motor.status()
            if not ms['zeroed']:raise MotorError('اسکن نیاز به صفر معتبر دارد')
            if min(angles)<ms['min'] or max(angles)>ms['max']:
                raise MotorError('زاویه اسکن خارج از محدوده Firmware است')
            journal=StreamJournal(outdir,meta['sample_id'],meta)
            self.journal_folder=journal.folder
            daq.begin_buffered()
            # Use same count for preflight. This does not move the arm.
            daq.capture_buffered(cfg['n'],cfg['rate'],cfg['n']/cfg['rate']+2,self.cancel)
            self._send('log','Buffered preflight OK; starting motion')
            for run in range(1,loops+1):
                for a in angles:
                    if self.cancel.is_set():raise InterruptedError('User STOP')
                    self._send('target',a)
                    done=motor.move(a)  # DONE = pulses finished, not physical encoder
                    self._send('angle',float(done['degrees']))
                    rec,raw,info=capture_point(daq,cfg,done,a,run,self.cancel,self.dark_sd)
                    rec['sample_id']=meta['sample_id'];rec['sample_kind']=meta['sample_kind']
                    rec['hb_g_dl']=meta['hb_g_dl'];rec['wavelength_nm']=meta['wavelength_nm']
                    journal.commit(rec,raw,info['ema_trace'],info['spikes'])
                    count+=1
                    # lightweight summary only; the long raw arrays are not posted to Tk.
                    self._send('point',rec)
                    self._send('progress',f'Scanning {count}/{len(angles)*loops} | {a:+.2f}° | requested {cfg["rate"]:.0f} Hz')
                    # Dwell begins at DONE. Capturing is bounded by dwell.
                    if self.cancel.wait(max(0,cfg['dwell']-cfg['settle']-rec['capture_elapsed_s'])):
                        raise InterruptedError('User STOP')
            status='COMPLETE'
        except BaseException as exc:
            problem=repr(exc)
            self._send('log','Scan interruption: '+problem)
        finally:
            if journal is not None:journal.close()
            try:self.daq.end_buffered()
            except BaseException as e:self._send('log','Restore instant failed: '+str(e))
            self.live_pause.clear()
        if not journal or journal.count<1:
            raise RuntimeError('Scan stored zero complete angles; '+str(problem))
        self._send('progress','Building Excel in worker thread (UI stays responsive)')
        try:
            path,_=export_paper_workbook(journal.folder,journal.target,status,lambda s:self._send('progress',s))
            # After reliable atomic save, temp journal may be cleaned up.
            shutil.rmtree(journal.folder)
            self.journal_folder=None
            return dict(saved=str(path),status=status,count=count,problem=problem)
        except BaseException as e:
            raise RuntimeError('Excel export failed. Recovery journal: '+str(journal.folder)+' | '+str(e))
    def _scan_done(self,r):
        self.is_scan=False;self.last_report=r['saved']
        self._log(f'Excel saved: {r["saved"]} | {r["status"]} | {r["count"]} points')
        self.job_var.set('Scan '+r['status'])
        if r['problem']:
            self._log('WARNING: Incomplete scan. Partial Excel saved with explicit status. '+str(r['problem']))
        else:
            self._log('COMPLETE: The Excel was saved atomically with all acquired angles.')
    def recover_excel(self):
        folder=filedialog.askdirectory(title='Choose .partial folder')
        if not folder:return
        ff=Path(folder)
        dst=ff.parent/(ff.name.replace('.partial','')+'.xlsx')
        self._run_task('Recover Excel',lambda:export_paper_workbook(ff,dst,'RECOVERED_INCOMPLETE')[0],
            lambda p:messagebox.showinfo('Recovered',str(p)))
    def rebuild_excel(self):
        if not self.journal_folder:
            messagebox.showinfo('Excel','برای بازسازی، «تبدیل آخرین ثبت موقت» را انتخاب کن.');return
        self.recover_excel()
    def close(self):
        if self.busy and not messagebox.askyesno('Exit','اسکن یا خروجی‌گیری در حال انجام است. STOP بزن و سپس خارج شو. الان خارج می‌شوی؟'):
            return
        self._closing=True;self.cancel.set()
        def finish():
            try:
                if self.motor:self.motor.stop();self.motor.close()
            except Exception:pass
            try:
                if self.daq:self.daq.close()
            except Exception:pass
        threading.Thread(target=finish,daemon=True).start()
        self.root.destroy()
    def run(self):self.root.mainloop()


if __name__=='__main__':
    import argparse
    cli=argparse.ArgumentParser(description='Responsive Goniometer Studio')
    cli.add_argument('--self-test',action='store_true',help='Run synthetic data and Excel test only. NO hardware.')
    args,_=cli.parse_known_args()
    if args.self_test:
        import random
        from random import Random
        rng=Random(27)
        temp=Path(tempfile.mkdtemp(prefix='gonio_v10_test_'))
        meta=dict(sample_id='SIMULATED_NOT_REAL',sample_kind='synthetic',hb_g_dl='',
            wavelength_nm=532,reference_v=16.37,thickness_cm=.014,baseline_V=0,
            fit_min_deg=2,fit_max_deg=90,rate_hz=1000,n_samples=200)
        j=StreamJournal(temp,'SIMULATED_NOT_REAL',meta)
        for i,angle in enumerate(range(-90,91,10)):
            g=.75;v=.4*phase_hg(abs(angle),g)+.01
            raw=[v+rng.gauss(0,.006) for _ in range(200)]
            f=filter_voltage_trace(raw)
            rec={'timestamp':datetime.now().isoformat(),'sample_id':'SIMULATED_NOT_REAL',
                'sample_kind':'synthetic','hb_g_dl':'','wavelength_nm':532,
                'run':1,'requested_angle_deg':angle,'done_estimated_angle_deg':angle,
                'motor_steps':i*10,'status':'OK','quality_note':'SYNTHETIC ONLY',
                'samples_requested':200,'samples_acquired':200,'sample_rate_requested_hz':1000,
                'capture_elapsed_s':.2,'dwell_s':3,'settle_s':.3,'mean_voltage_V':statistics.mean(raw),
                'median_voltage_V':statistics.median(raw),'intensity_proxy_V':f['robust_median'],
                'ema_voltage_V':f['ema_tail_mean'],'std_voltage_V':statistics.stdev(raw),
                'mad_sigma_voltage_V':f['mad_sigma'],'block_standard_error_V':.001,
                'outlier_count':f['spike_count'],'n_unflagged':f['kept_count'],
                'snr_single_sample':10,'dark_noise_sd_V':.001}
            j.commit(rec,raw,f['ema_trace'],f['spikes'])
        j.close();dest,a=export_paper_workbook(j.folder,j.target,'SIMULATED_NOT_REAL')
        print('SIMULATION PASS',dest,'g_paper=',a['g_paper_avg'],'g_hg=',a['g'])
    else:GoniometerStudio().run()

# Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠
