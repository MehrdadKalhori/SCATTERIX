# Blood Optical Goniometer

**An Arduino-controlled, Python-operated optical goniometry workflow for angle-resolved light-scattering measurements of blood samples.**

**Research software / experimental release candidate — not yet independently validated on the complete V10 hardware chain.** This repository packages the current motor firmware and the V10 acquisition/analysis interface. Results should not be treated as independently validated optical coefficients without the relevant calibrations and physical assumptions.

## Overview

The instrument acquires an angular signal from an optical goniometer. An **Arduino Mega 2560** commands a stepper motor while **Python** drives data acquisition, keeps raw measurements, provides a graphical interface, and exports a paper-oriented Excel workbook. **An Advantech USB-4704 DAQ** is used by this V10 Python application for detector sampling; Arduino analog input A8 is **not** the acquisition path in this version. The detector-amplifier output and DAQ input must be electrically compatible.

The system targets angle-resolved measurements at user-selected wavelengths (e.g. 532 nm and 837 nm) and optical path lengths (e.g. 140 µm). These are *configurable experimental conditions*, not claims of independent validation at either wavelength.

## Repository contents

| Path | Description |
|---|---|
| `arduino/Goniometer_Master_Mega/Goniometer_Master_Mega.ino` | Arduino Mega motion firmware, `GONIO_MASTER_1` serial protocol at 115200 baud |
| `python/Goniometer_Lab_V10_Stable.py` | V10 Tkinter application; buffered DAQ, measurement logging, output workbook |
| `python/Goniometer_Lab_V10_Stable.ipynb` | VS Code / Jupyter notebook edition |
| `docs/EQUATIONS.md` | Derivations, implemented estimators, signal processing, restrictions |
| `docs/HARDWARE.md` | Connections, geometry, operating considerations |
| `docs/REPRODUCIBILITY.md` | Validation, calibration, source-data and release checklist |
| `docs/SERIAL_PROTOCOL.md` | Arduino command and reply contract |
| `images/README.md` | Actual screenshots and photo guidance; no fabricated images |
| `examples/README.md` | Instructions for adding anonymized real datasets |
| `CITATION.cff` | GitHub's software citation metadata |

## Quick start

1. Review `docs/HARDWARE.md` and verify driver wiring, power rails, the signal ranges, and safe motion limits **before energizing hardware**.
2. Open `arduino/Goniometer_Master_Mega/Goniometer_Master_Mega.ino` in Arduino IDE, select **Arduino Mega or Mega 2560**, and upload. Do not mix it with legacy 9600-baud `WildWest_Mega_v5.ino` firmware.
3. Install the Python packages listed in `requirements.txt` into the selected VS Code/Jupyter environment. `tkinter` normally ships with a standard Windows Python installation.
4. Install the supported **Advantech DAQNavi/BDaq Python SDK** and driver separately. This vendor SDK is **not** included in pip requirements. Supply a valid USB-4704 XML profile and verify the configured DAQ device.
5. Open `python/Goniometer_Lab_V10_Stable.ipynb` inside VS Code and run its main code cell, or use a normal Python environment to execute the `.py` edition.
6. Close other software using the serial port or DAQ. Connect to the motor and USB-4704; **establish optical zero manually**. There is no home sensor/encoder.
7. Perform stationary buffered DAQ verification, dark/blank tests, and a short low-risk angular scan prior to a scientific run.
8. Inspect the exported Excel `Conditions`, `Paper_Summary`, `Intensity_by_Angle`, and `Raw_Samples` sheets. Carefully distinguish raw, corrected, plotted, and model-fit signals.

**Important:** `DONE` reports completion of *issued step pulses*, not optical encoder-confirmed arrival. The requested hardware DAQ sample rate is not independently verified just because the SDK accepts it. All motion remains subject to mechanical safety checks.

## Scientific outputs

- Signed-angle response, raw voltage trace and robust per-angle intensity proxy.
- A discrete forward-sector estimator labeled `g_paper`, calculated separately on the positive/negative angular sectors; it is **not automatically identical to the physical 3D anisotropy**.
- An azimuthally symmetric, solid-angle-weighted **partial-coverage** moment (`g_moment_partial`).
- A robust log-residual Henyey–Greenstein model fit (`g_HG_fit`), with explicit fit-quality checks.
- Apparent reference-based attenuation (`mu_t_apparent`) rather than demonstrably ballistic intrinsic `mu_t`.
- Angular forward/backward integrated **voltage proxies**, not calibrated hemispherical reflectance/transmittance or scattering coefficients.

See [the exact equations and implementation correspondence](docs/EQUATIONS.md). Neither absorption coefficient `mu_a` nor scattering coefficient `mu_s` is independently identified by these uncalibrated measurements alone.

## Current validation status

The inherited V10 project notes report Python syntax checks, simulated workbook creation and simulated GUI scans. **Full end-to-end V10 validation with the actual Arduino Mega + USB-4704 + optical arrangement remains outstanding.** Do not describe simulated output as acquired blood data. A real run must include baseline/reference procedures and independently checked timing and angular alignment.

## Figures and real data

Place **actual** GUI screenshots, setup photos, and nonidentifying experimental figures in `images/`; see `images/README.md`. If human blood data are shared, remove sample names, IDs, dates, sensitive metadata and any information that could identify a participant. Use explicit permissions and research approvals where applicable. Place only defensibly shareable observations in `examples/`.

## Citation and versioning

See `CITATION.cff`. After reviewing and verifying the code and output, create a fixed GitHub Release, attach a version tag, and (optionally) archive it through Zenodo for a DOI. Cite the **released version**, not an unpinned moving branch. Do not invent a DOI or publication reference.

## License

**No reuse license has been selected yet.** A public GitHub repository is not automatically an open-source licensed repository. Before publishing, choose a license appropriate to the ownership of all code, third-party dependencies, laboratory and institutional requirements; see `LICENSE-DECISION.md`.

## Scientific references

- Henyey, L. G., and Greenstein, J. L. (1941), *Diffuse radiation in the galaxy*, Astrophysical Journal 93, 70–83.
- Jacques, S. L. (2013), *Optical properties of biological tissues: a review*, Physics in Medicine & Biology 58, R37–R61.
- The paper-specific discrete Eq. (6) cited in V10 source as “Ziaee et al., Progress in Biomaterials 13 (2024), 132412” **needs complete bibliographic verification before inclusion in a conference manuscript**.

---

*Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠*
