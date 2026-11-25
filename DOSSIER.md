# Read-back limit and PLC handshake for a laser-marked Data Matrix code

Systems-engineering dossier. **Simulated study, no hardware.** Every number below is produced by `python scripts/run_all.py` and stored in `results/summary.json` or `results/handshake_exploration.md`. Sources are in [docs/SOURCES.md](docs/SOURCES.md) (S1 to S8). Parameter values are illustrative unless section 8 names a source.

## 0. Findings

The six findings are in [README.md](README.md). In one line each: the mark fails like a cliff; the grade scale does not see the drift but the raw cell contrast does; only a limit on the cell contrast value meets both targets; an error-correction margin adds nothing at this station; the guard band is for calibration drift, not for noise; the handshake holds five safety properties under any single fault, by exhaustive search.

## 1. Mission and operational concept

**Need.** Every part that leaves the station carries one Data Matrix code that (a) belongs to that part, (b) exists once, (c) is in the traceability records and (d) can still be read by a weaker reader later: at the customer's line or in the aftermarket.

**Stakeholders.** Line operator (few stops, clear reject reason), maintenance (knows which drift caused a reject), quality (escape rate, records), customer (reads the code on a different reader), the PLC programmer who owns marking (S1, conclusion).

**Operational concept.** A part arrives in the nest with its part number and variant known from the carrier or the line controller. The PLC looks up the marking job in a recipe table (more than 300 jobs live in the marker, S1 section 3.3.3), reserves a serial number, sends job and serial to the marker, starts the mark once, triggers the reader, compares the content, checks the quality limit, writes the record and only then releases the part. Any doubt ends in a reject and a void record. A part is never marked twice: a second pass over paint that is already open would damage the code.

**Out of scope.** Laser safety, fume extraction, the mechanical nest, the layout of the code on the part, the records database itself.

## 2. Modes and states

Station steps of `plc/FB_MarkStation.scl`:

| Step | Name | Leaves when |
|---|---|---|
| 0 | Init | first call after a restart: to Recover if a cycle was open, else to Idle |
| 10 | Idle | a part is present: to Reserve; no job or serial range used up: to Reject |
| 20 | Reserve | always after one call; the serial counter has moved and is retentive |
| 30 | Load | marker echoes the requested job and serial: to Mark; time-out or fault: to Reject |
| 40 | Mark | marker done: to Read; time-out or fault: to Reject |
| 50 / 51 | Read / gap | content equal and quality limit met: to Record; after `MaxReads` attempts: to Reject |
| 60 | Record | record acknowledged: to Release; time-out: to Reject |
| 65 | Release | the part has left: to Idle |
| 70 | Reject | void record acknowledged and the part has left: to Idle |
| 80 | Recover | nest empty and the last cycle was recorded good: to Idle; otherwise to Reject |

In steps 20 to 60 a part that disappears from the nest sends the sequence to Reject. Process modes (production, changeover, restart) need no separate state: a changeover is a new part number at Idle, a restart is step 0.

## 3. Requirements

| ID | Requirement | Origin | Verified by |
|---|---|---|---|
| R1 | The symbol is ECC 200, 16x16, ASCII encodation, 19 digits | ISO/IEC 16022 as described in S4 | `tests/test_ecc200.py`: published vectors, independent library |
| R2 | The station decodes the code and compares part number, variant and serial with what it ordered | this project | search property P1, `tests/test_plc.py` |
| R3 | At most 0.1 % of released parts are unreadable at the later reader | this project (target) | Monte Carlo, section 6 |
| R4 | At most 2 % of later-readable parts are rejected | this project (target) | Monte Carlo, section 6 |
| R5 | Quality is measured in the terms of ISO/IEC 15415 and ISO/IEC 29158 | standards as printed in S2, S3 | `tests/test_mark_reader_grading.py`: grade tables, synthetic symbols |
| R6 | A serial number is never used twice, also across a power loss | this project | search properties P2, P3; mutants 1 and 6 |
| R7 | A part is released only with an acknowledged "good" record | this project | search property P3; mutant 3 |
| R8 | A part gets one start command at most | this project (paint cannot be marked twice) | search property P4; mutant 4 |
| R9 | The job is selected from a table of at least 300 rows by part number and variant; no row or two rows give no job | S1 for the 300 jobs | `tests/test_plc.py` |
| R10 | Every reserved serial ends on a released part or in a void record | this project | search property P5 |
| R11 | Read attempts are bounded | this project | step 50 / 51, `MaxReads` |

## 4. Architecture and interfaces

### 4.1 Blocks

```
carrier / line controller --PartNo, VarCode--> FB_JobSelect --JobNo, Found--> FB_MarkStation
                                                                               |  ^
                                    marker  <-- MkJobNo, MkSerial, MkLoad, MkStart |
                                            --> MkLoaded, echoes, MkDone, MkFault -+
                                    reader  <-- RdTrigger   --> RdDone, RdOk, content
                                    records <-- RecReq, RecSerial, RecStatus --> RecAck
                                    nest    --> PartPresent <-- Release, Reject
FB_CodeContent: PartNo, VarCode, Serial --> twelve data codewords (for a marker that takes raw codewords, and for the HMI)
```

Call order in one cyclic interrupt: `FB_JobSelect`, then `FB_MarkStation`. Times are counted in calls.

### 4.2 Stand-in protocol

The real marker protocol is not public. S1 only says that the job files sit in the control unit and are called over Ethernet. The stand-in is the smallest protocol that lets the PLC prove what the marker is about to do:

- **Load.** `MkLoad` with `MkJobNo` and `MkSerial`. The marker answers `MkLoaded` and echoes both values. No start without a matching echo.
- **Start.** `MkStart` is a level. The marker answers `MkDone` and holds it until `MkStart` drops. Dropping `MkStart` during marking stops the marker.
- **Read.** `RdTrigger` is a level. The reader answers `RdDone`, `RdOk` (decoded and quality limit met) and the decoded fields.
- **Record.** `RecReq` with serial and status (1 good, 2 void); `RecAck` held until `RecReq` drops. A void request waits until an older acknowledge is gone.

### 4.3 Code content

Part number (8 digits), variant (2), serial (9). Digit pairs become one codeword each: ten codewords, two pads, twelve check codewords. `FB_CodeContent` builds the twelve data codewords; the test compares them with the Python encoder for 303 inputs.

## 5. Performance budgets and margins

| Budget | Value | Margin |
|---|---|---|
| Beam waist / Rayleigh range | 25.7 um / 1.30 mm | |
| Peak fluence against threshold | 19.3 J/cm2 against 1 J/cm2 | |
| Ablated diameter of one pulse | 62.5 um; pulse pitch 10 um, hatch 22 um | pulses overlap in both directions |
| Removed depth in a filled module | 41.3 um | 2.06 x the 20 um paint |
| Focus offset with removed depth >= paint | 3.9 mm (full energy), 3.0 mm (75 %), 1.75 mm (50 %) | 3.0 Rayleigh ranges at full energy |
| Error correction | 12 check codewords: 6 codeword errors | unused error correction 1.0 at nominal |
| Cell contrast | nominal 0.888, station limit 0.837 | 0.051 below nominal |
| Station repeatability of cell contrast | 0.0007 (1 sigma) | guard band 2 sigma + 0.01 |
| Later reader on nominal marks | reads 100.00 % of 1,000 parts x 3 reads | |

The depth margin is large and the contrast margin is small. That is the point of finding 2: a mark keeps full contrast until the removed depth comes within a few micrometres of the film, then loses patches of modules.

## 6. Analysis of alternatives

Same 6,000 simulated parts for every alternative. Each drift mechanism (focus, pulse energy, window transmission, paint thickness) has its own level per part. Of all marked parts the later reader reads 96.89 %.

| | Alternative | Not read later (% of released) | Rejected though readable (%) | Released (% of marked) | Meets R3 and R4 |
|---|---|---|---|---|---|
| A0 | no read-back | 3.11 | 0 | 100.00 | no |
| A1 | decode only | 0.76 | 0 | 97.63 | no |
| A2 | grade >= 1 | 0.14 | 1.04 | 96.02 | no |
| A2 | grade >= 2 | 0.13 | 1.17 | 95.88 | no |
| A2 | grade >= 3 | 0.13 | 1.41 | 95.65 | no |
| A2 | grade >= 4 | 0.018 | 2.43 | 94.55 | no |
| A3 | grade >= 2 and UEC >= 0.67 | 0.13 | 1.26 | 95.80 | no |
| A3 | grade >= 2 and UEC >= 0.84 | 0.13 | 1.31 | 95.75 | no |
| A3 | grade >= 3 and UEC >= 0.84 | 0.13 | 1.46 | 95.60 | no |
| A3 | grade >= 2 and UEC >= 1.0 | 0.13 | 1.31 | 95.75 | no |
| A4 | CC >= 0.826 (no guard band) | 0.087 | 1.19 | 95.82 | yes |
| A5 | CC >= 0.837 (guard-banded) | 0.035 | 1.59 | 95.38 | yes |

Criteria: R3, R4, and effort. A1 needs no quality measurement. A2 and A3 need grading in the reader. A4 and A5 need the raw cell contrast from the reader and a reference card to keep that reading calibrated.

**Why the grade does not work.** Grade 4 for cell contrast starts at 0.30 (S3). The nominal mark has 0.888 and the later reader starts to fail near 0.826. All of that is inside grade 4. When the overall grade does fall below 4 it is set by fixed pattern damage (118 parts), cell modulation (41) and grid non-uniformity (26), never by cell contrast or unused error correction.

**Why cell contrast works here.** It is the mean over all modules. A mark at the edge of the process window loses dark modules in patches where the film is thick; each lost patch raises the dark mean. Areas under the ROC curve as predictor of a later no-read, station-decoded parts only:

| Station value | Area under the ROC curve |
|---|---|
| cell contrast | 0.996 |
| cell modulation, 5th percentile | 0.994 |
| modulation, lowest module | 0.990 |
| cell modulation, lowest module | 0.990 |
| grid non-uniformity | 0.988 |
| print growth | 0.915 |
| axial non-uniformity | 0.842 |
| unused error correction | 0.840 |
| symbol contrast | 0.479 |
| cell modulation, mean | 0.360 |

**Selected: A5.** Limit on the raw cell contrast, 0.837 for this process. A station that reads 0.01 too high then still lets only 0.087 % through; with the limit of A4 it would be 0.14 %.

**Caveat.** The escape rates near 0.1 % rest on few failed later reads (15 for A4, 6 for A5, out of three reads per part), and the limit of A4 is fitted on the same parts. The ranking of the alternatives is the result, not the third digit.

## 7. Modelling and simulation

### 7.1 Symbol

`dmx/ecc200.py`. Field polynomial 301, generator with roots alpha^1 to alpha^n, systematic encoding, Berlekamp-Massey and a root search for decoding, error values from the syndrome equations. Checked against the published generator polynomial and the published 24 codewords of the "Wikipedia" example (S4), against an independent reader for all 14 sizes, and module for module against an independent writer.

### 7.2 Mark

`dmx/mark.py`. Beam radius from the Gaussian-beam relations (S5). One pulse leaves a paraboloid crater from d = delta ln(F/Fth) with a Gaussian fluence profile (S6, eq. 6.25 and 6.22). The removed depth of a filled area is the fill mask convolved with that crater, divided by pulse pitch times hatch. Three focus planes across the symbol follow the tilt and the curvature of the surface. The paint is open where the removed depth exceeds the local film thickness. The film varies in two scales: fine roughness inside a module and a slow build variation across the symbol. Reflectance is paint or substrate, with paint texture.

Checked: w(zR) = sqrt(2) w0; D^2 doubles when ln(E/Eth) doubles; the crater on the pixel grid holds the paraboloid volume within 3 %; a "paint" that is too thick is never opened.

### 7.3 Grading

| Parameter | Status | Note |
|---|---|---|
| Decode | simplified | own locator and decoder, not the reference decode algorithm |
| Symbol contrast | as in S2 | brightest minus darkest module, 70 / 55 / 40 / 20 % |
| Modulation | as in S2 | 2 abs(R - GT) / SC, graded per codeword through a notional unused error correction; low codewords are counted as errors (my reading) |
| Reflectance margin | not separate | wrong-side modules count as 0 inside cell modulation |
| Cell contrast | as in S3 | (Lmean - Dmean) / Lmean, 30 / 25 / 20 / 15 % |
| Cell modulation | simplified | range from threshold to group mean (S3); graded on the modulation scale because no public table was found |
| Minimum reflectance | missing | no public formula found |
| Fixed pattern damage | simplified | L sides, clock tracks, one-module quiet zones and the average grade with the published tables; the L sides use the quiet-zone table; no gap test, no transition ratio |
| Unused error correction | as in S2 | 1 - 2 t / 12 with t corrected codewords; no erasures |
| Axial non-uniformity | as in S2 | from the measured X and Y pitch |
| Grid non-uniformity | simplified | clock-track module centres against a uniform grid |
| Print growth | informative | dark clock-module width against the pitch, not graded (S3b) |
| Overall grade | as in S3b | lowest of decode, cell contrast, cell modulation, fixed pattern damage, unused error correction, axial and grid non-uniformity |

With the 15415 parameters the painted part never reaches grade 4: of 1,000 nominal marks 911 have grade 2 and 89 grade 3, because the symbol contrast (median 0.52, grade 3 from 0.55, grade 4 from 0.70) is capped by the paint reflectance. That is the reason to grade a direct part mark on cell contrast.

### 7.4 Readers

`dmx/reader.py`. Lighting gradient, additive glare, Gaussian blur, pixel aperture, foreshortening from the viewing angle, sub-pixel shift, noise. Locator: outer edges of the solid "L" from threshold crossings, uniform grid between them. Station: 10 pixels per module, minimum-variance threshold (S3c). Later reader: 5 pixels per module, more of everything, mid-range threshold.

### 7.5 Monte Carlo

`dmx/study.py`. Nominal run (1,000 parts), one-at-a-time sweeps (9 levels x 75 parts x 4 mechanisms), drift mix (6,000 parts). Every part: one station read with grading, three later reads with fresh reader conditions. Escape = accepted and not read later; false reject = rejected and read later. No reader returned a wrong content in any run.

### 7.6 Handshake

`dmx/explore.py`. The SCL source text of `FB_MarkStation` is translated and executed call by call (`dmx/scl.py`, S7). The environment may, before any call: let a part of type A or B arrive, remove the part, or cut the power. In its reaction it may: not load, load stale values with an honest or a wrong echo, take one, two or three calls to mark, hang with no, a partial or a complete mark, raise a fault, return a no-read, store a record at once or one call later. One fault per run. Breadth-first search over all reachable combined states.

| Parts per run | States | Transitions | Violations |
|---|---|---|---|
| 3 | 3,604 | 5,573 | 0 |
| 6 | 22,936 | 34,153 | 0 |

Properties: P1 right and complete code on a released part; P2 no repeated serial among released parts; P3 a good record and no void record at release; P4 one start command per part; P5 every reserved serial is on a released part or void once the station is idle and empty. All eleven steps of the block are reached.

Two design errors were found by this search while the block was written, both fixed: a restart in the release step could release a part that had been loaded into the nest during the power cut, and a void request directly after a good request never got its own edge at the record store.

## 8. Parameters: sourced and assumed

| Parameter | Value | Source |
|---|---|---|
| Wavelength | 1062 nm | S1 table 3 |
| Working distance used as focal length | 190 mm | S1 table 3 (LF4) |
| Beam diameter at the lens | 7.5 mm | S1 table 3 (my reading of the unit) |
| Marking speed | 500 mm/s | S1 section 3.3.3 |
| More than 300 jobs, selected by PLC | 320 rows | S1 section 3.3.3 |
| Beam quality M^2, pulse energy, repetition rate, hatch | 1.5, 0.2 mJ, 50 kHz, 22 um | ILLUSTRATIVE |
| Ablation threshold, penetration depth | 1 J/cm2, 2 um | ILLUSTRATIVE |
| Paint thickness, roughness, build variation | 20 um, 2 um, 3 um over 0.6 mm | ILLUSTRATIVE |
| Reflectance of paint and substrate | 0.55, 0.06 | ILLUSTRATIVE |
| Module size, symbol | 0.25 mm, 16x16 | ILLUSTRATIVE |
| Both readers | see `results/summary.json`, `parameters` | ILLUSTRATIVE |
| Drift full scale and Beta(1, 5) levels | focus 4 mm, energy 50 %, window 50 %, paint 40 um | ILLUSTRATIVE |
| Calibration allowance of the station contrast reading | 0.01 | ILLUSTRATIVE |

## 9. FMEA

| Failure | Effect without measure | Measure | Evidence |
|---|---|---|---|
| Focus offset, low pulse energy, dirty window, thick paint | code readable at the station, not later | limit on raw cell contrast (A5) | section 6 |
| Station contrast reading drifts high | limit silently loosens | reference card check; guard band | finding 5 |
| Marker keeps the previous job or serial | wrong or duplicate code | echo check before start; content compare after read | P1, P2; mutants 2 and 7 |
| Marker does not load, hangs or faults | part with no or partial code | time-outs; reject; never a second start | P1, P4 |
| Reader no-read | good part scrapped | bounded re-read, no re-mark | R11 |
| Part taken out during the cycle | another part released on its record | presence monitor in steps 20 to 60 | P1; mutant 5 |
| Power loss between mark and record | serial reused, or part released without record | counter moves before the mark and is retentive; open cycle is void after restart | P2, P3, P5; mutants 1, 4, 6 |
| Record store slow or down | release without record | release only on acknowledge | P3; mutant 3 |
| Part number not in the table, or twice | wrong job | no job, reject unmarked | `tests/test_plc.py` |
| Serial range used up | counter wraps, duplicates | reject unmarked, alarm 8 | block source, not explored |

The echo check alone is not needed for the five properties (mutant 7 gives no violation). It stays: with it a stale marker is caught before the start and the part stays unmarked; without it the part is marked wrong and scrapped.

## 10. Verification and validation plan

Done here: 71 tests (published vectors, independent library, closed forms, published grade tables, SCL blocks, exhaustive search and seven mutants, the PLC test bench through the translator), the study, CI on every push. Outside the test suite: the PLC test bench in S7-PLCSIM Advanced, 21 of 21 cases ([docs/PLCSIM_RUN.md](docs/PLCSIM_RUN.md)).

Needed on a real station, not done:

1. Run the blocks on the station's PLC. Done so far, in simulation only: the SCL sources compile in TIA Portal V18 with 0 errors and 0 warnings (`results/tia_compile.txt`), and a self-checking test project (`plc/test/`, 21 cases, expected values generated from the Python model) compiles the same way (`results/tia_compile_testbench.txt`) and passes 21 of 21 in the translator. On 25 November 2026 that project was upgraded to TIA Portal V20, compiled there (software 0 errors, 0 warnings) and run in S7-PLCSIM Advanced V7.0 Upd1: 21 of 21 cases passed with 803 bench calls, the count of the translator. Record: [docs/PLCSIM_RUN.md](docs/PLCSIM_RUN.md). A simulated CPU is not the station's PLC, and the bench has no power loss; item 5 stays.
2. Correlate the reader's cell contrast with a calibrated verifier on real marks; repeat the repeatability figure with a gauge study.
3. Mark a focus, power and paint-thickness matrix on real parts; read each part on the station and on the weakest later reader; refit the limit.
4. Replace the stand-in protocol with the marker's real one and rerun the search with its real failure behaviour.
5. Cut the power on the real PLC in every step.

## 11. Limits

See "Not verified, not done" in the README. In addition: the drift mix is an assumption, so the escape and reject rates are not predictions; the later reader is one invented reader, not a population of real ones; liveness of the sequence is not checked (a record store that never answers holds the station in Record or Reject, by design).

## 12. References

[docs/SOURCES.md](docs/SOURCES.md).
