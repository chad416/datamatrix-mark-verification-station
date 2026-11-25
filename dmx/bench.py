"""Scenario table and expected outcomes for the PLC test bench (plc/test/).

The bench that runs inside the PLC has its own SCL stand-ins for marker, reader and
record store (plc/test/FB_Sim*.scl). The expected values it checks do NOT come from
those stand-ins. They come from here: every case is walked through the Python
environment of the state-space search (dmx/explore.py), with the station block
executed through the translator, one deterministic path per case. The code-content
checksum comes from the Python encoder (dmx/ecc200.py). So a pass in the PLC means:
the compiled blocks, driven by an independent second model of the same protocol,
give the outcomes the Python model predicts.

`generate_cases_scl()` writes the table as SCL (plc/test/FB_BenchCases.scl).
`run_bench()` executes the whole bench through the translator.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import ecc200, explore, scl

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "plc" / "test"
BENCH_BLOCKS = ("FB_BenchCases", "FB_SimMarker", "FB_SimReader", "FB_SimRecords", "FB_TestBench")
TABLE_ROWS = 320
CASE_LIMIT = 400                 # calls per case before the bench gives up (check 13)

# fault kind in the bench -> label of the same event in dmx/explore.py
FAULT_LABEL = {
    1: "FAULT marker does not load",
    2: "FAULT marker hangs, no mark",
    3: "FAULT marker hangs, mark complete",
    4: "FAULT marker fault signal",
    5: "FAULT stale job, honest echo",
    6: "FAULT stale job, wrong echo",
    7: "FAULT stale serial, honest echo",
    8: "FAULT stale serial, wrong echo",
    9: "FAULT stale job and serial, wrong echo",
    10: "FAULT reader no-read",          # first read only
    11: "FAULT reader no-read",          # every read
    12: "FAULT part removed by hand",    # when the station is in step `arg`
    13: "record store slow",
}
CHECKS = ("released", "rejected", "removed", "alarm_sum", "mark_starts", "job_sum", "serial_last",
          "good_records", "void_records", "rec_serial_sum", "code_sum")


@dataclass(frozen=True)
class Case:
    name: str
    parts: str                   # one letter per part: A, B, C (in the recipe table), U (not in it)
    fault: int = 0
    fault_part: int = 0          # 1-based index of the part that gets the fault
    arg: int = 0


CASES = (
    Case("good cycle", "A"),
    Case("three part types in a row", "ABC"),
    Case("changeover back and forth", "BBACA"),
    Case("part number not in the table, then a good part", "UA"),
    Case("no-read once, second read succeeds", "A", 10, 1),
    Case("no-read on every attempt, then a good part", "AA", 11, 1),
    Case("marker does not load (time-out), then a good part", "AA", 1, 1),
    Case("marker hangs without a mark (time-out), then a good part", "BB", 2, 1),
    Case("marker hangs after a complete mark, then a good part", "AA", 3, 1),
    Case("marker fault during marking, then a good part", "AA", 4, 1),
    Case("changeover with a stale job, honest echo", "ABB", 5, 2),
    Case("changeover with a stale job, wrong echo", "ABB", 6, 2),
    Case("stale serial, honest echo", "AAA", 7, 2),
    Case("stale serial, wrong echo", "AAA", 8, 2),
    Case("stale job and serial, wrong echo", "ABA", 9, 2),
    Case("part taken out during load", "AA", 12, 1, 30),
    Case("part taken out during marking", "AA", 12, 1, 40),
    Case("part taken out during read", "BA", 12, 1, 50),
    Case("part taken out while the record is written", "AA", 12, 1, 60),
    Case("slow record store", "AB", 13, 1),
    Case("mixed run of six parts", "ACBUCA"),
)


def code_checksum(part_no: int, variant: int, serial: int) -> int:
    """Sum of (k + 1) * codeword k over the twelve data codewords, from the Python encoder."""
    cw = ecc200.pad(ecc200.encode_ascii(f"{part_no:08d}{variant:02d}{serial:09d}"), 12)
    return sum((k + 1) * c for k, c in enumerate(cw))


def _choose(options: list, want: str | None):
    """Pick the one reaction of the environment that belongs to this deterministic path."""
    picked = [o for o in options if want and want in o[0]]
    applied = bool(picked)
    if not picked:
        picked = options

    def clean(label: str) -> bool:
        rest = label.replace(want, "") if want else label
        return "FAULT" not in rest and "record store slow" not in rest

    picked = [o for o in picked if clean(o[0])]
    if any("mark takes" in o[0] for o in picked):
        picked = [o for o in picked if "mark takes 2" in o[0]]      # the SCL stand-in marks in two calls
    if len(picked) != 1:
        raise RuntimeError(f"path is not deterministic: {[o[0] for o in picked]}")
    return picked[0], applied


def reference(cases=CASES) -> list[dict]:
    """Expected outcome of every case, from the Python environment with the station block in the loop."""
    ex = explore.Explorer(max_parts=10 ** 9)
    ex.arrival_types = tuple(explore.TYPES)
    names = ex.names
    ix = {k: names.index(k) for k in ("Step", "Alarm", "MkSerial", "MkJobNo")}
    out_names = ("MkLoad", "MkStart", "MkJobNo", "MkSerial", "RdTrigger", "RecReq", "RecSerial", "RecStatus", "Release", "Reject")
    ex.plc.power_cycle()
    ps, env = ex._snap(), explore.ENV0
    results = []
    for case in cases:
        env = env._replace(fault="", rd_bad=False, marker=env.marker._replace(dead=""), out=())
        db0 = env.db
        r = dict.fromkeys(CHECKS, 0)
        arrived, applied, noread_used, removed, calls = 0, 0, False, False, 0
        while True:
            step = ps[ix["Step"]]
            if arrived == len(case.parts) and env.part is None and not env.gap and step == 10:
                break
            calls += 1
            if calls > CASE_LIMIT:
                raise RuntimeError(f"case {case.name!r} does not finish")
            want_event = "-"
            if env.part is None and not env.gap and arrived < len(case.parts) and step == 10:
                want_event = f"part {case.parts[arrived]} arrives"
                arrived += 1
                env = env._replace(fault="", rd_bad=False, marker=env.marker._replace(dead=""))   # one fault per part
            elif env.part is not None and case.fault == 12 and arrived == case.fault_part and step == case.arg and not removed:
                want_event, removed = FAULT_LABEL[12], True
                applied += 1
            active = case.fault if arrived == case.fault_part else 0
            events = [e for e in ex._events(env) if e[0] == want_event]
            if len(events) != 1:
                raise RuntimeError(f"event {want_event!r} is not possible in case {case.name!r}")
            _, env1, power = events[0]
            ps1 = ex._scan(ps, env1, power)
            if ps1[ix["Step"]] == 10 and step not in (10, 0):
                r["alarm_sum"] += ps1[ix["Alarm"]]
            o = {k: ps1[names.index(k)] for k in out_names}
            want = FAULT_LABEL.get(active) if active not in (0, 12) else None
            if active == 10 and noread_used:
                want = None
            (label, env2, viol, _released), hit = _choose(list(ex._react(env1, o)), want)
            if viol:
                raise RuntimeError(f"case {case.name!r} violates {viol}")
            applied += hit
            noread_used = noread_used or (hit and active == 10)
            if env1.part is not None and env2.part is not None and env2.part.starts > env1.part.starts:
                pn, var, _ = explore.TYPES[env1.part.ptype]
                r["mark_starts"] += 1
                r["job_sum"] += o["MkJobNo"]
                r["code_sum"] += code_checksum(pn, var, o["MkSerial"])
            ps, env = ps1, env2
        if case.fault and not applied:
            raise RuntimeError(f"the fault of case {case.name!r} never happened")
        exits = Counter(env.out)
        new = env.db - db0
        r.update(released=exits["released"], rejected=exits["rejected"], removed=exits["removed"],
                 serial_last=ps[ix["MkSerial"]],
                 good_records=sum(1 for _, s in new if s == 1), void_records=sum(1 for _, s in new if s == 2),
                 rec_serial_sum=sum(s for s, _ in new))
        results.append({"case": case, "calls": calls, **r})
    return results


# --------------------------------------------------------------------------
# SCL generation
# --------------------------------------------------------------------------
def _if_chain(var: str, branches: list[tuple[int, list[str]]], indent: str = "   ") -> list[str]:
    lines = []
    for i, (value, body) in enumerate(branches):
        lines.append(f"{indent}{'IF' if i == 0 else 'ELSIF'} #{var} = {value} THEN")
        lines += [f"{indent}   {b}" for b in body]
    lines.append(f"{indent}END_IF;")
    return lines


def generate_cases_scl(results: list[dict] | None = None) -> str:
    results = results if results is not None else reference()
    p = explore.PARAMS
    special = {job: (pn, var) for pn, var, job in explore.TYPES.values() if job > 0 and (pn, var) != (83700000 + job, job % 50)}
    head = f'''FUNCTION_BLOCK "FB_BenchCases"
TITLE = Scenario table and expected values of the test bench (generated)
{{ S7_Optimized_Access := 'TRUE' }}
AUTHOR : chad416
VERSION : 0.1
//  GENERATED by scripts/make_testbench.py from dmx/bench.py. Do not edit by hand.
//  The expected values are the outcomes of the Python model (dmx/explore.py with the station
//  block in the loop, dmx/ecc200.py for the code content), not of the SCL stand-ins.
//  CaseNo selects the case, PartIx the part inside the case, Row a row of the recipe table.
   VAR_INPUT
      CaseNo : Int;
      PartIx : Int;
      Row : Int;
   END_VAR

   VAR_OUTPUT
      CasesTotal : Int;
      NParts : Int;   // parts in this case
      FaultKind : Int;   // see "FB_SimMarker"; 10 no-read once, 11 no-read always, 12 part taken out, 13 slow record store
      FaultPart : Int;   // the part that gets the fault
      FaultArg : Int;   // fault 12: station step in which the part is taken out
      PartNo : DInt;   // part PartIx of this case
      VarCode : Int;
      RowPartNo : DInt;   // recipe row Row: the job of row n has the number n
      RowVarCode : Int;
      RowJob : Int;
      TmoLoad : Int;   // station time-outs for the bench, in calls
      TmoMark : Int;
      TmoRead : Int;
      TmoRec : Int;
      MaxReads : Int;
      ExpReleased : Int;
      ExpRejected : Int;
      ExpRemoved : Int;
      ExpAlarmSum : Int;
      ExpMarkStarts : Int;
      ExpJobSum : DInt;
      ExpSerialLast : DInt;
      ExpGood : Int;
      ExpVoid : Int;
      ExpRecSerialSum : DInt;
      ExpCodeSum : DInt;
   END_VAR

BEGIN
   #CasesTotal := {len(results)};
   #TmoLoad := {p["TmoLoad"]};
   #TmoMark := {p["TmoMark"]};
   #TmoRead := {p["TmoRead"]};
   #TmoRec := {p["TmoRec"]};
   #MaxReads := {p["MaxReads"]};

   REGION RecipeRow
   IF #Row >= 1 AND #Row <= {TABLE_ROWS} THEN
      #RowPartNo := 83700000 + INT_TO_DINT(#Row);
      #RowVarCode := #Row MOD 50;
      #RowJob := #Row;
   ELSE
      #RowPartNo := 0;
      #RowVarCode := 0;
      #RowJob := 0;
   END_IF;'''
    lines = [head]
    lines += _if_chain("Row", [(job, [f"#RowPartNo := {pn};", f"#RowVarCode := {var};"]) for job, (pn, var) in sorted(special.items())])
    lines += ["   END_REGION", "", "   REGION Cases",
              "   #NParts := 0;", "   #FaultKind := 0;", "   #FaultPart := 0;", "   #FaultArg := 0;", "   #PartNo := 0;", "   #VarCode := 0;",
              "   #ExpReleased := 0;", "   #ExpRejected := 0;", "   #ExpRemoved := 0;", "   #ExpAlarmSum := 0;", "   #ExpMarkStarts := 0;",
              "   #ExpJobSum := 0;", "   #ExpSerialLast := 0;", "   #ExpGood := 0;", "   #ExpVoid := 0;", "   #ExpRecSerialSum := 0;", "   #ExpCodeSum := 0;"]
    branches = []
    for n, r in enumerate(results, start=1):
        c = r["case"]
        body = [f"// {c.name}: parts {' '.join(c.parts)}",
                f"#NParts := {len(c.parts)};", f"#FaultKind := {c.fault};", f"#FaultPart := {c.fault_part};", f"#FaultArg := {c.arg};"]
        parts = []
        for i, t in enumerate(c.parts, start=1):
            pn, var, _ = explore.TYPES[t]
            parts.append((i, [f"#PartNo := {pn};", f"#VarCode := {var};"]))
        body += [ln[3:] for ln in _if_chain("PartIx", parts)]
        body += [f"#ExpReleased := {r['released']};", f"#ExpRejected := {r['rejected']};", f"#ExpRemoved := {r['removed']};",
                 f"#ExpAlarmSum := {r['alarm_sum']};", f"#ExpMarkStarts := {r['mark_starts']};", f"#ExpJobSum := {r['job_sum']};",
                 f"#ExpSerialLast := {r['serial_last']};", f"#ExpGood := {r['good_records']};", f"#ExpVoid := {r['void_records']};",
                 f"#ExpRecSerialSum := {r['rec_serial_sum']};", f"#ExpCodeSum := {r['code_sum']};"]
        branches.append((n, body))
    lines += _if_chain("CaseNo", branches)
    lines += ["   END_REGION", "END_FUNCTION_BLOCK", ""]
    return "\n".join(lines)


def expected_table_md(results: list[dict] | None = None) -> str:
    results = results if results is not None else reference()
    lines = ["# Test bench cases and expected values", "",
             "Generated by `python scripts/make_testbench.py` from `dmx/bench.py`. The same numbers are in `plc/test/FB_BenchCases.scl`.", "",
             "| Case | Scenario | Parts | Released | Rejected | Taken out | Alarm sum | Starts | Job sum | Last serial | Good rec. | Void rec. | Serial sum | Code checksum |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in enumerate(results, start=1):
        c = r["case"]
        lines.append(f"| {n} | {c.name} | {' '.join(c.parts)} | " + " | ".join(str(r[k]) for k in CHECKS) + " |")
    lines += ["", f"Calls of the bench block until done: 320 for the recipe table, then {sum(r['calls'] for r in results)} "
              f"station calls in the Python model plus one evaluation call per case.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the bench through the translator
# --------------------------------------------------------------------------
def load_bench(station_source: str | None = None, cases_source: str | None = None) -> dict:
    """All blocks of the bench, translated. The station or the case table can be replaced for tests."""
    sources = [p.read_text(encoding="utf-8") for p in sorted(explore.PLC_DIR.glob("FB_*.scl")) if p.name != "FB_MarkStation.scl"]
    sources.append(station_source if station_source is not None else (explore.PLC_DIR / "FB_MarkStation.scl").read_text(encoding="utf-8"))
    for name in BENCH_BLOCKS:
        if name == "FB_BenchCases" and cases_source is not None:
            sources.append(cases_source)
        else:
            sources.append((TEST_DIR / f"{name}.scl").read_text(encoding="utf-8"))
    code, _ = scl.translate(sources)
    ns: dict = {"_FB": {}}
    exec(compile(code, "<scl>", "exec"), ns)
    return dict(ns["_FB"])


def run_bench(station_source: str | None = None, cases_source: str | None = None, max_calls: int = 20000) -> dict:
    """Call FB_TestBench until Done, as OB1 would. Returns its outputs, plus the call count after Done."""
    bench = load_bench(station_source, cases_source)["FB_TestBench"]()
    calls = 0
    while not bench.Done and calls < max_calls:
        bench()
        calls += 1
    held = {k: getattr(bench, k) for k in ("Done", "AllPassed", "CasesTotal", "CasesRun", "CasesPassed", "CasesFailed",
                                             "FirstFailedCase", "FirstFailedCheck", "Calls")}
    held["CasePass"] = list(bench.CasePass)
    for _ in range(50):                      # the result must be held when the block keeps being called
        bench()
    again = {k: getattr(bench, k) for k in held if k != "CasePass"}
    again["CasePass"] = list(bench.CasePass)
    held["held"] = again == held
    held["python_calls"] = calls
    return held
