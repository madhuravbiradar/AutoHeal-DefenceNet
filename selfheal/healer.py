"""The autonomous control loop: MONITOR -> DETECT -> DIAGNOSE -> PLAN -> EXECUTE -> VERIFY."""
from collections import Counter
from dataclasses import dataclass, field
from .monitor import Monitor
from .diagnosis import diagnose
from .planner import Planner
from .executor import Executor
from .cisco_adapter import CiscoAdapter
from .routing import edge_keys

HOLD_DOWN, SUSPECT_TTL, COOLDOWN = 3, 30, 2


@dataclass
class Incident:
    key: str
    category: str
    subtype: str
    location: str
    severity: int
    confidence: float
    cause: str
    detect_tick: int
    status: str = "OPEN"          # OPEN -> MITIGATED / MANUAL_ACTION_REQUIRED -> CLOSED
    plan_tick: int = None
    plan_name: str = ""
    mitigated_tick: int = None
    closed_tick: int = None
    affected: set = field(default_factory=set)
    recommendation: str = ""


class SelfHealingEngine:
    def __init__(self, net, verbose=True, live_cisco=False):
        self.net, self.verbose = net, verbose
        self.monitor = Monitor(net)
        self.adapter = CiscoAdapter(net.g, live=live_cisco)
        self.executor = Executor(net, self.adapter)
        self.incidents, self.events, self.suspects = {}, [], {}
        self.last_version, self.cooldown, self.reopt_at, self.ignored = -1, 0, None, set()

    def log(self, t, stage, msg):
        self.events.append({"tick": t, "stage": stage, "msg": msg})
        if self.verbose:
            print(f"[t={t:03d}] {stage:<9} {msg}")

    # ------------------------------------------------------------------
    def tick(self, stats, t):
        view = self.monitor.observe(stats, t)
        nodes = [c for c in view.changes if c[1] == "NODE"]
        links = [c for c in view.changes if c[1] == "LINK"]
        for _, _, key, state in nodes:
            self.log(t, "DETECT", f"node {key} -> {state}")
        if len(links) <= 3:
            for _, _, key, state in links:
                self.log(t, "DETECT", "link %s-%s -> %s" % (key + (state,)))
        elif links:
            self.log(t, "DETECT", f"{len(links)} link state changes (correlated; root cause analysis follows)")
        if any(c[3] == "OK" for c in view.changes):
            self.reopt_at = t + HOLD_DOWN
        for e in [e for e, t0 in self.suspects.items() if t - t0 >= SUSPECT_TTL]:
            del self.suspects[e]
        flows = list(self.net.flows.values())
        bad = {f for f, s in view.bad_streak.items() if s >= 2 and f not in self.ignored}
        due = self.reopt_at is not None and t >= self.reopt_at
        if view.changes or due or (bad and t >= self.cooldown):
            self._heal(view, flows, bad, due, t)
        self.last_version = view.version
        self._verify(view, t)

    def _util(self, flows):
        load = {}
        for f in flows:
            if f.path:
                for k in edge_keys(f.path):
                    load[k] = load.get(k, 0) + f.demand * f.rate
        return {k: v / self.net.g.edges[k]["bw"] for k, v in load.items()}

    def _heal(self, view, flows, bad, due, t):
        dx = diagnose(self.net.g, view, flows, self._util(flows), set(self.suspects))
        for e in dx.new_suspects:
            self.suspects[e] = t
        self.ignored = set(dx.unrecoverable)
        self._register(dx, view, t)
        affected = (set(dx.hit_flows) | bad) - self.ignored
        if due:
            affected |= {f.id for f in flows if (f.path != f.home_path or f.dst != f.orig_dst or f.rate < 1)} - self.ignored
            self.reopt_at = None
        if not affected:
            return
        best, plans, cur = Planner(self.net.g, view, flows, set(self.suspects)).plan(affected)
        alts = ", ".join(f"{p.name}={p.score:.0f}" for p in plans)
        if not best.actions or best.score <= cur.score + 1e-6:
            self.log(t, "PLAN", f"no better option than current state ({cur.score:.0f}); holding. [{alts}]")
            self.cooldown = t + 5
            return
        self.log(t, "PLAN", f"chose {best.name} (predicted service {best.metrics['served']:.1%} vs {cur.metrics['served']:.1%} now, "
                            f"{best.metrics['standby_used']} new standby link(s), {best.metrics['failovers']} flow(s) on replica servers)")
        self.log(t, "PLAN", f"alternatives: {alts}")
        kinds = Counter(a.kind for a in best.actions)
        ok, info = self.executor.execute(best)
        if not ok:
            self.log(t, "ROLLBACK", f"execution failed ({info}); control state restored")
            return
        links = [a for a in best.actions if a.kind in ("ACTIVATE_LINK", "DEACTIVATE_LINK")]
        for a in links:
            self.log(t, "EXECUTE", f"{a.kind} {a.target}  ({a.reason})")
        self.log(t, "EXECUTE", f"{dict(kinds)} applied, {info} CLI lines generated (dry-run Cisco IOS)")
        touched = [a.target for a in best.actions if a.target in self.net.flows]
        self.monitor.reset_flows(touched)
        self.cooldown = t + COOLDOWN
        for inc in self.incidents.values():
            if inc.status == "OPEN" and inc.plan_tick is None and (inc.affected & set(touched) or not inc.affected):
                inc.plan_tick, inc.plan_name = t, best.name

    def _register(self, dx, view, t):
        keys = set()
        for f in dx.faults:
            keys.add(f.key)
            inc = self.incidents.get(f.key)
            if inc is None or inc.status == "CLOSED":
                inc = self.incidents[f.key] = Incident(f.key, f.category, f.subtype, f.location, f.severity,
                                                       f.confidence, f.cause, t)
                self.log(t, "DIAGNOSE", f"{f.category} @ {f.location} | {f.subtype} | severity {f.severity}/5 | "
                                        f"confidence {f.confidence:.0%} | {len(f.affected)} flows hit")
                self.log(t, "DIAGNOSE", f"  cause: {f.cause}")
                for ev in f.evidence:
                    self.log(t, "DIAGNOSE", f"  evidence: {ev}")
                if dx.partitions:
                    self.log(t, "DIAGNOSE", f"  core partitioned into {len(dx.partitions)} islands; "
                                            f"{'healable by standby links' if dx.partition_healable else 'NOT healable remotely'}")
            inc.affected |= f.affected
            inc.severity = max(inc.severity, f.severity)
        if dx.isolated_nodes:
            for inc in self.incidents.values():
                if inc.status == "OPEN" and inc.category == "NODE_DOWN" and set(inc.affected) <= self.ignored and inc.affected:
                    inc.recommendation = (f"{len(dx.isolated_nodes)} single-homed hosts isolated; no remote path exists. "
                                          f"Dispatch repair for {inc.location}.")
        for key, inc in self.incidents.items():
            if inc.status != "CLOSED" and key not in keys:
                inc.status, inc.closed_tick = "CLOSED", t
                self.log(t, "RESOLVE", f"{key} cleared (3 consecutive healthy probes); fail-back after {HOLD_DOWN}s hold-down")
        if dx.risks:
            self.last_risks = dx.risks

    def _verify(self, view, t):
        for inc in self.incidents.values():
            if inc.status != "OPEN":
                continue
            rec = inc.affected - self.ignored
            if inc.affected and not rec:
                inc.status, inc.mitigated_tick = "MANUAL_ACTION_REQUIRED", t
                inc.recommendation = inc.recommendation or "No remote recovery path; dispatch field repair."
                self.log(t, "VERIFY", f"{inc.key}: {len(inc.affected)} flows unrecoverable remotely -> MANUAL ACTION REQUIRED")
            elif not inc.affected or (inc.plan_tick is not None and t > inc.plan_tick and
                                      sum(view.flow_ratio[f] for f in rec) / len(rec) >= 0.95):
                inc.status, inc.mitigated_tick = "MITIGATED", t
                self.log(t, "VERIFY", f"{inc.key}: service restored ({len(rec)} flows >=95% delivery)")
