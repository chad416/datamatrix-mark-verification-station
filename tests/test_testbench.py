"""The PLC test bench (plc/test/), executed through the translator before it goes into PLCSIM."""

import re
from pathlib import Path

import pytest

from dmx import bench, explore

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "plc" / "test"
N = len(bench.CASES)


@pytest.fixture(scope="module")
def reference():
    return bench.reference()


def _text(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_generated_files_are_up_to_date(reference):
    assert _text(TEST_DIR / "FB_BenchCases.scl") == bench.generate_cases_scl(reference)
    assert _text(ROOT / "results" / "testbench_cases.md") == bench.expected_table_md(reference)


def test_scenarios_cover_what_was_asked(reference):
    assert {c.fault for c in bench.CASES} == set(range(0, 14))          # every fault kind of the bench is used
    assert {t for c in bench.CASES for t in c.parts} == {"A", "B", "C", "U"}
    assert {c.arg for c in bench.CASES if c.fault == 12} == {30, 40, 50, 60}
    by_name = {r["case"].name: r for r in reference}
    good = by_name["good cycle"]
    assert (good["released"], good["rejected"], good["alarm_sum"], good["job_sum"], good["serial_last"]) == (1, 0, 0, 205, 1)
    assert by_name["no-read once, second read succeeds"]["released"] == 1
    r = by_name["no-read on every attempt, then a good part"]
    assert (r["released"], r["rejected"], r["alarm_sum"], r["mark_starts"]) == (1, 1, 4, 2)   # never marked a second time
    assert by_name["marker does not load (time-out), then a good part"]["alarm_sum"] == 2
    assert by_name["changeover with a stale job, honest echo"]["mark_starts"] == 2           # stopped before the start
    assert by_name["changeover with a stale job, wrong echo"]["mark_starts"] == 3            # stopped by the read-back
    assert by_name["part taken out during marking"]["removed"] == 1
    assert by_name["mixed run of six parts"]["released"] == 5
    # every serial that was reserved is good or void; one serial (case 19) is both
    assert reference[-1]["serial_last"] == sum(r["good_records"] + r["void_records"] for r in reference) - 1
    assert sum(r["released"] for r in reference) == sum(r["good_records"] for r in reference) - 1


def test_bench_passes_in_the_translator(reference):
    r = bench.run_bench()
    assert r["Done"] and r["AllPassed"] and r["held"]
    assert (r["CasesTotal"], r["CasesRun"], r["CasesPassed"], r["CasesFailed"]) == (N, N, N, 0)
    assert (r["FirstFailedCase"], r["FirstFailedCheck"]) == (0, 0)
    assert r["CasePass"] == [True] * N + [False] * (32 - N)
    # same number of calls as the Python model: 320 recipe rows, the station calls, one evaluation per case
    assert r["Calls"] == r["python_calls"] == bench.TABLE_ROWS + sum(x["calls"] for x in reference) + N


FAILS = {       # removed safeguard -> must make the bench fail
    "no content compare after the read",
    "presence not monitored during the cycle",
    "release without waiting for the record",
    "no echo check before the start",
}


@pytest.mark.parametrize("name", sorted(FAILS))
def test_bench_fails_when_a_safeguard_is_removed(name):
    r = bench.run_bench(station_source=explore.mutant_source(name))
    assert r["Done"] and not r["AllPassed"] and r["CasesFailed"] >= 1 and r["held"]
    assert 1 <= r["FirstFailedCase"] <= N and 1 <= r["FirstFailedCheck"] <= 12
    assert r["CasePass"][r["FirstFailedCase"] - 1] is False
    assert r["CasesPassed"] + r["CasesFailed"] == r["CasesRun"] == N


def test_bench_reports_a_wrong_expected_value_and_a_hang(reference):
    src = bench.generate_cases_scl(reference)
    want = f"#ExpAlarmSum := {reference[5]['alarm_sum']};"
    head, tail = src.split("ELSIF #CaseNo = 6 THEN", 1)
    r = bench.run_bench(cases_source=head + "ELSIF #CaseNo = 6 THEN" + tail.replace(want, "#ExpAlarmSum := 0;", 1))
    assert (r["CasesRun"], r["CasesPassed"], r["CasesFailed"], r["FirstFailedCase"], r["FirstFailedCheck"]) == (N, N - 1, 1, 6, 4)
    station = _text(explore.PLC_DIR / "FB_MarkStation.scl")
    old = "IF #VoidPhase = 2 AND NOT #PartPresent THEN"
    assert station.count(old) == 1
    r = bench.run_bench(station_source=station.replace(old, "IF FALSE THEN"))      # the reject step never ends
    assert r["Done"] and not r["AllPassed"] and r["held"]
    assert (r["FirstFailedCase"], r["FirstFailedCheck"]) == (4, 13) and r["CasesRun"] == 4 < r["CasesTotal"]


def test_ob_and_result_db_use_names_that_exist():
    fb = _text(TEST_DIR / "FB_TestBench.scl")
    outputs = set(re.findall(r"^\s*(\w+)\s*:", fb.split("VAR_OUTPUT", 1)[1].split("END_VAR", 1)[0], flags=re.M))
    db = _text(TEST_DIR / "DB_TestResult.db")
    fields = set(re.findall(r"^\s*(\w+)\s*:", db.split("   VAR\n", 1)[1].split("END_VAR", 1)[0], flags=re.M))
    assert {"Done", "CasesTotal", "CasesRun", "CasesPassed", "CasesFailed", "FirstFailedCase", "FirstFailedCheck", "CasePass"} <= fields
    assert fields == outputs
    ob = _text(TEST_DIR / "OB_Main.scl")
    copies = re.findall(r'"DB_TestResult"\.(\w+) := "DB_TestBench"\.(\w+);', ob)
    assert {a for a, _ in copies} == fields and all(a == b for a, b in copies)
    assert '"DB_TestBench"();' in ob and '"FB_TestBench"' in _text(TEST_DIR / "DB_TestBench.db")
    for name in ("FB_TestBench", "FB_SimMarker", "FB_SimReader", "FB_SimRecords", "FB_BenchCases"):
        src = _text(TEST_DIR / f"{name}.scl")
        body = re.sub(r"//[^\n]*", "", src.split("BEGIN", 1)[1])
        outs = re.findall(r"^\s*(\w+)\s*:", src.split("VAR_OUTPUT", 1)[1].split("END_VAR", 1)[0], flags=re.M)
        assert "RETURN" not in body
        for o in outs:                                             # no block reads its own outputs
            assert re.findall(rf"(?<![.\w])#{o}\b(?!\s*(?::=|\[))", body) == [], (name, o)
