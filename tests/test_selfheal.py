import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from selfheal.topology import build_topology, build_flows
from selfheal.network import SimNetwork
from selfheal.healer import SelfHealingEngine


def make(demand=4.0, seed=3):
    g = build_topology()
    net = SimNetwork(g, build_flows(g, demand), seed=seed)
    return net, SelfHealingEngine(net, verbose=False)


def run(net, eng, ticks, events=None):
    events, ratios = events or {}, []
    for t in range(1, ticks + 1):
        for fn, args in events.get(t, []):
            getattr(net, fn)(*args)
        st = net.step()
        ratios.append({f: d / s for f, (s, d, _) in st.items()})
        eng.tick(st, t)
    return ratios


def avg(r, flows=None):
    flows = flows or r.keys()
    return sum(r[f] for f in flows) / len(list(flows))


class Tests(unittest.TestCase):
    def test_topology_matches_spec(self):
        g = build_topology()
        kinds = [d["kind"] for _, d in g.nodes(data=True)]
        self.assertEqual((kinds.count("router"), kinds.count("switch"), kinds.count("pc"), kinds.count("server")), (7, 14, 112, 12))

    def test_no_false_positives_when_healthy(self):
        net, eng = make(); run(net, eng, 40)
        self.assertEqual(eng.incidents, {})

    def test_link_cut_rerouted(self):
        net, eng = make(); r = run(net, eng, 20, {5: [("fail_link", ("R1", "R2"))]})
        self.assertGreaterEqual(avg(r[-1]), 0.99)
        self.assertEqual(eng.incidents["LINK_DOWN:R1-R2"].subtype, "CABLE_CUT_OR_PORT_FAILURE")

    def test_router_failure_root_cause_and_partition_heal(self):
        net, eng = make()
        r = run(net, eng, 30, {5: [("fail_link", ("R1", "R2"))], 12: [("fail_node", ("R4",))]})
        self.assertGreaterEqual(avg(r[-1]), 0.99)
        self.assertEqual(sorted(k for k in eng.incidents if k.startswith("NODE_DOWN")), ["NODE_DOWN:R4"])   # links are symptoms only
        self.assertNotIn("LINK_DOWN:R3-R4", eng.incidents)
        self.assertTrue(any(d["standby"] and d["active"] for _, _, d in net.g.edges(data=True)))

    def test_jamming_classified_and_avoided(self):
        net, eng = make(); r = run(net, eng, 25, {5: [("degrade_link", ("R3", "R4", 50, 1.1))]})
        self.assertEqual(eng.incidents["LINK_DEGRADED:R3-R4"].subtype, "RF_JAMMING_OR_INTERFERENCE")
        self.assertGreaterEqual(avg(r[-1]), 0.99)

    def test_server_failover(self):
        net, eng = make(); r = run(net, eng, 20, {5: [("fail_node", ("SRV3",))]})
        self.assertGreaterEqual(avg(r[-1]), 0.99)
        self.assertTrue(any(f.dst != f.orig_dst for f in net.flows.values()))

    def test_isolated_switch_flagged_not_hidden(self):
        net, eng = make(); run(net, eng, 20, {5: [("fail_node", ("SW6",))]})
        self.assertEqual(eng.incidents["NODE_DOWN:SW6"].status, "MANUAL_ACTION_REQUIRED")

    def test_silent_failure_found_by_tomography(self):
        net, eng = make(); r = run(net, eng, 25, {5: [("degrade_link", ("R2", "R3", 0, 1.0, 35))]})
        self.assertEqual(eng.incidents["LINK_DEGRADED:R2-R3"].subtype, "GRAY_FAILURE_INFERRED")
        self.assertGreaterEqual(avg(r[-1]), 0.97)

    def test_failback_after_repair(self):
        net, eng = make()
        run(net, eng, 40, {5: [("fail_link", ("R1", "R2"))], 15: [("restore_link", ("R1", "R2"))]})
        self.assertTrue(all(f.path == f.home_path for f in net.flows.values()))

    def test_priority_preemption_under_overload(self):
        net, eng = make(demand=14.0)
        ev = {5: [("fail_link", ("R1", "R2")), ("fail_node", ("R4",))]}
        r = run(net, eng, 30, ev)
        hi = [f.id for f in net.flows.values() if f.priority == 3]
        lo = [f.id for f in net.flows.values() if f.priority == 1]
        self.assertGreaterEqual(avg(r[-1], hi), avg(r[-1], lo))
        self.assertGreaterEqual(avg(r[-1], hi), 0.95)

    def test_healing_beats_baseline(self):
        ev = {5: [("fail_node", ("R3",))]}
        a, ea = make(); b, _ = make()
        ra = run(a, ea, 25, ev)
        rb = []
        for t in range(1, 26):
            for fn, args in ev.get(t, []): getattr(b, fn)(*args)
            rb.append({f: d / s for f, (s, d, _) in b.step().items()})
        self.assertGreater(avg(ra[-1]), avg(rb[-1]) + 0.1)

    def test_single_stream_resumes_for_every_fault_type(self):
        import io, contextlib, demo_resume
        for kind in demo_resume.FAULTS:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(demo_resume.run(kind, "F1"), kind)


if __name__ == "__main__":
    unittest.main(verbosity=2)
