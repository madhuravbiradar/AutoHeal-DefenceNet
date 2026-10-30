#!/usr/bin/env python3
"""Runs the same fault scenario twice (with and without the self-healing engine) and compares."""
import argparse, csv, json, os
from selfheal.topology import build_topology, build_flows
from selfheal.network import SimNetwork
from selfheal.healer import SelfHealingEngine

# (tick, action, args, expected incident key, expected subtype, description)
SCENARIO = [
    (8,  "fail_link",    ("R1", "R2"),        "LINK_DOWN:R1-R2",      "CABLE_CUT_OR_PORT_FAILURE", "ring cable R1-R2 cut"),
    (20, "fail_node",    ("R4",),             "NODE_DOWN:R4",         "ROUTER_FAILURE",            "router R4 destroyed (ring partitions)"),
    (35, "degrade_link", ("R6", "R7", 45, 1.1), "LINK_DEGRADED:R6-R7", "RF_JAMMING_OR_INTERFERENCE", "jamming on R6-R7 (45% loss)"),
    (44, "fail_node",    ("SW6",),            "NODE_DOWN:SW6",        "SWITCH_FAILURE",            "access switch SW6 dies (8 PCs isolated)"),
    (50, "fail_node",    ("SRV3",),           "NODE_DOWN:SRV3",       "SERVER_FAILURE",            "server SRV3 crashes"),
    (62, "restore_link", ("R1", "R2"),        None, None,                                          "R1-R2 repaired"),
    (62, "restore_node", ("R4",),             None, None,                                          "R4 repaired"),
    (62, "restore_link", ("R6", "R7"),        None, None,                                          "jamming ends"),
    (62, "restore_node", ("SRV3",),           None, None,                                          "SRV3 back"),
    (72, "degrade_link", ("R2", "R3", 0, 1.0, 35), "LINK_DEGRADED:R2-R3", "GRAY_FAILURE_INFERRED", "SILENT 35% loss on R2-R3 (probes cannot see it)"),
]


def apply_events(net, t, quiet=False):
    for ev in SCENARIO:
        if ev[0] == t:
            getattr(net, ev[1])(*ev[2])
            if not quiet:
                print(f"\n>>> [t={t:03d}] FAULT INJECTED: {ev[5]}")


def run(ticks, seed, heal, verbose):
    g = build_topology()
    net = SimNetwork(g, build_flows(g), seed=seed)
    eng = SelfHealingEngine(net, verbose=verbose) if heal else None
    series = []
    for t in range(1, ticks + 1):
        apply_events(net, t, quiet=not verbose)
        stats = net.step()
        sent = sum(s for s, _, _ in stats.values()); dl = sum(d for _, d, _ in stats.values())
        series.append((t, dl / sent, sent - dl))
        if eng:
            eng.tick(stats, t)
    return net, eng, series


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    print("=== Run 1: WITH autonomous self-healing ===")
    net, eng, healed = run(a.ticks, a.seed, True, not a.quiet)
    _, _, base = run(a.ticks, a.seed, False, False)

    inj = {e[3]: e for e in SCENARIO if e[3]}
    print("\n" + "=" * 100 + "\nINCIDENT REPORT\n" + "=" * 100)
    print(f"{'incident':<22}{'injected':>9}{'detected':>9}{'restored':>9}{'MTTD':>6}{'MTTR':>6}  {'diagnosis':<28}{'correct':<8}status")
    rows = []
    for key, inc in eng.incidents.items():
        e = inj.get(key)
        t0 = e[0] if e else None
        ok = (e[4] == inc.subtype) if e else None
        rows.append(dict(key=key, injected=t0, detected=inc.detect_tick, restored=inc.mitigated_tick, subtype=inc.subtype,
                         correct=ok, status=inc.status, plan=inc.plan_name, severity=inc.severity,
                         confidence=inc.confidence, recommendation=inc.recommendation))
        mttd = inc.detect_tick - t0 if t0 else "-"
        mttr = (inc.mitigated_tick - t0) if (t0 and inc.mitigated_tick) else "-"
        print(f"{key:<22}{str(t0):>9}{inc.detect_tick:>9}{str(inc.mitigated_tick):>9}{str(mttd):>6}{str(mttr):>6}  "
              f"{inc.subtype:<28}{'YES' if ok else ('NO' if ok is False else '-'):<8}{inc.status}")
        if inc.recommendation:
            print(f"    -> {inc.recommendation}")
    avg_h = sum(r for _, r, _ in healed) / len(healed); avg_b = sum(r for _, r, _ in base) / len(base)
    print(f"\nAverage packet delivery over {a.ticks}s:  WITHOUT healing {avg_b:.1%}   WITH healing {avg_h:.1%}")
    print(f"Packets lost:                          WITHOUT healing {sum(l for *_, l in base):,}   WITH healing {sum(l for *_, l in healed):,}")
    print("(8 PCs behind failed switch SW6 are physically isolated and cannot be saved remotely; the engine flags them for repair.)")

    os.makedirs("output", exist_ok=True)
    with open("output/timeline.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["tick", "delivery_with_healing", "delivery_without_healing", "lost_with", "lost_without"])
        for (t, r1, l1), (_, r0, l0) in zip(healed, base):
            w.writerow([t, f"{r1:.4f}", f"{r0:.4f}", l1, l0])
    json.dump({"incidents": rows, "events": eng.events, "avg_delivery_with": avg_h, "avg_delivery_without": avg_b},
              open("output/incident_report.json", "w"), indent=2, default=str)
    print("Wrote output/timeline.csv and output/incident_report.json")


if __name__ == "__main__":
    main()
