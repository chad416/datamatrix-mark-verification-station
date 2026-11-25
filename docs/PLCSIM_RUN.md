# Running the compiled blocks in S7-PLCSIM

**Status: prepared, not yet run in PLCSIM.** Everything below up to "Observed in PLCSIM" was done without PLCSIM. The last section is empty on purpose.

## What the bench does

`plc/test/` holds a test bench that runs inside the PLC with no outside stimulus.

- `FB_TestBench` is called once per cycle from OB1 (`Main`).
- It first loads the 320-row recipe table into `FB_JobSelect`, one row per call.
- Then it runs 21 cases one after the other. In each case parts arrive in the nest, `FB_MarkStation` works against three small stand-ins (`FB_SimMarker`, `FB_SimReader`, `FB_SimRecords`), and one fault may be injected: no-read, marker time-out, stale job, stale serial, part taken out, slow record store. `FB_CodeContent` builds the code content for every start command.
- After each case it compares eleven values with the expected ones (parts released, rejected and taken out, alarm codes, start commands, job numbers, last serial, good and void records, recorded serials, checksum of the code content) and checks that no released part carries a wrong code.
- When all cases are through it stops calling the blocks and holds the result. OB1 copies the result into `DB_TestResult` in every cycle.

The expected values are not typed by hand and do not come from the SCL stand-ins. `scripts/make_testbench.py` generates them from the Python model (`dmx/bench.py`: the environment of the state-space search with the station block in the loop, and the Python encoder for the code content) and writes them into `plc/test/FB_BenchCases.scl`. The cases and their numbers are listed in [results/testbench_cases.md](../results/testbench_cases.md).

The bench does not cut the power. A restart with an open cycle is covered by the state-space search and by `tests/test_plc.py`, not here.

## What is known before PLCSIM

- **Translator.** The same source text runs through the repo's translator in `tests/test_testbench.py` and in CI: 21 of 21 cases pass, 803 calls. With one safeguard removed from the station block the bench fails, with a wrong expected value it names the case and the check, and a station that hangs ends in check 13.
- **TIA Portal V18.** `scripts/tia_build.ps1` generates all blocks, both data blocks and OB1 from the sources and compiles them for the CPU 1513-1 PN (6ES7 513-1AL02-0AB0, V2.9). Record: [results/tia_compile_testbench.txt](../results/tia_compile_testbench.txt). Software: 0 errors, 0 warnings. Hardware: 0 errors, 2 warnings, both about missing passwords (see below).

What PLCSIM adds: the code that the TIA compiler generated is executed, instead of the translator's reading of the source.

## Settings of the test project

Three settings differ from a new project, so that the project loads into PLCSIM without a wizard. They are set by the build script and written into the compile record. They belong to this simulation project only; do not copy them to a real PLC.

| Setting | Value | Why |
|---|---|---|
| Project > Properties > Protection: "Support simulation during block compilation" | on | PLCSIM needs it |
| CPU > Protection & Security: protection of confidential PLC configuration data | switched off (Openness `Unprotect()`; the record prints the state `None`) | without a decision the hardware does not compile |
| CPU > Protection & Security > Access level | full access, no password (record: `NoAccess -> FullAccess`) | the default asks for a password and does not compile without one |

The two hardware warnings ("PLC_1 does not contain a configured protection level", "CPU display does not contain any password protection") follow from this and are expected.

## How to run

The menu names below are written from memory of TIA Portal V18; they were not checked on a screen for this document.

The archive is not in the repo. It is written to `C:\TIA_build\plcsim\dmc36_plcsim_test.zap18` by the build command in the header of `scripts/tia_build.ps1` (usage 2).

1. TIA Portal V18: **Project > Retrieve**, choose `dmc36_plcsim_test.zap18`, choose a target folder. The project opens.
2. Select **PLC_1** in the project tree. **Online > Simulation > Start**. Confirm the note about disabling other online interfaces. PLCSIM starts.
3. In the load dialog: **Start search**, select the simulated CPU, **Load**. In the preview: **Load**. At the end choose **Start module** and **Finish**. The CPU goes to RUN.
4. In the project tree open **PLC_1 > Program blocks > DB_TestResult**. Click **Monitor all** (the glasses icon).
5. Compare the column "Monitor value" with the table below. The bench is finished long before the block is open: it needs 803 cycles.

Not checked here, because PLCSIM is not installed on the build machine: whether PLCSIM in the target installation accepts the firmware V2.9 CPU with secure PG/PC communication as it is, or asks for one more confirmation in the load dialog.

## Values that mean pass

| Tag in `DB_TestResult` | Expected |
|---|---|
| `Done` | TRUE |
| `AllPassed` | TRUE |
| `CasesTotal` | 21 |
| `CasesRun` | 21 |
| `CasesPassed` | 21 |
| `CasesFailed` | 0 |
| `FirstFailedCase` | 0 |
| `FirstFailedCheck` | 0 |
| `Calls` | 803 |
| `CasePass[1]` to `CasePass[21]` | TRUE |
| `CasePass[22]` to `CasePass[32]` | FALSE (not used) |

`Calls` is the number of bench calls until it was done. It must be exactly 803, the same as in the translator: one more or one less means the compiled blocks took a different path.

If it does not pass: `FirstFailedCase` is the case number in [results/testbench_cases.md](../results/testbench_cases.md). `FirstFailedCheck` is the check:

| No. | Check | No. | Check |
|---|---|---|---|
| 1 | parts released | 8 | good records |
| 2 | parts rejected | 9 | void records |
| 3 | parts taken out | 10 | sum of recorded serials |
| 4 | sum of alarm codes | 11 | checksum of the code content |
| 5 | start commands | 12 | released parts with a wrong code (must be 0) |
| 6 | sum of job numbers | 13 | case not finished within 400 calls |
| 7 | last serial | | |

`Done` = FALSE after a few seconds in RUN means the bench is not being called or the CPU is in STOP. The actual values of the run are in the instance data block `DB_TestBench` (static part: `NReleased`, `AlarmSum`, `CaseIx` and so on).

## Observed in PLCSIM

**Not run yet. To be filled in after the run.**

- Date, TIA Portal and PLCSIM version:
- `Done`:
- `AllPassed`:
- `CasesTotal` / `CasesRun` / `CasesPassed` / `CasesFailed`:
- `FirstFailedCase` / `FirstFailedCheck`:
- `Calls`:
- `CasePass[1..21]`:

Screenshot of `DB_TestResult` in "Monitor all": *(place the image in `docs/img/plcsim_db_testresult.png` and link it here)*
