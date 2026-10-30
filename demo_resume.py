#!/usr/bin/env python3
"""Follow ONE packet stream through a failure: see it break, get healed automatically, and resume.
   python demo_resume.py --fault link|router|server|jam|all   [--flow F1]"""
import argparse
from selfheal.topology import build_topology, build_flows
from selfheal.network import SimNetwork
from selfheal.healer import SelfHealingEngine

FAULTS = ("link", "router", "server", "jam")


def inject(net, f, kind):
    p = f.path
    if kind == "link":
        net.fail_link(p[1], p[2]); return f"access link {p[1]}-{p[2]} cut"
    if kind == "router":
        net.fail_node(p[2]); return f"router {p[2]} destroyed"
    if kind == "server":
        net.fail_node(p[-1]); return f"server {p[-1]} crashed"
    net.degrade_link(p[2], p[3], loss=60, latency_mult=1.1); return f"60% jamming on {p[2]}-{p[3]}"


def run(kind, fid, ticks=22, fault_at=6, verbose_engine=False):
    g = build_topology()
    net = SimNetwork(g, build_flows(g), seed=11)
    eng = SelfHealingEngine(net, verbose=verbose_engine)
    f = net.flows[fid]
    print(f"\n{'=' * 92}\nFAULT TYPE: {kind.upper()}   |   tracking {fid}: {f.src} -> {f.dst}   (10 packets/s)\n{'=' * 92}")
    print(f"{'t':>3} {'sent':>5} {'recv':>5}  {'status':<10} path / event")
    fault_desc, lost, outage, resumed_at, seen_heal = "", 0, [], None, None
    for t in range(1, ticks + 1):
        ev = ""
        if t == fault_at:
            fault_desc = inject(net, f, kind); ev = f"  <<< FAULT: {fault_desc}"
        path_before = list(f.path)
        stats = net.step()
        sent, recv, _ = stats[fid]
        lost += sent - recv
        status = "OK" if recv >= 0.9 * sent else ("DEGRADED" if recv > 0 else "DOWN")
        n_before = len(eng.executor.audit)
        eng.tick(stats, t)
        if len(eng.executor.audit) > n_before:
            ev += "  >>> SELF-HEAL APPLIED" + (f": {path_before} -> {f.path}" if f.path != path_before else "")
        if t >= fault_at and status != "OK":
            outage.append(t)
        if t > fault_at and status == "OK" and resumed_at is None and outage:
            resumed_at = t
        print(f"{t:>3} {sent:>5} {recv:>5}  {status:<10} {'->'.join(path_before)}{ev}")
    inc = [i for i in eng.incidents.values()]
    print("-" * 92)
    if inc:
        i = inc[0]
        print(f"Detected   : t={i.detect_tick}  {i.category} @ {i.location}  ({i.subtype}, {i.confidence:.0%} confidence)")
    gap = len(outage)
    print(f"Outage     : {gap} second(s) with degraded traffic, {lost} packet(s) lost out of {ticks * 10}")
    print(f"Resumed    : t={resumed_at}, delivery back to 100% on new path {'->'.join(f.path)}" if resumed_at else "Resumed    : NOT RECOVERED")
    tail = [net.step() for _ in range(5)]
    ok = all(s[fid][1] == s[fid][0] for s in tail)
    print(f"Verification (next 5 s after recovery): {'PASS - every packet delivered' if ok else 'FAIL'}")
    return resumed_at is not None and ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", default="all", choices=FAULTS + ("all",))
    ap.add_argument("--flow", default="F1")
    ap.add_argument("--engine-log", action="store_true", help="also print the engine's detect/diagnose/plan log")
    a = ap.parse_args()
    results = {k: run(k, a.flow, verbose_engine=a.engine_log) for k in (FAULTS if a.fault == "all" else (a.fault,))}
    print("\nSUMMARY:", ", ".join(f"{k}: {'RECOVERED' if v else 'FAILED'}" for k, v in results.items()))
