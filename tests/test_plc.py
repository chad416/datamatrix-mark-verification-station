"""The SCL blocks, executed from the source text that goes into TIA Portal, and the state-space search."""

import random
from pathlib import Path

import pytest

from dmx import ecc200, explore, scl

ROOT = Path(__file__).resolve().parents[1]
FB = scl.load(sorted((ROOT / "plc").glob("FB_*.scl")))


def test_code_content_matches_the_python_encoder():
    fb = FB["FB_CodeContent"]()
    rnd = random.Random(1)
    cases = [(0, 0, 0), (99999999, 99, 999999999), (83702051, 17, 12345)]
    cases += [(rnd.randrange(10 ** 8), rnd.randrange(100), rnd.randrange(10 ** 9)) for _ in range(300)]
    for pn, var, ser in cases:
        fb.PartNo, fb.VarCode, fb.Serial = pn, var, ser
        fb()
        text = f"{pn:08d}{var:02d}{ser:09d}"
        assert fb.Valid and fb.Cw == ecc200.pad(ecc200.encode_ascii(text), 12), text
        assert ecc200.encode(text, 16).codewords[:12] == fb.Cw
    for bad in ((-1, 0, 0), (10 ** 8, 0, 0), (1, 100, 0), (1, 0, 10 ** 9)):
        fb.PartNo, fb.VarCode, fb.Serial = bad
        fb()
        assert not fb.Valid and fb.Cw == [0] * 12


def _filled_table():
    js = FB["FB_JobSelect"]()
    js.WrEnable = True
    for i in range(1, 321):
        js.WrIndex, js.WrPartNo, js.WrVariant, js.WrJob = i, 83700000 + i, i % 50, i
        js()
    js.WrEnable = False
    return js


def test_job_select_over_320_rows():
    js = _filled_table()
    for i in (1, 77, 205, 320):
        js.PartNo, js.VarCode = 83700000 + i, i % 50
        js()
        assert (js.JobNo, js.Found, js.Ambiguous, js.Rows) == (i, True, False, 320)
    js.PartNo, js.VarCode = 83700205, 6                       # right part number, wrong variant
    js()
    assert (js.JobNo, js.Found) == (0, False)
    js.WrEnable, js.WrIndex, js.WrPartNo, js.WrVariant, js.WrJob = True, 10, 83700205, 205 % 50, 300   # a second row for the same part
    js.PartNo, js.VarCode = 83700205, 205 % 50
    js()
    assert (js.JobNo, js.Found, js.Ambiguous) == (0, False, True)
    js.power_cycle()                                           # the table is retentive
    js.PartNo, js.VarCode = 83700077, 77 % 50
    js()
    assert (js.JobNo, js.Found, js.Rows) == (77, True, 320)


class Bench:
    """Minimal well-behaved marker, reader and record store for a hand-driven cycle."""

    def __init__(self):
        self.s = FB["FB_MarkStation"]()
        self.records, self.marked = [], []

    def scan(self, present=True):
        s = self.s
        for k, v in explore.PARAMS.items():        # the caller writes every input in every cycle
            setattr(s, k, v)
        s.PartNo, s.VarCode, s.JobNo, s.JobFound = 83702051, 17, 205, True
        s.PartPresent = present
        s()
        s.MkLoaded, s.MkJobEcho, s.MkSerialEcho = s.MkLoad, s.MkJobNo, s.MkSerial
        if s.MkStart and not s.MkDone:
            self.marked.append(s.MkSerial)
        s.MkDone = s.MkStart
        s.RdDone = s.RdTrigger
        s.RdOk, s.RdPartNo, s.RdVariant, s.RdSerial = s.RdTrigger, 83702051, 17, self.marked[-1] if self.marked else 0
        if s.RecReq and not s.RecAck:
            self.records.append((s.RecSerial, s.RecStatus))
        s.RecAck = s.RecReq
        return s.Step


def test_good_cycle_and_retentive_serial():
    b = Bench()
    steps = [b.scan() for _ in range(9)]
    assert steps == [10, 20, 30, 40, 50, 60, 65, 65, 65]
    assert b.s.Release and not b.s.Reject and b.records == [(1, 1)] and b.marked == [1]
    assert b.scan(present=False) == 10 and b.s.InProg == 0
    b.s.power_cycle()                                          # warm restart: counter survives, sequence does not
    assert b.s.NextSerial == 2 and b.s.Step == 0 and b.s.InProg == 0
    for _ in range(8):
        b.scan()
    assert b.s.Release and b.records == [(1, 1), (2, 1)] and b.marked == [1, 2]


def test_restart_with_open_cycle_never_marks_again():
    b = Bench()
    for _ in range(4):
        b.scan()
    assert b.s.Step == 40 and b.marked == [1] and b.s.InProg == 2
    b.s.power_cycle()
    b.s.MkLoaded = b.s.MkDone = b.s.RdDone = b.s.RecAck = False
    seen = [b.scan() for _ in range(6)]
    assert seen[0] == 80 and set(seen[1:]) == {70}
    assert b.s.Reject and not b.s.Release and b.marked == [1] and b.records == [(1, 2)]
    assert b.scan(present=False) == 10 and b.s.NextSerial == 2


def test_exhaustive_search_finds_no_violation():
    r = explore.explore(max_parts=3)
    assert r.complete and r.violations == []
    assert r.states > 3000 and r.releases > 0
    assert set(r.steps_seen) == {0, 10, 20, 30, 40, 50, 51, 60, 65, 70, 80}     # every step of the block is reached
    assert set(explore.FAULTS) <= set(r.by_fault)                               # every fault class is injected


EXPECTED = {
    "serial counter not retentive": "P3",
    "no content compare after the read": "P2",
    "release without waiting for the record": "P3",
    "open cycle ignored after a restart": "P4",
    "presence not monitored during the cycle": "P1",
    "serial counted up at release, not at reserve": "P3",
    "no echo check before the start": None,        # second line of defence: the read-back compare still stops it
}


@pytest.mark.parametrize("name", list(explore.MUTANTS))
def test_search_finds_a_counterexample_when_a_safeguard_is_removed(name):
    r = explore.explore(explore.mutant_source(name), max_parts=3)
    if EXPECTED[name] is None:
        assert r.complete and r.violations == []
    else:
        assert r.violations and r.violations[0][0].startswith(EXPECTED[name])
        assert len(r.violations[0][1]) > 3


def test_reserved_words_are_refused():
    with pytest.raises(scl.SclError):
        scl.translate(['FUNCTION_BLOCK "X"\n VAR_INPUT\n Variant : Int;\n END_VAR\nBEGIN\nEND_FUNCTION_BLOCK\n'])


def test_block_never_reads_its_own_outputs():
    # TIA Portal warns "might not be initialized" when a block reads its own VAR_OUTPUT.
    # The sequence state lives in the static SeqStep; Step is a copy written once, at the end.
    import re
    src = (ROOT / "plc" / "FB_MarkStation.scl").read_text(encoding="utf-8")
    body = re.sub(r"//[^\n]*", "", src.split("BEGIN", 1)[1])
    outputs = re.findall(r"^\s*(\w+)\s*:", src.split("VAR_OUTPUT", 1)[1].split("END_VAR", 1)[0], flags=re.M)
    assert "Step" in outputs and "RETURN" not in body
    for name in outputs:
        uses = re.findall(rf"#{name}\b(?!\s*:=)", body)
        assert uses == [], f"output #{name} is read {len(uses)} time(s)"
    assert len(re.findall(r"#Step\s*:=\s*#SeqStep\s*;", body)) == 1
    b = Bench()
    for present in [True] * 9 + [False] * 2 + [True] * 5:
        b.scan(present)
        assert b.s.Step == b.s.SeqStep
