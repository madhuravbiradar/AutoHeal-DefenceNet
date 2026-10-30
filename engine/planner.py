"""Recovery planner: enumerate candidate fixes, score them, pick the best, keep a fallback."""
import json

import networkx as nx

from simulator.network import DEMAND, DST, SRC, TOPOLOGY, lk


def _key(action, params):
    return f"{action}|{json.dumps(params, sort_keys=True)}"


def make_plan(diag, sim, assess, failed):
    """Return candidate plans (best first). `failed` = keys of fixes that already failed."""
    cands, target = [], diag["target"]
    route_links = sim.route_links()

    def add(action, params, desc, succ, risk, lat=0.0, util=0.0):
        k = _key(action, params)
        if k in failed:                                  # never repeat a failed fix
            return
        cands.append({"action": action, "params": params, "description": desc, "key": k,
                      "expected_success": round(succ, 2), "risk": round(risk, 1),
                      "expected_latency_ms": round(lat, 1), "expected_util_pct": round(util, 1),
                      "score": round(100 * succ - 0.5 * risk - 0.05 * lat - 0.1 * util, 1)})

    if diag["fault"] == "server_failure":
        node = target.split(":")[1]
        add("switch_server", {"node": node}, f"Fail over site {node} to its standby server", 0.9, 34)
    else:
        link = target
        if link not in route_links:                      # backup link: no traffic impact
            add("flag_link", {"link": link}, f"Flag {link} as unusable (not carrying traffic)", 0.99, 3)
        else:
            for path in nx.all_simple_paths(sim.g, SRC, DST, cutoff=3):
                pl = [lk(a, b) for a, b in zip(path, path[1:])]
                if link in pl or any(not sim.latest[l]["healthy"] or l in sim.avoid_links for l in pl):
                    continue
                lat = sum(sim.latest[l]["latency_ms"] for l in pl)
                util = max((TOPOLOGY[l][2] + (0 if l in route_links else DEMAND)) / TOPOLOGY[l][1] * 100 for l in pl)
                brisk = max(assess[l]["risk"] for l in pl)
                risk = 8 + max(0, util - 60) * 0.8 + brisk * 0.4 + (len(pl) - 1) * 3 + 3.2
                add("reroute", {"path": path}, f"Reroute {SRC}→{DST} via {' → '.join(path)}", 0.92, risk, lat, util)
            succ = 0.85 if diag["fault"] in ("intermittent_link", "degrading_link") else 0.2
            add("restart_link", {"link": link}, f"Restart link {link} (brief disruption on the live path)",
                succ, 25 + (1 - succ) * 40 + 15)
    cands.sort(key=lambda c: -c["score"])
    for c in cands:
        c["fallback"] = next((o["description"] for o in cands if o is not c), "Escalate to the operator")
    return cands
