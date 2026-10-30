"""Shared routing helpers.  Only routers may carry transit traffic; switches, servers and
PCs are leaf/access nodes (a switch dual-homed to two routers must not become a shortcut)."""
from itertools import islice
import networkx as nx


def ek(u, v):
    """Canonical (undirected) edge key."""
    return (u, v) if u <= v else (v, u)


def edge_keys(path):
    return [ek(a, b) for a, b in zip(path, path[1:])]


def anchor_of(inv, node):
    """A PC enters the core through its (single) access switch."""
    if inv.nodes[node]["kind"] == "pc":
        return next(iter(inv.neighbors(node)))
    return node


def core_paths(H, anchor, dst, k=3, weight="latency"):
    if anchor not in H or dst not in H:
        return []
    nodes = {n for n, d in H.nodes(data=True) if d.get("kind") == "router"} | {anchor, dst}
    try:
        return list(islice(nx.shortest_simple_paths(H.subgraph(nodes), anchor, dst, weight=weight), k))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def host_paths(H, inv, src, dst, k=3, weight="latency", cache=None):
    a = anchor_of(inv, src)
    if a != src and not H.has_edge(src, a):
        return []
    key = (a, dst, k)
    if cache is not None and key in cache:
        core = cache[key]
    else:
        core = core_paths(H, a, dst, k, weight)
        if cache is not None:
            cache[key] = core
    return [([src] + p) if a != src else p for p in core]
