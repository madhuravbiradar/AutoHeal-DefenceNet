"""The closed loop: monitor -> predict -> diagnose -> plan -> gate -> execute -> prove -> record.

One incident is handled at a time (serialised changes, like a real change-management window).
Incident status: gathering | awaiting_approval | manual_review | proving | resolved | mitigated
"""
from collections import deque
from datetime import datetime

from engine import diagnose as dg, executor, gate, planner, proof
from engine.predict import Predictor
from simulator.network import TOPOLOGY

FINAL = {"resolved", "mitigated"}


class Orchestrator:
    def __init__(self, sim, store):
        self.sim, self.store = sim, store
        self.pred = Predictor()
        self.reset()

    def reset(self):
        self.mode = "auto"
        self.assess = {l: self.pred.assess([], TOPOLOGY[l][0]) for l in TOPOLOGY}
        self.alerts = deque(maxlen=60)
        self.alert_level = {l: "ok" for l in TOPOLOGY}
        self.incidents = {}
        self.failed_fixes = {}      # plan key -> info  (the system never repeats these)
        self.known_faults = {}      # target -> tick handled
        self._diags = {}
        self._next_id = self.store.next_incident_id()

    # ------------------------------------------------------------------ helpers
    def _log(self, inc, kind, text):
        inc["timeline"].append({"tick": self.sim.t, "time": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text})

    def _save(self, inc):
        self.store.save_incident(inc)

    def _on_route(self, target):
        return target in self.sim.route_links() or (target.startswith("node:") and target[5:] in self.sim.route)

    def active(self):
        return [i for i in self.incidents.values() if i["status"] not in FINAL]

    # --------------------------------------------------------------------- loop
    def step(self):
        self.sim.tick()
        self._update_assessments()
        self._diags = {d["target"]: d for d in dg.diagnose(self.sim.history, self.sim.node_hist, self.assess)}
        self._cleanup_known()
        self._advance()
        self._detect()

    def _update_assessments(self):
        for l, (base, _, _) in TOPOLOGY.items():
            a = self.assess[l] = self.pred.assess(self.sim.history[l], base)
            last = self.sim.latest[l]
            lvl = a["level"]
            if lvl != self.alert_level[l]:
                if lvl != "ok":
                    down = not last["healthy"]
                    self._alert(l, "critical" if lvl == "critical" else "warning", "outage" if down else "predictive", a["risk"],
                                f"{l} is failing (risk {a['risk']}/100, loss {last['loss_pct']}%)" if down else
                                f"Predicted degradation on {l}: risk {a['risk']}/100, latency {a['avg_latency']} ms rising {a['trend']:+.1f} ms/s")
                elif self.alert_level[l] != "ok":
                    self._alert(l, "info", "recovery", a["risk"], f"{l} back to normal (risk {a['risk']}/100)")
                self.alert_level[l] = lvl

    def _alert(self, link, level, kind, risk, msg):
        a = {"tick": self.sim.t, "time": datetime.now().strftime("%H:%M:%S"), "level": level, "kind": kind,
             "link": link, "risk": risk, "message": msg}
        self.alerts.appendleft(a)
        self.store.add_alert(a)

    def _cleanup_known(self):
        for t in list(self.known_faults):
            if t.startswith("node:"):
                h = list(self.sim.node_hist[t[5:]])[-6:]
                clear = len(h) == 6 and all(x["responding"] for x in h)
            else:
                h = list(self.sim.history[t])[-6:]
                clear = len(h) == 6 and all(x["healthy"] for x in h)
            if clear and t not in self.sim.faults:
                self.known_faults.pop(t)
                if not t.startswith("node:"):
                    self.sim.avoid_links.discard(t)

    def _detect(self):
        if self.active():
            return
        for d in sorted(self._diags.values(), key=lambda d: -d["confidence"]):
            if d["target"] in self.known_faults and not self._on_route(d["target"]):
                continue
            self._open(d)
            break

    def _open(self, d):
        inc = {"id": self._next_id, "status": "detected", "fault": d["fault"], "target": d["target"], "first_target": d["target"],
               "confidence": d["confidence"], "evidence": d["evidence"], "readings": d["readings"],
               "opened_tick": self.sim.t, "opened_at": datetime.now().isoformat(timespec="seconds"),
               "gathers": 0, "quiet": 0, "reassessed": False, "attempts": [], "plan": None, "alternatives": [],
               "decision": None, "gate_reason": None, "proof": None, "probes": [], "approved_by": None,
               "timeline": [], "recovery_ticks": None, "mode_at_open": self.mode}
        self._next_id += 1
        self.incidents[inc["id"]] = inc
        self._log(inc, "detect", f"Incident opened: {d['fault']} on {d['target']}")
        self._evaluate(inc, d)

    # -------------------------------------------------------- diagnose/plan/gate
    def _evaluate(self, inc, d):
        inc.update(fault=d["fault"], target=d["target"], confidence=d["confidence"],
                   evidence=d["evidence"], readings=d["readings"])
        self._log(inc, "diagnose", f"{d['fault']} on {d['target']} · confidence {d['confidence']} · " + d["evidence"][0])
        cands = planner.make_plan(d, self.sim, self.assess, self.failed_fixes)
        if not cands:
            inc.update(status="manual_review", decision="MANUAL_REVIEW", plan=None, alternatives=[],
                       gate_reason="No viable recovery candidate left (all remaining options already failed)")
            self._log(inc, "gate", "MANUAL_REVIEW: " + inc["gate_reason"])
            return self._save(inc)
        best = cands[0]
        conf = round(d["confidence"] * min(1.0, best["expected_success"] / 0.7), 2)
        g = gate.decide(conf, best["risk"], self.mode, inc["gathers"])
        inc.update(plan=best, alternatives=cands[1:], decision=g["decision"], gate_reason=g["reason"],
                   gate_rule=g["rule"], decision_confidence=conf)
        self._log(inc, "plan", f"{best['description']} (risk {best['risk']}, expected success {best['expected_success']}); fallback: {best['fallback']}")
        self._log(inc, "gate", f"{g['decision']}: {g['reason']}")
        dec = g["decision"]
        if dec == "AUTO_HEAL":
            self._execute(inc, best, "auto")
        elif dec == "GATHER_EVIDENCE":
            inc["gathers"] += 1
            inc["status"] = "gathering"
        elif dec == "OPERATOR_APPROVAL":
            inc["status"] = "awaiting_approval"
        else:
            inc["status"] = "manual_review"
        self._save(inc)

    def _execute(self, inc, plan, who):
        res = executor.execute(self.sim, plan)
        inc.update(status="proving", approved_by=who, probes=[])
        inc["attempts"].append({"plan": plan, "approved_by": who, "exec": res, "tick": self.sim.t, "proof": None})
        self._log(inc, "execute", f"{plan['description']} · by {who} · {res['detail']}")
        self._save(inc)

    # ---------------------------------------------------------- advance / prove
    def _advance(self):
        for inc in self.active():
            if inc["status"] == "gathering":
                d = self._diags.get(inc["target"])
                if d:
                    inc["quiet"] = 0
                    self._evaluate(inc, d)
                else:
                    inc["quiet"] += 1
                    if inc["quiet"] >= 4:
                        self._log(inc, "close", "Fault cleared by itself: no action needed")
                        inc["decision"], inc["gate_reason"] = "NO_ACTION", "Transient fault, self-recovered"
                        self._finish(inc, "mitigated", persist_fault=False)
            elif inc["status"] == "awaiting_approval":
                # keep the diagnosis fresh while the human decides (e.g. link_failure -> intermittent_link)
                d = self._diags.get(inc["target"])
                if d and (d["fault"] != inc["fault"] or d["confidence"] != inc["confidence"]):
                    relabel = d["fault"] != inc["fault"]
                    cands = planner.make_plan(d, self.sim, self.assess, self.failed_fixes)
                    inc.update(fault=d["fault"], confidence=d["confidence"], evidence=d["evidence"], readings=d["readings"])
                    if cands:
                        inc.update(plan=cands[0], alternatives=cands[1:])
                    if relabel:
                        self._log(inc, "diagnose", f"Diagnosis updated while waiting: {d['fault']} · confidence {d['confidence']} · " + d["evidence"][0])
                    self._save(inc)
            elif inc["status"] == "proving":
                inc["probes"].append(self.sim.probe())
                if len(inc["probes"]) >= proof.PROBES:
                    res = proof.verdict(inc["probes"])
                    inc["proof"] = res
                    inc["attempts"][-1]["proof"] = res
                    self._log(inc, "proof", f"{res['verdict']}: {res['probes_ok']} probes ok, loss {res['avg_loss_pct']}%, latency {res['avg_latency_ms']} ms, jitter {res['jitter_ms']} ms")
                    self._conclude(inc, res)

    def _conclude(self, inc, res):
        v, plan = res["verdict"], inc["plan"]
        if v == "Verified":
            return self._finish(inc, "resolved")
        if v == "Partial":
            return self._finish(inc, "mitigated")
        self.failed_fixes[plan["key"]] = {"incident": inc["id"], "fix": plan["description"], "tick": self.sim.t}
        self._log(inc, "failed-fix", f"Recorded as failed fix: {plan['description']} (will not be repeated)")
        if not inc["reassessed"]:
            inc["reassessed"] = True
            self._log(inc, "reassess", "Proof failed: re-diagnosing once with current evidence")
            on = [d for t, d in self._diags.items() if self._on_route(t)]
            d = max(on, key=lambda x: x["confidence"]) if on else self._diags.get(inc["target"]) or \
                {"fault": inc["fault"], "target": inc["target"], "confidence": inc["confidence"],
                 "evidence": inc["evidence"], "readings": inc["readings"]}
            inc["gathers"] = 0
            self._evaluate(inc, d)
        else:
            inc.update(status="manual_review", decision="ESCALATED",
                       gate_reason="Second attempt also failed: escalated to a human operator")
            self._log(inc, "escalate", inc["gate_reason"])
            self._save(inc)

    def _finish(self, inc, status, persist_fault=True):
        inc["status"] = status
        inc["resolved_tick"] = self.sim.t
        inc["recovery_ticks"] = self.sim.t - inc["opened_tick"]
        plan = inc.get("plan") or {}
        if persist_fault:
            self.known_faults[inc["target"]] = self.sim.t
            if plan.get("action") == "reroute" and not inc["target"].startswith("node:"):
                self.sim.avoid_links.add(inc["target"])
        self._log(inc, "close", f"Incident {status} after {inc['recovery_ticks']} s")
        self._save(inc)

    # --------------------------------------------------------- operator actions
    def approve(self, iid, user):
        inc = self.incidents.get(iid)
        if not inc or inc["status"] != "awaiting_approval":
            raise ValueError("incident is not waiting for approval")
        self._log(inc, "approve", f"Approved by {user}")
        self._execute(inc, inc["plan"], user)

    def reject(self, iid, user):
        inc = self.incidents.get(iid)
        if not inc or inc["status"] != "awaiting_approval":
            raise ValueError("incident is not waiting for approval")
        inc.update(status="manual_review", decision="REJECTED", gate_reason=f"Plan rejected by {user}")
        self._log(inc, "reject", f"Plan rejected by {user}; manual handling")
        self._save(inc)

    def manual(self, iid, user, action, params):
        inc = self.incidents.get(iid)
        if not inc or inc["status"] not in ("manual_review", "awaiting_approval"):
            raise ValueError("incident is not waiting for an operator")
        plan = {"action": action, "params": params, "description": f"Operator action: {action} {params}",
                "key": f"manual|{action}|{params}", "risk": 0, "expected_success": 1.0, "fallback": "-"}
        inc["reassessed"] = True
        inc["plan"] = plan
        inc["decision"] = "OPERATOR_ACTION"
        self._log(inc, "manual", f"{user} executes {action} {params}")
        self._execute(inc, plan, user)

    def set_mode(self, mode):
        self.mode = mode

    # ------------------------------------------------------------------- views
    def view(self):
        return {"mode": self.mode, "assess": self.assess, "alerts": list(self.alerts)[:30],
                "active": [self.incidents[i["id"]] for i in self.active()],
                "failed_fixes": [{"key": k, **v} for k, v in self.failed_fixes.items()],
                "known_faults": list(self.known_faults)}
