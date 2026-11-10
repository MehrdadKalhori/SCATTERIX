# Reproducibility, release, and paper-submission checklist

## Before making a paper claim

- [ ] Confirm exact firmware file, version and SHA-256 hash.
- [ ] Test firmware compilation in the target Arduino IDE / AVR core.
- [ ] Confirm Python environment versions and Advantech DAQ driver/SDK versions.
- [ ] Test serial link and request-ID replies on actual Arduino Mega hardware.
- [ ] Validate optical zero, repeatability, signed-angle convention and gear ratio independently; document angular resolution and absolute error.
- [ ] Validate the USB-4704 device profile, input range, buffer mode, actual sample timing and clipped-data handling.
- [ ] Record source wavelength(s), power stability, polarization, detector gain/range, aperture size, acceptance angle, source-sample-detector distances, and sample thickness.
- [ ] Acquire laser-blocked baseline, empty-cuvette reference, repeated real sample scans and independent repeatability runs.
- [ ] Preserve raw voltage samples and status flags. Verify that no simulated results are mislabeled as real experiments.
- [ ] Quantify incomplete angular range and low-SNR bias; do not infer missing rear light without an explicit validated method.
- [ ] Reconcile all numerical equations in `docs/EQUATIONS.md` against the frozen release code.
- [ ] Verify scientific literature details and conference formatting requirements.
- [ ] Export publication figures with clearly labeled axes, units, wavelength, sample identifier (anonymous) and error bars where defensible.
- [ ] Remove identifying sample metadata, software license keys, device serials and private lab locations before public upload.
- [ ] Obtain license clearance and create versioned release; archive to Zenodo if a DOI is wanted.

## Validation status as packaged

The inherited V10 notes describe syntax checks and software simulation, **not fully verified hardware performance**. The original `.py` and `.ipynb` have been packaged without scientific algorithm rewrites. An end-to-end experimental benchmark remains essential.

## Recommended figures for the paper and README

1. Actual experimental optical/mechanical arrangement with a schematic and distances.
2. Actual V10 main application interface (remove private identifiers).
3. Angular intensity in volts against signed angle in degrees, including dark/background definition.
4. HG modeled signal over real input points with R² log-domain diagnostic (only if valid).
5. Independent repeatability comparison and identification of missing angular sectors.

Every screenshot or plot should be made from your actual executable application and marked as simulated only if it uses simulation data. Save figures as legible PNG/SVG and link them from the README after they exist.

---

*Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠*
