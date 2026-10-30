"""Builds the demo network:  7 routers (ring + 4 standby chords), 14 access switches (dual-homed),
112 PCs (8 per switch) and 12 servers (dual-homed, arranged in 6 replica pairs)."""
import networkx as nx

N_ROUTERS, N_SWITCHES, PCS_PER_SWITCH, N_SERVERS = 7, 14, 8, 12


def _link(g, u, v, latency, bw, standby=False, role="access"):
    g.add_edge(u, v, latency=float(latency), bw=float(bw), loss=0.1, silent=0.0, lat_mult=1.0,
               up=True, standby=standby, active=not standby, role=role)


def build_topology():
    g = nx.Graph()
    routers = [f"R{i}" for i in range(1, N_ROUTERS + 1)]
    switches = [f"SW{i}" for i in range(1, N_SWITCHES + 1)]
    servers = [f"SRV{i}" for i in range(1, N_SERVERS + 1)]
    pcs = [f"PC{i}" for i in range(1, N_SWITCHES * PCS_PER_SWITCH + 1)]
    for kind, names in (("router", routers), ("switch", switches), ("server", servers), ("pc", pcs)):
        for n in names:
            g.add_node(n, kind=kind, up=True)
    for i in range(N_ROUTERS):                                   # core ring
        _link(g, routers[i], routers[(i + 1) % N_ROUTERS], 4, 200, role="core")
    for a, b in [(0, 3), (1, 5), (2, 6), (3, 6)]:                # dark standby chords
        _link(g, routers[a], routers[b], 9, 150, standby=True, role="standby")
    for j, sw in enumerate(switches):                            # dual-homed access switches
        _link(g, sw, routers[j % 7], 1, 200, role="uplink")
        _link(g, sw, routers[(j % 7 + 1) % 7], 2, 200, role="uplink")
    for i, s in enumerate(servers):                              # dual-homed servers
        _link(g, s, routers[i % 7], 1, 400, role="server")
        _link(g, s, routers[(i + 1) % 7], 1.5, 400, role="server")
    for k, pc in enumerate(pcs):
        _link(g, pc, switches[k // PCS_PER_SWITCH], 0.2, 100, role="edge")
    g.graph["replica"] = {servers[i]: servers[(i + 6) % 12] for i in range(12)}
    return g


def build_flows(g, demand=4.0):
    from .network import Flow
    servers = [n for n, d in g.nodes(data=True) if d["kind"] == "server"]
    pcs = [n for n, d in g.nodes(data=True) if d["kind"] == "pc"]
    flows = []
    for k, pc in enumerate(pcs):
        prio = 3 if k < 16 else (2 if k < 56 else 1)             # 3 = command traffic, 1 = bulk
        dst = servers[k % len(servers)]
        flows.append(Flow(id=f"F{k + 1}", src=pc, dst=dst, orig_dst=dst, demand=demand, priority=prio))
    return flows
