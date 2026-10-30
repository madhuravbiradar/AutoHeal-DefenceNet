"""Run:  python -m unittest discover -s tests -v     (no extra packages needed)"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.service import Runtime
from db.store import Store
from demo.compare import compare, run_baseline, run_engine
from engine import gate, proof
from engine.orchestrator import Orchestrator
from simulator.network import Simulator


class Gate(unittest.TestCase):
    def test_four_rules(self):
        self.assertEqual(gate.decide(0.95, 20)["decision"], "AUTO_HEAL")
        self.assertEqual(gate.decide(0.65, 20)["decision"], "GATHER_EVIDENCE")
        self.assertEqual(gate.decide(0.9, 50)["decision"], "OPERATOR_APPROVAL")
        self.assertEqual(gate.decide(0.3, 60)["decision"], "MANUAL_REVIEW")
        self.assertEqual(gate.decide(0.95, 20, mode="paused")["decision"], "OPERATOR_APPROVAL")
        self.assertEqual(gate.decide(0.65, 20, gathers=4)["decision"], "OPERATOR_APPROVAL")


class Proof(unittest.TestCase):
    def p(self, loss, ok, lat=40):
        return {"loss_pct": loss, "ok": ok, "latency_ms": lat}
    def test_verdicts(self):
        self.assertEqual(proof.verdict([self.p(0.2, True)] * 4)["verdict"], "Verified")
        self.assertEqual(proof.verdict([self.p(6, False)] * 2 + [self.p(0.1, True)] * 2)["verdict"], "Partial")
        self.assertEqual(proof.verdict([self.p(25, False)] * 4)["verdict"], "Failed")


class Scenarios(unittest.TestCase):
    def test_predicted_failure_has_no_outage(self):
        e = run_engine("predicted")
        self.assertEqual(e["downtime_s"], 0)
        self.assertEqual(e["verified"], 1)

    def test_sudden_failure_heals_and_is_proven(self):
        e = run_engine("sudden")
        self.assertTrue(e["final_ok"]); self.assertEqual(e["verified"], 1); self.assertLessEqual(e["downtime_s"], 3)

    def test_backup_fails_needs_operator_approval(self):
        e = run_engine("backup_fails")
        self.assertTrue(e["final_ok"]); self.assertEqual(e["operator_actions"], 1)
        self.assertFalse(run_baseline("backup_fails")["final_ok"])

    def test_intermittent_beats_baseline(self):
        e, b = run_engine("intermittent"), run_baseline("intermittent")
        self.assertLess(e["downtime_s"], b["downtime_s"])
        self.assertEqual(list(e["orch"].incidents.values())[0]["fault"], "intermittent_link")

    def test_failed_fix_is_blacklisted_and_operator_recovers(self):
        e = run_engine("auto_heal_fails")
        o = e["orch"]
        inc = list(o.incidents.values())[0]
        self.assertTrue(e["final_ok"])
        self.assertEqual(len(o.failed_fixes), 2)                      # reroute via C + restart B-C
        self.assertEqual([a["proof"]["verdict"] for a in inc["attempts"]], ["Failed", "Failed", "Verified"])
        self.assertEqual(inc["approved_by"], "operator")
        self.assertEqual(inc["first_target"], "A-B")
        self.assertFalse(run_baseline("auto_heal_fails")["final_ok"])

    def test_server_failure_is_not_blamed_on_a_link(self):
        e = run_engine("server_failure")
        self.assertEqual(list(e["orch"].incidents.values())[0]["fault"], "server_failure")
        self.assertTrue(e["final_ok"])

    def test_every_incident_is_persisted_with_evidence(self):
        store = Store(":memory:")
        e = run_engine("backup_fails", store=store)
        rows = store.incidents()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertTrue(r["evidence"]); self.assertTrue(r["plan"]); self.assertTrue(r["proof"]); self.assertIn("timeline", r)

    def test_engine_never_repeats_failed_fix(self):
        o = run_engine("auto_heal_fails")["orch"]
        keys = [a["plan"]["key"] for i in o.incidents.values() for a in i["attempts"]]
        failed = [a["plan"]["key"] for i in o.incidents.values() for a in i["attempts"] if a["proof"]["verdict"] == "Failed"]
        for k in failed:
            self.assertEqual(keys.count(k), 1)

    def test_pause_forces_operator_approval(self):
        sim = Simulator(7); o = Orchestrator(sim, Store(":memory:")); o.set_mode("paused")
        for t in range(12):
            if t == 3: sim.inject("down", "A-B")
            o.step()
        inc = list(o.incidents.values())[0]
        self.assertEqual(inc["status"], "awaiting_approval")
        o.approve(inc["id"], "operator")
        for _ in range(6): o.step()
        self.assertEqual(inc["status"], "resolved")

    def test_paused_flap_relabels_to_intermittent_while_waiting(self):
        sim = Simulator(7); o = Orchestrator(sim, Store(":memory:")); o.set_mode("paused")
        sim.inject("flap", "A-B")
        for _ in range(9): o.step()
        inc = list(o.incidents.values())[0]
        self.assertEqual(inc["status"], "awaiting_approval")
        self.assertEqual(inc["fault"], "intermittent_link")
        o.approve(inc["id"], "operator")
        for _ in range(6): o.step()
        self.assertEqual(inc["status"], "resolved")

    def test_compare_covers_all_scenarios(self):
        rows = compare()
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["engine"]["final_ok"] for r in rows))


class Service(unittest.TestCase):
    def test_roles_and_actions(self):
        rt = Runtime(db_path=":memory:")
        self.assertIsNone(rt.login("operator", "wrong"))
        op, vw = rt.login("operator", "operator123"), rt.login("viewer", "viewer123")
        self.assertEqual((op["role"], vw["role"]), ("operator", "viewer"))
        self.assertEqual(rt.who(op["token"])["user"], "operator")
        who = rt.who(op["token"])
        rt.inject(who, "down", "A-B")
        with self.assertRaises(ValueError): rt.inject(who, "down", "Z-Z")
        rt.start_scenario(who, "sudden")
        for _ in range(12):
            rt.runner.step(rt.sim); rt.orch.step()
        self.assertEqual(rt.incidents()[0]["status"], "resolved")
        self.assertIn("links", rt.state())
        self.assertTrue(rt.store.audit())


class Accounts(unittest.TestCase):
    good = dict(full_name="Sanvi Rao", username="sanvi_r", email="Sanvi@Example.com", password="Passw0rd99",
                confirm_password="Passw0rd99", role="viewer", organization="")

    def test_required_fields_are_reported(self):
        rt = Runtime(db_path=":memory:")
        errs = rt.signup({})
        for k in ("full_name", "username", "email", "password", "role"):
            self.assertIn(k, errs)

    def test_validation_rules(self):
        rt = Runtime(db_path=":memory:")
        self.assertIn("email", rt.signup({**self.good, "email": "nope"}))
        self.assertIn("password", rt.signup({**self.good, "password": "short1", "confirm_password": "short1"}))
        self.assertIn("password", rt.signup({**self.good, "password": "onlyletters", "confirm_password": "onlyletters"}))
        self.assertIn("confirm_password", rt.signup({**self.good, "confirm_password": "different1"}))
        self.assertIn("username", rt.signup({**self.good, "username": "a b"}))

    def test_signup_then_login_by_username_or_email(self):
        rt = Runtime(db_path=":memory:")
        self.assertEqual(rt.signup(self.good), {})
        self.assertIn("username", rt.signup(self.good))                                   # duplicate username
        self.assertIn("email", rt.signup({**self.good, "username": "other_user"}))        # duplicate email
        self.assertEqual(rt.login("sanvi_r", "Passw0rd99")["role"], "viewer")
        self.assertEqual(rt.login("SANVI@example.com", "Passw0rd99")["user"], "sanvi_r")
        self.assertIsNone(rt.login("sanvi_r", "wrong-pass1"))

    def test_operator_role_needs_access_code(self):
        from api.service import OPERATOR_CODE
        rt = Runtime(db_path=":memory:")
        self.assertIn("operator_code", rt.signup({**self.good, "role": "operator"}))
        self.assertIn("operator_code", rt.signup({**self.good, "role": "operator", "operator_code": "guess"}))
        self.assertEqual(rt.signup({**self.good, "role": "operator", "operator_code": OPERATOR_CODE}), {})
        self.assertEqual(rt.login("sanvi_r", "Passw0rd99")["role"], "operator")

    def test_demo_accounts_and_logout(self):
        rt = Runtime(db_path=":memory:")
        r = rt.login("operator", "operator123")
        self.assertEqual(r["role"], "operator")
        rt.logout(r["token"])
        self.assertIsNone(rt.who(r["token"]))

    def test_pages_mark_required_fields_with_asterisk(self):
        import re
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")
        up = open(os.path.join(base, "signup.html"), encoding="utf-8").read()
        for fid in ("full_name", "username", "email", "password", "confirm_password", "role", "operator_code"):
            self.assertRegex(up, r'<label for="%s">[^<]*<span class="req"' % fid)
        self.assertNotRegex(up, r'<label for="organization">[^<]*<span class="req"')       # optional field
        inn = open(os.path.join(base, "signin.html"), encoding="utf-8").read()
        for fid in ("id", "pw"):
            self.assertRegex(inn, r'<label for="%s">[^<]*<span class="req"' % fid)


if __name__ == "__main__":
    unittest.main()
