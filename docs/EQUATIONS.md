# Mathematical definitions and implementation audit (V10)

The following equations correspond to the supplied `Goniometer_Lab_V10_Stable.py` **as implemented**, not to a fully calibrated measurement of the intrinsic optical coefficients. Angles supplied in degrees are converted to radians for trigonometric functions and numerical integration. **Voltage is a detector signal proxy** unless the angular response and measurement chain are calibrated.

## 1. Step count and angular coordinate

For motor steps $n$, effective step count per motor revolution $N$, and mechanical motor-to-goniometer angular ratio $r=\theta_{\rm gonio}/\theta_{\rm motor}$:

$$\theta_{\rm gonio}=r\,\frac{360^\circ n}{N}. $$

The included firmware assumes $r=1$ and uses a configurable 1600 steps/revolution default. With that assumption, the nominal command resolution is $360^\circ/1600=0.225^\circ$ per pulse. Real angular accuracy also depends on microstepping, backlash, missed steps, calibration and geometry. `DONE` is not an encoder position confirmation.

## 2. Angular signal and statistics

Let $V_{ij}$ be the $j$-th raw DAQ voltage within angular window $i$; $N_i$ is its sample count. Its arithmetic mean and sample standard deviation are

$$\bar V_i=\frac{1}{N_i}\sum_{j=1}^{N_i}V_{ij},\qquad s_i=\sqrt{\frac{\sum_j(V_{ij}-\bar V_i)^2}{N_i-1}}.$$

The reported per-angle scientific intensity proxy is a **robust median**. Define $m_i=\operatorname{median}_j(V_{ij})$ and robust MAD scale

$$\widehat\sigma_{\rm MAD,i}=1.4826\operatorname{median}_j|V_{ij}-m_i|.$$

For the default V10 code, a sample is flagged as a spike if

$$|V_{ij}-m_i|>k\max(\widehat\sigma_{\rm MAD,i},V_{\rm LSB}),\qquad k=6,$$

where $V_{\rm LSB}$ is the code's nominal $40/16384\ \mathrm V$ floor. The robust median is taken over non-flagged samples **unless** fewer than $\max(3,\lfloor 0.5N_i\rfloor)$ remain, in which case the original trace is used with a quality flag. The original measurements remain in output; spikes are not silently deleted.

Exponential moving average (EMA), primarily for display:

$$y_1=x_1,\qquad y_j=\alpha x_j+(1-\alpha)y_{j-1},\qquad 0<\alpha\le 1.$$

Spikes may be substituted with the robust median *only in the derived EMA trace*. This **does not alter raw storage**. The scientific estimators use robust per-angle medians, not the EMA plotting curve. For repeated scans at a given signed angle, the code takes another median over complete repeated-angle values.

The code's single-sample SNR diagnostic is

$$\mathrm{SNR}_{i,\mathrm{proxy}}=\frac{|\widetilde V_i-V_{d}|}{\max(\widehat\sigma_{\rm MAD,i},s_i,s_{d},V_{\rm LSB})}.$$

Low SNR (below 3), excessive flagged spikes (over 5%) or insufficient unflagged samples trigger `LOW_CONFIDENCE`; **these data are retained** for the discrete paper index. Standard-error diagnostics from groups of samples are not a substitute for independently replicated uncertainty, especially for correlated measurements.

## 3. Discrete published-paper-style angular index (`g_paper`)

On the positive and negative *signed* forward sectors separately, with $\theta_i\in[0^\circ,90^\circ]$ and $[-90^\circ,0^\circ]$ respectively, V10 implements

$$g_{\pm,\mathrm{paper}}=\frac{\sum_{i\in S_\pm}V_i\cos\theta_i}{\sum_{i\in S_\pm}V_i}.$$

Here $V_i$ is the **uncorrected** robust voltage proxy; there is *no* $\sin\theta$ weight or empty-cuvette subtraction. Both sectors can contain zero degrees, but zero is evaluated within each sector, not counted twice in a single sum. When both nonzero signed sectors exist, the reported `g_paper_avg` is $(g_++g_-)/2$; otherwise the available sector is used. Unequal angle spacing or sectoral sampling can bias this discrete index. If the denominator is effectively zero the output is undefined. This index should **not** be called an independently validated physical anisotropy measurement.

The source comment attributes its definition to Eq. (6) of a 2024 Ziaee et al. article. **Verify the original article, equation number, bibliographic details and applicability before publishing that reference.**

## 4. Solid-angle-weighted anisotropy (`g_moment_partial`)

The physical scattering anisotropy is

$$g=\langle\cos\theta\rangle=\frac{\int_{4\pi}p(\theta,\phi)\cos\theta\,d\Omega}{\int_{4\pi}p(\theta,\phi)\,d\Omega}.$$

Under **azimuthal symmetry** $d\Omega=2\pi\sin\theta\,d\theta$, and a detector response proportional to angular radiant intensity (with relevant angular transfer effects corrected), its polar moment can be written

$$g=\frac{\int_0^\pi I(\theta)\cos\theta\sin\theta\,d\theta}{\int_0^\pi I(\theta)\sin\theta\,d\theta}.$$

V10 instead computes a trapezoidal estimate over **only the observed polar coverage**:

$$g_{\mathrm{moment,partial}}=\frac{\int_{\theta_{\min}}^{\theta_{\max}}V_c(\theta)\cos\theta\sin\theta\,d\theta}{\int_{\theta_{\min}}^{\theta_{\max}}V_c(\theta)\sin\theta\,d\theta},\qquad V_c=V-V_d.$$

The implementation groups $|\theta_{\rm signed}|$ values and takes their median (positive and negative readings do **not** create separate solid-angle domains). Negative baseline-corrected values are excluded from this particular moment and flagged in output. Angular holes are not extrapolated; a trapezoidal segment bridges consecutive observed points, potentially causing bias. If coverage ends at $155^\circ$, that is **not** the complete $0^\circ$–$180^\circ$ integral. No hardware angular sensitivity or acceptance-solid-angle correction is performed by the present estimator.

## 5. Henyey–Greenstein fit (`g_HG_fit`)

The normalized HG phase function is

$$p_{\rm HG}(\theta;g)=\frac{1-g^2}{4\pi\left(1+g^2-2g\cos\theta\right)^{3/2}},\qquad -1<g<1.$$

V10 fits the forward **positive baseline-corrected** signal as $V_c(\theta)\approx A p_{\rm HG}(\theta;g)$, using symmetric averaging by $|\theta|$ and a robust nonlinear least-squares fit of *logarithmic* residuals

$$r_i(g,\log A)=\log A+\log p_{\rm HG}(\theta_i;g)-\log V_{c,i}.$$

The code uses SciPy `least_squares(loss='soft_l1', f_scale=0.2)`, initial $g=0.75$, and fit bounds $0.001\le g\le0.999$. Only positive corrected signals, at least **six distinct angles**, and forward-angle span of at least **25 degrees** are admitted, by default within $2^\circ$–$90^\circ$. The log-space diagnostic is

$$R^2_{\log}=1-\frac{\sum_i(\widehat{\log V_i}-\log V_i)^2}{\sum_i(\log V_i-\overline{\log V})^2}.$$

The code makes `g_HG_fit` blank in the paper summary if $R^2_{\log}<0.8$, $R^2_{\log}$ cannot be computed, or $g$ is close to bounds ($g\le0.005$ or $g\ge0.995$). A fit passing those **software heuristics is not, by itself, physical validation**. Multiple scattering, polarization effects, angular response, finite acceptance angles and unobserved backward flux can bias the fitted parameter. Note that fitting only nonnegative $g$ cannot identify negative anisotropy even though the general HG model permits it.

## 6. Apparent attenuation and penetration-depth proxy

Let $V_{\rm ref}$ be the empty-cuvette reference, $V_{\rm sample,max}$ the largest sample signal over recorded angles, $V_d$ the baseline (dark) voltage, and $L$ the entered optical path in **cm**. V10 computes

$$T_{\rm app}=\frac{V_{\rm sample,max}-V_d}{V_{\rm ref}-V_d},\quad\tau_{\rm app}=-\ln T_{\rm app},\quad \mu_{t,\rm app}=\frac{\tau_{\rm app}}{L}.$$

For $\mu_{t,\rm app}>0$, its reciprocal is a **proxy** $\delta_{\rm app}=1/\mu_{t,\rm app}$. A $140\ \mathrm{\mu m}$ path is $0.014\ \mathrm{cm}$. Inputs must share gain, optical geometry and reference conditions; the sample maximum must be physically meaningful. In a scattering sample the peak detected signal may not be ballistic transmittance, so **do not equate $\mu_{t,\rm app}$ to intrinsic $\mu_a+\mu_s$** without extra evidence. A negative apparent attenuation (possible if $T_{\rm app}>1$) is a quality or model warning, not a negative intrinsic extinction coefficient.

In an ideal independently calibrated ballistic Beer–Lambert model, $T_{\rm ballistic}=\exp[-(\mu_a+\mu_s)L]$, $\mu_t=\mu_a+\mu_s$; this ideal relation does not establish those coefficients from the present angular voltage profile.

## 7. Forward and backward angular-signal proxies

For the valid, baseline-corrected voltage curve, V10 approximates

$$P_{F,\mathrm{proxy}}=2\pi\int_{0}^{\pi/2}V_c(\theta)\sin\theta\,d\theta,$$

$$P_{B,\mathrm{proxy}}=2\pi\int_{\pi/2}^{\pi}V_c(\theta)\sin\theta\,d\theta,\qquad B/F=P_{B,\mathrm{proxy}}/P_{F,\mathrm{proxy}}.$$

**Actual code integrates only valid observed points within each sector**, not missing angles or a reconstructed full hemisphere. These are voltage × steradian **proxies**, not absolute scattered optical power, reflectance, transmittance or $\mu_s$. The partial backward fraction is $P_B/(P_F+P_B)$ when both are available and the denominator is positive.

## 8. Identifiability and measurement uncertainty

$\mu_s'=\mu_s(1-g)$ is a physical definition. Neither $\mu_s$ nor $\mu_a$ (and thus not independently $\mu_s'$) is estimated from the available uncalibrated voltage-vs-angle measurements. Independent optical power calibration and an appropriate light-transport inverse model, or additional measurements, are required.

For uncertainty, record independent scans, angular zero error, detector responsivity/gain drift, blank uncertainty, baseline and acquisition repeatability, angular acceptance, path-length uncertainty, and polarization. No confidence interval for intrinsic optical coefficients is justified simply by displaying sample standard deviation within one acquisition window.

## 9. Implemented vs. theoretical equations

Equations 2–7 describe the V10 processing or their physical motivation. The theoretical full-sphere $g$, ballistic Beer–Lambert extinction, and the definition of $\mu_s'$ are **not independent validated outputs** of the present system. Record all differences between detector intensity and voltage, and label uncalibrated results as **signal proxies**.

## Reference starting points

1. Henyey, L. G. & Greenstein, J. L. (1941). *Diffuse radiation in the galaxy*. Astrophysical Journal **93**, 70–83.
2. Jacques, S. L. (2013). *Optical properties of biological tissues: a review*. Physics in Medicine & Biology **58**, R37–R61.
3. Source-code attribution for a 2024 discrete estimator requires independent bibliographic verification before final submission.

---

*Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠*
