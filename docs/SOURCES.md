# Sources

Sources were read for this project in November 2026. The column "taken from it" says
exactly what is used. Where a document could not be opened, the row says "not read";
such documents are cited by number only and used as reported by the public source
named next to them. Anything not in this table is an assumption and is marked
ILLUSTRATIVE in the code and in `DOSSIER.md` section 8.

## Scenario

| # | Source | URL | Taken from it |
|---|---|---|---|
| S1 | V. Sándor, *Průmyslové značení výrobků* (Industrial marking), bachelor thesis, Czech Technical University in Prague, Faculty of Electrical Engineering, 2017 (file name: "Znaceni vyrobku v prumyslu") | https://dspace.cvut.cz/handle/10467/70010 | Section 3.3.3 (p. 38): marking jobs are stored in the laser control unit over Ethernet and "called, for example by a PLC, according to the part to be marked"; "the control unit often holds over 300 JOBs". Table 3 (p. 32): ytterbium fibre laser, 1055 to 1070 nm (typically 1062 nm), beam diameter 7.5 (the table prints "nm"; read here as mm), optics LF4 with a 100 x 100 mm field at 190 mm, RS 232 / USB / Ethernet. Section 3.3.3 example job: 50 % power, 500 mm/s. Section 3.3.4 (p. 39): a mark in bare plastic has limited quality; with a coated surface the laser removes the coating, uncovers the black plastic and gives better contrast (chrome plating is the example given). Conclusion (p. 51): the author looks after marking next to PLC programming. **Not in the thesis:** read-back, grading, any standard for symbol quality, the handshake, a painted part. Those are this project's assumptions. |

## Symbol quality (ISO/IEC 15415, ISO/IEC 29158)

| # | Source | URL | Taken from it |
|---|---|---|---|
| - | ISO/IEC 15415, *Bar code symbol print quality test specification: Two-dimensional symbols* | not read (paywalled) | Nothing directly. Used as printed in S2. |
| - | ISO/IEC TR 29158 (AIM DPM-1-2006) and ISO/IEC 29158, *Direct Part Mark (DPM) Quality Guideline* | not read (paywalled) | Nothing directly. Used as printed in S3 and S3b. |
| S2 | Cognex DataMan documentation, *ISO/IEC 15415 Grading Parameters* | https://docs.cognex.com/dmst_619/web/EN/DM475V_Manual/Content/Topics/DM475V/TruCheck/ISO_IEC15415.htm | Symbol contrast = reflectance of the brightest minus the darkest module, grades at 70 / 55 / 40 / 20 %. Modulation MOD = 2 abs(R - GT) / SC with GT midway between brightest and darkest module, grades at 50 / 40 / 30 / 20 %; the modulation grade is the highest level whose modules give a notional unused-error-correction grade of that level or higher; reflectance margin is the same with corrected modules counted as 0. Fixed pattern damage: components (L sides, quiet zones, clock tracks, transition ratios, average grade), share of incorrect modules 0 / 9 / 13 / 17 %, clock track "3 of every 5 consecutive modules correct", average grade of five values with grades at 4.0 / 3.5 / 3.0 / 2.5, overall = lowest component. Axial non-uniformity grades at 6 / 8 / 10 / 12 %. Grid non-uniformity = worst distance of a module centre from its ideal centre in module sizes, centres from the clock-track edges, grades at 38 / 50 / 63 / 75 %. Unused error correction grades at 62 / 50 / 37 / 25 %. The page does not give the transition-ratio procedure in enough detail to implement, and no rule for the overall grade. |
| S3 | Cognex documentation, *ISO 29158 (AIM-DPM) 2006 Grading Parameters* and *ISO/IEC TR 29158 (AIM-DPM)* | https://docs.cognex.com/dmst_617/web/EN/DM475V_Manual/Content/Topics/DM475V/TruCheck/AIM_DPM.htm , https://docs.cognex.com/is_650/web/EN/ise/Content/Reference/iso-iec-tr-29158.htm | Cell contrast CC = (Lmean - Dmean) / Lmean, grades at 30 / 25 / 20 / 15 %. Cell modulation: "a range for each group (light and dark) is created from the global threshold to the mean reflectance of the elements"; no formula and no grade table on the page. Minimum reflectance: described as a 5 % requirement, no formula. Fixed pattern damage is "functionally different" from 15415 because threshold and modulation scale differ; a distributed damage grade exists; no numbers. |
| S3b | Euresys Open eVision documentation, *ISO/IEC 15415 for Data Matrix and QR Codes* and *ISO/IEC 29158 for Data Matrix and QR Codes* | https://documentation.euresys.com/Products/OPEN_EVISION/OPEN_EVISION/en-us/Content/03_Using/4_Text_and_Code_Reading/9_Code_Grading/ISO-IEC_15415_for_Data_Matrix_and_QR_Codes.htm | The scan grade is the lowest of all grades. Print growth is informative, not graded, and measured on the timing pattern. The cell modulation grade combines modulation and reflectance margin. Formal grading averages five acquisitions at different rotations (not done here). Reader calibration keeps the mean light level at 70 to 86 % of full scale; the aperture is 50 % or 80 % of the cell. |
| S3c | Keyence, *Major differences in verification between ISO/IEC 15415 and ISO/IEC TR 29158* | https://www.keyence.com/ss/products/auto_id/codereader/code_verification/iso-iec-15415-29158.jsp | 15415 takes the threshold midway between the highest and lowest intensity; 29158 takes the threshold with the smallest summed dispersion of the dark and light cells and uses the two means for the cell contrast. 15415 is aimed at printed labels, 29158 at direct part marks. |

## Symbology (ISO/IEC 16022)

| # | Source | URL | Taken from it |
|---|---|---|---|
| - | ISO/IEC 16022, *Data Matrix bar code symbology specification* | not read (paywalled) | Nothing directly. |
| S4 | Wikipedia, *Data Matrix* | https://en.wikipedia.org/wiki/Data_Matrix | "L" finder and alternating timing pattern; even square sizes 10x10 to 144x144; Reed-Solomon over GF(2^8) with x^8+x^5+x^3+x^2+1 (301), first root 1; generator polynomial for five check codewords x^5+62x^4+111x^3+15x^2+48x+228; ASCII encodation (value + 1, pad 129, digit pairs 130 to 229, latch codes); worked example "Wikipedia" in a 16x16 symbol with all 24 codewords. Both vectors are test cases in `tests/test_ecc200.py`. |
| - | Codeword placement and the table of symbol sizes | no single source read in this run | The diagonal placement with its four corner cases and the data / check codeword counts per size are written from the commonly published form of the standard's routine. They are **verified, not cited**: for every supported size an independent library (zxing-cpp, tests only) decodes the symbols made here, and for the 16x16 symbol its writer produces the same matrix module for module. |

## Laser and optics

| # | Source | URL | Taken from it |
|---|---|---|---|
| S5 | Wikipedia, *Gaussian beam* | https://en.wikipedia.org/wiki/Gaussian_beam | w(z) = w0 sqrt(1 + (z/zR)^2), zR = pi w0^2 n / lambda, intensity I(r, z) with peak 2 P0 / (pi w0^2) and 1/e^2 radius w, focused waist w0' = f w0 / zR (= lambda f / (pi w0)), definition of M^2 as the ratio of the beam parameter product to that of the ideal Gaussian beam. The M^2 forms used in `dmx/mark.py` follow from that definition; they are not printed on the page. |
| S6 | M. Lenzner, J. Bonse, *How Does Incubation Affect Laser Material Processing?*, preprint of a chapter in *Scaling of Laser Processing* (Springer, 2026) | https://arxiv.org/abs/2509.08326 | Liu's D^2 method (crater diameter squared proportional to ln(E/Eth)); eq. (6.22) D = w0 sqrt(2 ln(phi0 / phi_th)) for one pulse; eq. (6.25) single-pulse ablation depth d1 = delta ln(phi0 / phi_th) from Beer's law, delta close to the optical penetration depth for metals; the warning that this single-pulse form holds only for small pulse numbers and shallow craters. Incubation (the threshold falling with pulse number) is described there and **not** modelled here. |
| - | J. M. Liu, *Simple technique for measurements of pulsed Gaussian-beam spot sizes*, Optics Letters 7 (1982) | not read | Cited through S6 only. |

## Software

| # | Source | Taken from it |
|---|---|---|
| S7 | `scl.py` of the author's extruder-pressure-fill-estimator project (MIT) | The SCL-to-Python translator, extended here with `VAR RETAIN`, `power_cycle()` and `DINT_TO_INT`. |
| S8 | zxing-cpp (Apache-2.0), Python package `zxing-cpp` | Independent Data Matrix reader and writer, imported only in `tests/test_ecc200.py`. |
