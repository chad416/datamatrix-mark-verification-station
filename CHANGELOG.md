# Changelog

## 1.1.1 (2026-11-25)
- PLC test bench run in S7-PLCSIM Advanced V7.0 Upd1 under TIA Portal V20 Update 7 (project upgraded from V18): 21 of 21 cases passed, 803 bench calls. Record, observed values and screenshots in `docs/PLCSIM_RUN.md`; README and dossier say what was run. No source changed.

## 1.1.0 (2026-11-25)
- Self-checking PLC test bench for S7-PLCSIM in `plc/test/`: stand-ins for marker, reader and record store, 21 cases, result data block `DB_TestResult`, OB1. Expected values are generated from the Python model (`dmx/bench.py`, `scripts/make_testbench.py`).
- The bench runs through the translator in `tests/test_testbench.py` and in CI: 21 of 21 cases pass.
- `scripts/tia_build.ps1` can also generate data block and OB sources, switch on simulation support, compile the hardware and save a project archive. Test project: 0 errors, 0 warnings (`results/tia_compile_testbench.txt`).
- `docs/PLCSIM_RUN.md`: how to run it and which values mean pass. The PLCSIM run itself is not done yet.
- Translator: elements of array outputs of a multi-instance.

## 1.0.1 (2026-11-25)
- The SCL blocks compile in TIA Portal V18 with 0 errors and 0 warnings (`scripts/tia_build.ps1`, `results/tia_compile.txt`).
- `FB_MarkStation`: the sequence state is the static `SeqStep`; the output `Step` is a copy written at the end of the block, so the block no longer reads its own outputs. `Timer` and `Reads` renamed to `StepTicks` and `ReadCount` (TIA rejects the old names). Behaviour and state-space results unchanged.

## 1.0.0 (2026-11-25)
- ECC 200 encoder and decoder (Reed-Solomon over GF(256), codeword placement), checked against published vectors and an independent library.
- Mark model: Gaussian beam, single-pulse ablation relations, paint removal; reader model with a simple locator.
- Grading in the sense of ISO/IEC 15415 and ISO/IEC 29158 from public vendor documentation.
- Monte Carlo study of drift and of a weaker later reader; trade study of station acceptance rules.
- SCL blocks for job selection, code content and the marking sequence; exhaustive search of the handshake state space with seven mutants.
- Dossier, sources, CI.
