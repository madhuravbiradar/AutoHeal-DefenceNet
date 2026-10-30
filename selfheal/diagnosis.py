"""DIAGNOSE: root-cause analysis, symptom correlation, impact (affected flows), isolation,
partition and single-point-of-failure analysis, and network tomography for silent failures."""
import math
from collections import defaultdict
from dataclasses import dataclass, field
import networkx as nx
from .routing import ek, edge_keys, host_paths


@dataclass
class Fault:
    key: str
    category: str            # NODE_DOWN | LINK_DOWN | LINK_DEGRADED
    subtype: str
    location: str
    severity: int
    confidence: float
    cause: str
    evidence: list
    symptoms: list = field(default_factory=list)
    affected: set = field(default_factory=set)


@dataclass
class Diagnosis:
    faults: list = field(default_factory=list)
    hit_flows: set = field(default_factory=set)
    unrecoverable: set = field(default_factory=set)
    isolated_nodes: list = field(default_factory=list)
    partitions: list = field(default_factory=list)
    partition_healable: bool = True
    risks: list = field(default_factory=list)
    new_suspects: set = field(default_factory=set)


def believed_graph(inv, view, include_standby=False, exclude_edges=frozenset()):
    """Topology as the monitor currently believes it (healthy elements only)."""
    H = nx.Graph()
    for n, d in inv.nodes(data=True):
        if n not in view.nodes_down:
            H.add_node(n, kind=d["kind"])
    for u, v, d in inv.edges(data=True):
        k = ek(u, v)
        if u in view.nodes_down or v in view.nodes_down or k in view.links_down or k in exclude_edges:
            continue
        if d["standby"] and not d["active"] and not include_standby:
            continue
        deg = view.links_degraded.get(k, {})
        H.add_edge(u, v, latency=d["latency"], bw=d["bw"], standby=d["standby"], active=d["active"],
                   loss=deg.get("loss", 0.0), lat_ratio=max(1.0, deg.get("lat_ratio", 1.0)))
    return H


def classify_degradation(deg, util):
    loss, lr = deg["loss"], deg["lat_ratio"]
    if util >= 0.95 or (lr >= 2 and util >= 0.8):
        return "CONGESTION", 0.85, "traffic exceeds link capacity (queue drops + delay)"
    if loss >= 10 and lr < 1.5:
        return "RF_JAMMING_OR_INTERFERENCE", 0.80, "high loss with normal delay and low load: physical-layer interference/jamming"
    if lr >= 2 and loss < 10:
        return "LATENCY_DEGRADATION", 0.70, "delay inflation without heavy loss: queuing or rerouting upstream"
    return "PACKET_LOSS_DEGRADATION", 0.60, "elevated loss, mixed signature"


def _severity(n_aff, total, floor=1):
    frac = n_aff / max(1, total)
    s = 5 if frac >= .5 else 4 if frac >= .2 else 3 if frac >= .05 else 2 if n_aff else 1
    return max(s, floor)


def _hit_keys(path, view, suspects):
    keys = set()
    for n in path:
        if n in view.nodes_down:
            keys.add(f"NODE_DOWN:{n}")
    for a, b in zip(path, path[1:]):
        k = ek(a, b)
        if a in view.nodes_down or b in view.nodes_down:
            continue
        if k in view.links_down:
            keys.add("LINK_DOWN:%s-%s" % k)
        elif k in view.links_degraded or k in suspects:
            keys.add("LINK_DEGRADED:%s-%s" % k)
    return keys


def _tomography(flows, bad_ids):
    """Find the link shared by most failing flows (and mostly failing flows only)."""
    through = defaultdict(list)
    for f in flows:
        if f.path:
            for k in edge_keys(f.path):
                through[k].append(f.id)
    best, score = None, (0, 0.0)
    cand = set()
    for f in flows:
        if f.id in bad_ids:
            cand.update(edge_keys(f.path))
    for k in cand:
        n_bad = sum(1 for i in through[k] if i in bad_ids)
        frac = n_bad / len(through[k])
        if n_bad >= 3 and frac >= 0.8 and (n_bad, frac) > score:
            best, score = k, (n_bad, frac)
    return best, score


def diagnose(inv, view, flows, util, suspects):
    d, total = Diagnosis(), len(flows)
    by_fault = defaultdict(set)
    for f in flows:
        if not f.path:
            d.hit_flows.add(f.id); continue
        for key in _hit_keys(f.path, view, suspects):
            by_fault[key].add(f.id); d.hit_flows.add(f.id)

    # --- tomography for failures the probes cannot see (silent loss) ---
    suspects = set(suspects)
    unexplained = {f.id for f in flows if view.bad_streak.get(f.id, 0) >= 2 and f.id not in d.hit_flows and f.path}
    if len(unexplained) >= 3:
        edge, (nb, fr) = _tomography(flows, unexplained)
        if edge:
            suspects.add(edge); d.new_suspects.add(edge)
            key = "LINK_DEGRADED:%s-%s" % edge
            for f in flows:
                if f.path and edge in edge_keys(f.path):
                    by_fault[key].add(f.id); d.hit_flows.add(f.id)

    # --- root causes ---
    for n in sorted(view.nodes_down):
        kind = inv.nodes[n]["kind"]
        sym = ["%s-%s" % k for k in sorted(view.links_down) if n in k]
        aff = by_fault.get(f"NODE_DOWN:{n}", set())
        d.faults.append(Fault(f"NODE_DOWN:{n}", "NODE_DOWN", f"{kind.upper()}_FAILURE", n,
                              _severity(len(aff), total, {"router": 4, "server": 3, "switch": 3}.get(kind, 1)), 0.97,
                              f"{kind} {n} unresponsive (power/hardware/destroyed)",
                              [f"heartbeat lost on {n}", f"{len(sym)} adjacent links down = symptoms, not separate faults"],
                              sym, aff))
    for k in sorted(view.links_down):
        if k[0] in view.nodes_down or k[1] in view.nodes_down:
            continue
        key = "LINK_DOWN:%s-%s" % k
        aff = by_fault.get(key, set())
        d.faults.append(Fault(key, "LINK_DOWN", "CABLE_CUT_OR_PORT_FAILURE", "%s-%s" % k, _severity(len(aff), total), 0.93,
                              "both endpoints alive but link unreachable: cable cut / port / transceiver failure",
                              ["probe failures on link while both endpoints answer heartbeats"], [], aff))
    for k, deg in sorted(view.links_degraded.items()):
        if k in view.links_down or k[0] in view.nodes_down or k[1] in view.nodes_down:
            continue
        key = "LINK_DEGRADED:%s-%s" % k
        sub, conf, why = classify_degradation(deg, util.get(k, 0.0))
        aff = by_fault.get(key, set())
        d.faults.append(Fault(key, "LINK_DEGRADED", sub, "%s-%s" % k, _severity(len(aff), total), conf, why,
                              [f"loss={deg['loss']:.0f}%", f"latency x{deg['lat_ratio']:.1f}", f"utilisation={util.get(k, 0):.0%}"], [], aff))
    for k in sorted(suspects):
        key = "LINK_DEGRADED:%s-%s" % k
        if any(f.key == key for f in d.faults):
            continue
        aff = by_fault.get(key, set())
        d.faults.append(Fault(key, "LINK_DEGRADED", "GRAY_FAILURE_INFERRED", "%s-%s" % k, _severity(len(aff), total), 0.65,
                              "probes look healthy but flows crossing this link fail: silent/gray failure (inferred by tomography)",
                              [f"{len(aff)} flows share this link and are losing packets"], [], aff))

    # --- isolation / recoverability ---
    Hall = believed_graph(inv, view, include_standby=True)
    rep, cache = inv.graph["replica"], {}
    for comp in nx.connected_components(Hall):
        if not any(Hall.nodes[n]["kind"] == "router" for n in comp):
            d.isolated_nodes += sorted(n for n in comp if Hall.nodes[n]["kind"] in ("pc", "server"))
    for f in flows:
        ok = any(host_paths(Hall, inv, f.src, dst, k=1, cache=cache) for dst in (f.orig_dst, rep[f.orig_dst]) if dst in Hall)
        if not ok:
            d.unrecoverable.add(f.id)

    # --- partitions (core routers) ---
    for label, H in (("noSB", believed_graph(inv, view)), ("SB", Hall)):
        rs = [n for n in H if H.nodes[n]["kind"] == "router"]
        comps = [sorted(c) for c in nx.connected_components(H.subgraph(rs))]
        if label == "noSB":
            d.partitions = comps if len(comps) > 1 else []
        else:
            d.partition_healable = len(comps) <= 1
    # --- single points of failure in the surviving core ---
    Hc = believed_graph(inv, view)
    Hc.remove_nodes_from([n for n in list(Hc) if Hc.nodes[n]["kind"] == "pc"])
    d.risks = sorted(n for n in nx.articulation_points(Hc) if Hc.nodes[n]["kind"] in ("router", "switch"))
    return d
