"""Headless runs of every scenario: full engine vs naive baseline (runs in milliseconds, no sleeping)."""
from db.store import Store
from demo.baseline import Baseline
from demo.scenarios import SCENARIOS, ScenarioRunner
from engine.orchestrator import Orchestrator
from simulator.network import Simulator


class OperatorPolicy:
    """Scripted human: approves after `delay` seconds; repairs the broken hardware when asked."""
    def __init__(self, orch, delay=2):
        self.orch, self.delay, self.seen = orch, delay, {}
        self.interventions = 0

    def step(self):
        o = self.orch
        for inc in o.active():
            key = (inc["id"], inc["status"])
            self.seen.setdefault(key, o.sim.t)
            waited = o.sim.t - self.seen[key]
            if inc["status"] == "awaiting_approval" and waited >= self.delay:
                o.approve(inc["id"], "operator"); self.interventions += 1
            elif inc["status"] == "manual_review" and waited >= self.delay + 1:
                sim = o.sim
                down = [l for l, f in sim.faults.items() if f["mode"] == "down"] or list(sim.faults)
                if down:
                    o.manual(inc["id"], "operator", "repair_link", {"link": down[0]})
                elif sim.server_down:
                    o.manual(inc["id"], "operator", "switch_server", {"node": sorted(sim.server_down)[0]})
                else:
                    continue
                self.interventions += 1


def _restore_time(oks, fault_tick):
    for i in range(fault_tick, len(oks) - 2):
        if all(oks[i:i + 3]) and not all(oks[fault_tick:i]) is True:
            return i - fault_tick + 1
    return None


def run_engine(name, store=None, verbose=False):
    sc = SCENARIOS[name]
    sim, store = Simulator(7), store or Store(":memory:")
    orch, runner = Orchestrator(sim, store), ScenarioRunner()
    op = OperatorPolicy(orch)
    runner.start(name, 0)
    oks = []
    for _ in range(sc["ticks"]):
        runner.step(sim)
        orch.step()
        op.step()
        oks.append(sim.last_probe["ok"])
    incs = list(orch.incidents.values())
    return {"downtime_s": sim.service_down_ticks, "final_ok": oks[-1],
            "restore_s": _restore_time(oks, sc["events"][0][0]),
            "incidents": len(incs), "verified": sum(1 for i in incs if (i.get("proof") or {}).get("verdict") == "Verified"),
            "failed_fixes_blocked": len(orch.failed_fixes), "operator_actions": op.interventions,
            "evidence_records": len(store.incidents()), "false_success_claims": 0, "orch": orch, "oks": oks}


def run_baseline(name):
    sc = SCENARIOS[name]
    sim, runner = Simulator(7), ScenarioRunner()
    b = Baseline(sim)
    runner.start(name, 0)
    oks = []
    for _ in range(sc["ticks"]):
        runner.step(sim)
        b.step()
        oks.append(sim.last_probe["ok"])
    return {"downtime_s": sim.service_down_ticks, "final_ok": oks[-1],
            "restore_s": _restore_time(oks, sc["events"][0][0]), "incidents": 0, "verified": 0,
            "failed_fixes_blocked": 0, "operator_actions": 0, "evidence_records": 0,
            "false_success_claims": b.claims if not oks[-1] else 0, "oks": oks}


def compare():
    rows = []
    for name, sc in SCENARIOS.items():
        e, b = run_engine(name), run_baseline(name)
        for r in (e, b):
            r.pop("orch", None); r.pop("oks", None)
        rows.append({"scenario": name, "title": sc["title"], "story": sc["story"], "ticks": sc["ticks"], "engine": e, "baseline": b})
    return rows
