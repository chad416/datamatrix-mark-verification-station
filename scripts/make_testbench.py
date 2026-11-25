"""Generate the scenario table of the PLC test bench from the Python model.

    python scripts/make_testbench.py           writes plc/test/FB_BenchCases.scl and results/testbench_cases.md
    python scripts/make_testbench.py --check   fails if the files in the repo are not what the model gives now

Then the bench runs through the translator (the same source text that goes into TIA Portal)
and the result is printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dmx import bench  # noqa: E402


def main() -> int:
    results = bench.reference()
    files = {bench.TEST_DIR / "FB_BenchCases.scl": bench.generate_cases_scl(results),
             ROOT / "results" / "testbench_cases.md": bench.expected_table_md(results)}
    if "--check" in sys.argv:
        stale = [str(p.relative_to(ROOT)) for p, text in files.items()
                 if not p.exists() or p.read_text(encoding="utf-8").replace("\r\n", "\n") != text]
        if stale:
            print("not up to date:", ", ".join(stale))
            return 1
        print("generated files are up to date")
    else:
        for p, text in files.items():
            p.write_text(text, encoding="utf-8", newline="\n")
            print("wrote", p.relative_to(ROOT))
    r = bench.run_bench()
    print(f"translator: Done={r['Done']} AllPassed={r['AllPassed']} CasesTotal={r['CasesTotal']} CasesRun={r['CasesRun']} "
          f"CasesPassed={r['CasesPassed']} CasesFailed={r['CasesFailed']} FirstFailedCase={r['FirstFailedCase']} "
          f"FirstFailedCheck={r['FirstFailedCheck']} Calls={r['Calls']}")
    return 0 if r["AllPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
