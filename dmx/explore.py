"""Exhaustive exploration of the marking handshake with the real SCL block in the loop.

The controller is plc/FB_MarkStation.scl, executed call by call through dmx/scl.py.
Around it is a small model of the marker, the reader, the record store, the part
and the supply. The model is non-deterministic: in every controller cycle it may
let a part arrive, and, at most once per run, inject one fault. A breadth-first
search visits every reachable combined state and checks the safety properties on
every transition. Because the search is breadth-first, a violated property comes
with a shortest trace.

Faults (one per run):
  marker_no_load   the marker never acknowledges the job
  marker_hang      after the start command the marker never reports done; the
                   part is left unmarked, partly marked or completely marked
  marker_fault     the marker raises its fault signal during marking (partial mark)
  stale_job        after a load the marker still holds the previous job; its echo
                   is either honest (old number) or wrong (new number)
  stale_serial     the same for the serial number
  stale_both       the marker ignored the whole load telegram
  reader_no_read   the reader returns "no read", once or every time
  part_removed     the part is taken out of the nest in any cycle
  power_loss       the controller restarts in any cycle; a mark in progress is
                   finished or cut off

Properties checked:
  P1  a released part carries the complete code that belongs to its part type
  P2  no two released parts carry the same serial number
  P3  a released part has a "good" record and no "void" record
  P4  no part is given a second start command
  P5  when the station is idle and empty, every serial number the controller has
      reserved is either on a released part or has a "void" record

Not covered: two faults in one run, a power loss in the middle of a controller
cycle, parts that follow each other with a gap shorter than one cycle, a reader
that returns a wrong but valid code, real times (time-outs are shortened to a few
cycles), and the difference between this translator and the TIA Portal compiler.
"""

from __future__ import annotations

from collections import Counter, deque, namedtuple
from dataclasses import dataclass, field
from pathlib import Path

from . import scl

ROOT = Path(__file__).resolve().parents[1]
PLC_DIR = ROOT / "plc"

# part type -> (part number, variant, marking job). Two rows of the recipe table are enough
# to cover "same type again" and "changeover".
TYPES = {"A": (83702051, 17, 205), "B": (83702052, 4, 206),
         "C": (83700017, 17, 17),        # an ordinary row of the table; used by the PLC test bench only
         "U": (12345678, 1, 0)}          # a part number that is not in the table; test bench only
ARRIVAL_TYPES = ("A", "B")               # the part types the state-space search lets arrive
JOB_CONTENT = {job: (pn, var) for pn, var, job in TYPES.values() if job > 0}
PARAMS = {"TmoLoad": 3, "TmoMark": 4, "TmoRead": 3, "TmoRec": 3, "MaxReads": 2}
MARK_CYCLES = (1, 2, 3)        # controller cycles a mark can take (time-out: 4)
FAULTS = ("marker_no_load", "marker_hang", "marker_fault", "stale_job", "stale_serial", "stale_both",
          "reader_no_read", "part_removed", "power_loss")

Part = namedtuple("Part", "ptype mark starts")              # mark: None | ("ok", pn, var, serial) | ("partial",)
Marker = namedtuple("Marker", "job serial loaded echo_job echo_serial busy done fault dead")
Rd = namedtuple("Rd", "done ok pn var serial")
Env = namedtuple("Env", "n_parts part gap marker rd db ack released out fault rd_bad rec_wait")

MARKER0 = Marker(0, 0, False, 0, 0, -1, False, False, "")
RD0 = Rd(False, False, 0, 0, 0)
ENV0 = Env(0, None, False, MARKER0, RD0, frozenset(), False, frozenset(), (), "", False, False)


def load_blocks(station_source: str | None = None) -> dict:
    sources = [p.read_text(encoding="utf-8") for p in sorted(PLC_DIR.glob("FB_*.scl")) if p.name != "FB_MarkStation.scl"]
    sources.append(station_source if station_source is not None else (PLC_DIR / "FB_MarkStation.scl").read_text(encoding="utf-8"))
    code, _ = scl.translate(sources)
    ns: dict = {"_FB": {}}
    exec(compile(code, "<scl>", "exec"), ns)
    return dict(ns["_FB"])


def job_table(blocks: dict) -> dict:
    """Run the real FB_JobSelect on a 320-row table and keep the answers for the part types used here."""
    js = blocks["FB_JobSelect"]()
    js.WrEnable = True
    for i in range(1, 321):                          # 320 rows; rows 205 and 206 are the two types explored
        pn, var = next(((p, v) for p, v, j in TYPES.values() if j == i), (83700000 + i, i % 50))
        js.WrIndex, js.WrPartNo, js.WrVariant, js.WrJob = i, pn, var, i
        js()
    js.WrEnable = False
    out = {}
    for t, (pn, var, _job) in TYPES.items():
        js.PartNo, js.VarCode = pn, var
        js()
        out[t] = (js.JobNo, js.Found)
    return out


@dataclass
class Result:
    states: int = 0
    transitions: int = 0
    depth: int = 0
    releases: int = 0
    violations: list = field(default_factory=list)       # (property, trace)
    by_fault: Counter = field(default_factory=Counter)   # reachable states per injected fault ("" = none yet)
    outcomes: Counter = field(default_factory=Counter)   # (fault, exits of the parts) of quiescent end states
    steps_seen: Counter = field(default_factory=Counter)
    complete: bool = True

    @property
    def ok(self) -> bool:
        return self.complete and not self.violations


class Explorer:
    def __init__(self, station_source: str | None = None, max_parts: int = 3, faults=FAULTS,
                 max_states: int = 3_000_000, stop_at_first: bool = True):
        self.blocks = load_blocks(station_source)
        self.jobs = job_table(self.blocks)
        self.plc = self.blocks["FB_MarkStation"]()
        self.names = tuple(k for k in vars(self.plc) if k not in self.plc.INPUTS)
        self.max_parts, self.faults, self.max_states, self.stop_at_first = max_parts, tuple(faults), max_states, stop_at_first
        self.arrival_types = ARRIVAL_TYPES

    # -- controller ---------------------------------------------------------
    def _restore(self, ps):
        for k, v in zip(self.names, ps):
            setattr(self.plc, k, v)

    def _snap(self):
        return tuple(getattr(self.plc, k) for k in self.names)

    def _scan(self, ps, env: Env, power_cycle: bool):
        p = self.plc
        self._restore(ps)
        if power_cycle:
            p.power_cycle()
        for k, v in PARAMS.items():
            setattr(p, k, v)
        p.PartPresent = env.part is not None
        if env.part is not None:
            pn, var, _ = TYPES[env.part.ptype]
            p.PartNo, p.VarCode = pn, var
            p.JobNo, p.JobFound = self.jobs[env.part.ptype]
        else:
            p.PartNo, p.VarCode, p.JobNo, p.JobFound = 0, 0, 0, False
        m, r = env.marker, env.rd
        p.MkLoaded, p.MkJobEcho, p.MkSerialEcho, p.MkDone, p.MkFault = m.loaded, m.echo_job, m.echo_serial, m.done, m.fault
        p.RdDone, p.RdOk, p.RdPartNo, p.RdVariant, p.RdSerial = r.done, r.ok, r.pn, r.var, r.serial
        p.RecAck = env.ack
        p()
        return self._snap()

    # -- environment --------------------------------------------------------
    def _events(self, env: Env):
        """Things that can happen before a controller cycle: (label, env, power_cycle)."""
        yield "-", env, False
        if env.part is None and not env.gap and env.n_parts < self.max_parts:
            for t in self.arrival_types:
                yield f"part {t} arrives", env._replace(part=Part(t, None, 0), n_parts=env.n_parts + 1), False
        if env.fault:
            return
        if "part_removed" in self.faults and env.part is not None:
            part = env.part
            if env.marker.busy > 0:
                part = part._replace(mark=("partial",))
            yield "FAULT part removed by hand", env._replace(part=None, gap=True, out=env.out + ("removed",),
                                                             fault="part_removed"), False
        if "power_loss" in self.faults:
            m = env.marker
            base = env._replace(fault="power_loss", rd=RD0, ack=False, rec_wait=False)
            idle = m._replace(loaded=False, busy=-1, done=False, fault=False)
            if m.busy > 0 and env.part is not None:
                done = env.part._replace(mark=("ok",) + JOB_CONTENT[m.job] + (m.serial,))
                yield "FAULT power loss, marker finishes the mark", base._replace(marker=idle, part=done), True
                cut = env.part._replace(mark=("partial",))
                yield "FAULT power loss, mark cut off", base._replace(marker=idle, part=cut), True
            else:
                yield "FAULT power loss", base._replace(marker=idle), True

    def _react(self, env: Env, o):
        """Reaction of marker, reader, record store and part to the controller outputs.
        Yields (label, env, violations, released_now)."""
        for lab_m, marker, part, fault in self._marker(env, o):
            e1 = env._replace(marker=marker, part=part, fault=fault)
            for lab_r, rd, fault2, rd_bad in self._reader(e1, o):
                e0 = e1._replace(rd=rd, fault=fault2, rd_bad=rd_bad, gap=False)
                # record store: writes on a request, at once or one cycle later
                if o["RecReq"] and not e0.ack:
                    stores = [("", e0._replace(db=e0.db | {(o["RecSerial"], o["RecStatus"])}, ack=True, rec_wait=False))]
                    if not e0.rec_wait:
                        stores.append(("record store slow", e0._replace(rec_wait=True)))
                elif not o["RecReq"]:
                    stores = [("", e0._replace(ack=False, rec_wait=False))]
                else:
                    stores = [("", e0)]
                for lab_s, e in stores:
                    yield self._leave(" ".join(x for x in (lab_m, lab_r, lab_s) if x), e, o)

    @staticmethod
    def _leave(label, e: Env, o):
                viol = []
                if e.part is not None and e.part.starts > 1:
                    viol.append("P4 part got a second start command")
                # part leaves
                released = False
                if e.part is not None and o["Release"]:
                    released = True
                    pt = e.part
                    pn, var, _ = TYPES[pt.ptype]
                    serial = pt.mark[3] if pt.mark and pt.mark[0] == "ok" else None
                    if not (pt.mark and pt.mark[0] == "ok" and pt.mark[1:3] == (pn, var)):
                        viol.append("P1 released part does not carry the complete code of its type")
                    if serial is not None and serial in e.released:
                        viol.append("P2 released part repeats a serial number")
                    if serial is None or (serial, 1) not in e.db or (serial, 2) in e.db:
                        viol.append("P3 released part without a clean good record")
                    if pt.starts != 1:
                        viol.append("P4 released part was not started exactly once")
                    e = e._replace(part=None, gap=True, out=e.out + ("released",),
                                   released=e.released | ({serial} if serial is not None else set()))
                elif e.part is not None and o["Reject"]:
                    e = e._replace(part=None, gap=True, out=e.out + ("rejected",))
                if o["Release"] and o["Reject"]:
                    viol.append("P6 release and reject at the same time")
                return label, e, viol, released

    def _marker(self, env: Env, o):
        m, part, fault = env.marker, env.part, env.fault
        free = not fault
        if m.dead == "no_load":
            yield "", m._replace(loaded=False), part, fault
            return
        if not o["MkLoad"]:
            m = m._replace(loaded=False, fault=False)
        if not o["MkStart"]:
            if m.busy > 0:                              # start dropped while marking: the marker stops
                if part is not None:
                    part = part._replace(mark=("partial",))
                m = m._replace(busy=-1)
            m = m._replace(done=False)
        # --- load
        if o["MkLoad"] and not m.loaded and not m.fault:
            req_job, req_ser = o["MkJobNo"], o["MkSerial"]
            ok = m._replace(job=req_job, serial=req_ser, echo_job=req_job, echo_serial=req_ser, loaded=True)
            yield "", ok, part, fault
            if free:
                if "marker_no_load" in self.faults:
                    yield "FAULT marker does not load", m._replace(dead="no_load"), part, "marker_no_load"
                if "stale_job" in self.faults and m.job not in (0, req_job):
                    st = ok._replace(job=m.job)
                    yield "FAULT stale job, honest echo", st._replace(echo_job=m.job), part, "stale_job"
                    yield "FAULT stale job, wrong echo", st, part, "stale_job"
                if "stale_serial" in self.faults and m.serial not in (0, req_ser):
                    st = ok._replace(serial=m.serial)
                    yield "FAULT stale serial, honest echo", st._replace(echo_serial=m.serial), part, "stale_serial"
                    yield "FAULT stale serial, wrong echo", st, part, "stale_serial"
                if "stale_both" in self.faults and m.job != 0 and m.serial not in (0, req_ser):
                    st = ok._replace(job=m.job, serial=m.serial)
                    yield "FAULT stale job and serial, honest echo", st._replace(echo_job=m.job, echo_serial=m.serial), part, "stale_both"
                    yield "FAULT stale job and serial, wrong echo", st, part, "stale_both"
            return
        # --- start
        if o["MkStart"] and m.loaded and not m.done and m.busy < 0 and not m.fault and m.dead != "hang":
            started = part._replace(starts=part.starts + 1) if part is not None else None
            for cycles in MARK_CYCLES:              # the marking time is not fixed
                yield (f"mark takes {cycles}" if started is not None else ""), m._replace(busy=cycles), started, fault
            if free and started is not None:
                content = ("ok",) + JOB_CONTENT[m.job] + (m.serial,)
                if "marker_hang" in self.faults:
                    hung = m._replace(dead="hang")
                    yield "FAULT marker hangs, no mark", hung, started, "marker_hang"
                    yield "FAULT marker hangs, partial mark", hung, started._replace(mark=("partial",)), "marker_hang"
                    yield "FAULT marker hangs, mark complete", hung, started._replace(mark=content), "marker_hang"
                if "marker_fault" in self.faults:
                    yield "FAULT marker fault signal", m._replace(fault=True), started._replace(mark=("partial",)), "marker_fault"
            return
        # --- marking runs
        if m.busy > 0:
            if m.busy == 1:
                if part is not None:
                    part = part._replace(mark=("ok",) + JOB_CONTENT[m.job] + (m.serial,))
                m = m._replace(busy=-1, done=True)
            else:
                m = m._replace(busy=m.busy - 1)
        yield "", m, part, fault

    def _reader(self, env: Env, o):
        rd, fault = env.rd, env.fault
        if not o["RdTrigger"]:
            yield "", RD0, fault, env.rd_bad
            return
        if rd.done:
            yield "", rd, fault, env.rd_bad
            return
        mk = env.part.mark if env.part is not None else None
        good = Rd(True, True, mk[1], mk[2], mk[3]) if mk and mk[0] == "ok" else Rd(True, False, 0, 0, 0)
        yield "", good, fault, env.rd_bad
        if good.ok and "reader_no_read" in self.faults and (not fault or env.rd_bad):
            yield "FAULT reader no-read", Rd(True, False, 0, 0, 0), "reader_no_read", True

    # -- search -------------------------------------------------------------
    def run(self) -> Result:
        res = Result()
        self.plc.power_cycle()
        start = (self._snap(), ENV0)
        parent = {start: None}
        queue = deque([(start, 0)])
        i_step = self.names.index("Step")
        i_next = self.names.index("NextSerial")
        out_names = ("MkLoad", "MkStart", "MkJobNo", "MkSerial", "RdTrigger", "RecReq", "RecSerial", "RecStatus", "Release", "Reject")
        i_out = [self.names.index(k) for k in out_names]
        while queue:
            state, depth = queue.popleft()
            ps, env = state
            res.depth = max(res.depth, depth)
            res.by_fault[env.fault] += 1
            res.steps_seen[ps[i_step]] += 1
            quiet = ps[i_step] == 10 and env.part is None and not env.gap
            if quiet:
                missing = [s for s in range(1, ps[i_next]) if s not in env.released and (s, 2) not in env.db]
                if missing:
                    res.violations.append(("P5 reserved serial neither released nor void", self._trace(parent, state)))
                    if self.stop_at_first:
                        break
                if env.n_parts == self.max_parts:
                    res.outcomes[(env.fault or "none", tuple(sorted(Counter(env.out).items())))] += 1
            for ev_label, env1, cycle in self._events(env):
                ps1 = self._scan(ps, env1, cycle)
                o = {k: ps1[i] for k, i in zip(out_names, i_out)}
                for re_label, env2, viol, released in self._react(env1, o):
                    res.transitions += 1
                    res.releases += released
                    nxt = (ps1, env2)
                    label = f"{ev_label:<44} step {ps1[i_step]:>2}  {re_label}".rstrip()
                    if nxt not in parent:
                        parent[nxt] = (state, label)
                        if not viol:
                            queue.append((nxt, depth + 1))
                    if viol:
                        if nxt not in parent or parent[nxt] is None:
                            parent[nxt] = (state, label)
                        res.violations.append((viol[0], self._trace(parent, state) + [label]))
                        if self.stop_at_first:
                            queue.clear()
                            break
                if res.violations and self.stop_at_first:
                    break
            if len(parent) > self.max_states:
                res.complete = False
                break
        res.states = len(parent)
        return res

    @staticmethod
    def _trace(parent, state) -> list[str]:
        lines = []
        while parent.get(state) is not None:
            state, label = parent[state]
            lines.append(label)
        return lines[::-1]


# --------------------------------------------------------------------------
# mutants: the same search must find a counterexample when a safeguard is removed
# --------------------------------------------------------------------------
MUTANTS = {
    "serial counter not retentive": (
        "   VAR RETAIN\n      NextSerial : DInt := 1;\n",
        "   VAR\n      NextSerial : DInt := 1;\n   END_VAR\n\n   VAR RETAIN\n"),
    "no content compare after the read": (
        "IF #RdOk AND #RdPartNo = #CurPartNo AND #RdVariant = #CurVariant AND #RdSerial = #Serial THEN",
        "IF #RdOk THEN"),
    "release without waiting for the record": (
        "      IF #RecAck THEN\n         #InProg := 3;",
        "      IF TRUE THEN\n         #InProg := 3;"),
    "open cycle ignored after a restart": (
        "      IF #InProg <> 0 THEN\n         #SeqStep := 80;",
        "      IF FALSE THEN\n         #SeqStep := 80;"),
    "presence not monitored during the cycle": (
        "IF #SeqStep >= 20 AND #SeqStep <= 60 AND NOT #PartPresent THEN",
        "IF #SeqStep >= 20 AND #SeqStep <= 60 AND FALSE THEN"),
    "serial counted up at release, not at reserve": None,      # two edits, see mutant_source
    "no echo check before the start": (
        "ELSIF #MkLoaded AND NOT #MkDone AND #MkJobEcho = #Job AND #MkSerialEcho = #Serial THEN",
        "ELSIF #MkLoaded AND NOT #MkDone THEN"),
}


def mutant_source(name: str) -> str:
    src = (PLC_DIR / "FB_MarkStation.scl").read_text(encoding="utf-8").replace("\r\n", "\n")
    if name == "serial counted up at release, not at reserve":
        edits = [("      #NextSerial := #NextSerial + 1;\n      #InProg := 1;", "      #InProg := 1;"),
                 ("      IF #RecAck THEN\n         #InProg := 3;", "      IF #RecAck THEN\n         #NextSerial := #NextSerial + 1;\n         #InProg := 3;")]
    else:
        edits = [MUTANTS[name]]
    for old, new in edits:
        if src.count(old) != 1:
            raise ValueError(f"mutant {name!r}: pattern not found exactly once")
        src = src.replace(old, new)
    return src


def explore(station_source: str | None = None, **kw) -> Result:
    return Explorer(station_source, **kw).run()
