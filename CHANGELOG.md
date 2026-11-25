# Changelog

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
