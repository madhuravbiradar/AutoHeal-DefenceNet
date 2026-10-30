"""Rule-based diagnosis. Every verdict carries the evidence (readings) that triggered it."""
from engine.predict import WARN
from simulator.network import LAT_LIMIT, LOSS_LIMIT


def trailing_bad(rs):
    n = 0
    for r in reversed(rs):
        if r["healthy"]:
            break
        n += 1
    return n


def transitions(rs):
    return sum(1 for a, b in zip(rs, rs[1:]) if a["healthy"] != b["healthy"])


def _fmt(r):
    return f"t={r['tick']}: {r['latency_ms']} ms / {r['loss_pct']}% loss"


def diagnose(link_hist, node_hist, assess):
    out = []
    # Rule 1 - server failure: heartbeat missing AND every link touching the node unhealthy
    for n, h in node_hist.items():
        hb = list(h)[-3:]
        if not hb or hb[-1]["responding"]:
            continue
        bad = sum(1 for x in hb if not x["responding"])
        inc = [l for l in link_hist if n in l.split("-")]
        all_bad = all(link_hist[l] and not link_hist[l][-1]["healthy"] for l in inc)
        conf = min(0.95, 0.35 + 0.2 * bad + (0.15 if all_bad else 0))
        out.append({"fault": "server_failure", "target": f"node:{n}", "confidence": round(conf, 2),
                    "evidence": [f"Server heartbeat at site {n} missing in {bad}/3 probes",
                                 f"All {len(inc)} links touching {n} are unhealthy" if all_bad
                                 else "Some links touching the node are still healthy",
                                 "Failure is on the node, not on one link"],
                    "readings": [link_hist[l][-1] for l in inc if link_hist[l]]})
    # Link rules - only when both endpoints respond ("both endpoints up, so it is the link")
    for l, h in link_hist.items():
        rs = list(h)
        u, v = l.split("-")
        if len(rs) < 2 or not all(node_hist[x][-1]["responding"] for x in (u, v)):
            continue
        a = assess[l]
        t = transitions(rs[-10:])
        n = trailing_bad(rs)
        last3 = rs[-3:]
        if t >= 3:                                              # Rule 2 - intermittent link
            out.append({"fault": "intermittent_link", "target": l, "confidence": round(min(0.95, 0.7 + 0.05 * t), 2),
                        "evidence": [f"{t} healthy/unhealthy transitions in the last {len(rs[-10:])} readings",
                                     "Link keeps failing and recovering: " + "; ".join(_fmt(r) for r in last3),
                                     f"Both endpoints ({u}, {v}) respond, so the fault is the link"],
                        "readings": last3})
        elif n >= 1 and rs[-1]["loss_pct"] >= 50:               # Rule 3 - hard link failure
            conf = {1: 0.65, 2: 0.9}.get(n, 0.95)
            out.append({"fault": "link_failure", "target": l, "confidence": conf,
                        "evidence": [f"{n} consecutive probe(s) with {rs[-1]['loss_pct']}% loss on {l}",
                                     f"Both endpoints ({u}, {v}) still respond, so the fault is the link, not a server",
                                     "Latest: " + _fmt(rs[-1])],
                        "readings": last3})
        elif n >= 1:                                            # Rule 4 - degraded past thresholds
            out.append({"fault": "degrading_link", "target": l, "confidence": 0.9,
                        "evidence": [f"{l} breaches limits (>{LAT_LIMIT:.0f} ms or >{LOSS_LIMIT:.0f}% loss) for {n} probe(s)",
                                     f"Risk score {a['risk']}/100", "Latest: " + _fmt(rs[-1])],
                        "readings": last3})
        elif a["risk"] >= WARN and a["trend"] > 1:              # Rule 5 - predicted failure
            out.append({"fault": "degrading_link", "target": l, "confidence": round(min(0.9, 0.4 + a["risk"] / 100 * 0.7), 2),
                        "evidence": [f"Rolling avg latency {a['avg_latency']} ms, rising {a['trend']:+.1f} ms per second",
                                     f"Rolling avg loss {a['avg_loss']}%",
                                     f"Risk score {a['risk']}/100 is above the warning level ({WARN}); link still passes its limits",
                                     "Predicted failure: act before the outage"],
                        "readings": last3})
    return out
