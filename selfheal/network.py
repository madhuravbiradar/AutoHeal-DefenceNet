"""Simulated data plane (stands in for real routers).  Holds ground-truth state, injects faults,
forwards packets and answers probe requests.  The healer never reads ground truth directly:
it only sees probe results, exactly as it would on real hardware."""
import random
from dataclasses import dataclass
import networkx as nx
from .routing import ek, edge_keys, host_paths


@dataclass
class Flow:
    id: str
    src: str
    dst: str
    orig_dst: str
    demand: float
    priority: int
    path: list = None
    home_path: list = None
    rate: float = 1.0


class SimNetwork:
    def __init__(self, g, flows, seed=7, pkts_per_tick=10):
        self.g, self.flows = g, {f.id: f for f in flows}
        self.rng, self.ppt = random.Random(seed), pkts_per_tick
        self.tick_no, self.last_load, self.injected = 0, {}, []
        self._initial_routing()

    # ---------------- state helpers ----------------
    def node_up(self, n):
        return self.g.nodes[n]["up"]

    def link_phys_up(self, u, v):
        return self.g[u][v]["up"] and self.node_up(u) and self.node_up(v)

    def edge_usable(self, u, v):
        return self.link_phys_up(u, v) and self.g[u][v]["active"]

    def path_ok(self, path):
        return all(self.node_up(n) for n in path) and all(self.edge_usable(a, b) for a, b in zip(path, path[1:]))

    def usable_graph(self):
        H = nx.Graph()
        for n, d in self.g.nodes(data=True):
            if d["up"]:
                H.add_node(n, kind=d["kind"])
        for u, v, d in self.g.edges(data=True):
            if self.edge_usable(u, v):
                H.add_edge(u, v, latency=d["latency"] * d["lat_mult"])
        return H

    def _initial_routing(self):
        H = self.usable_graph()
        for f in self.flows.values():
            p = host_paths(H, self.g, f.src, f.dst, k=1)
            f.path = f.home_path = (p[0] if p else None)

    # ---------------- fault injection ----------------
    def _log(self, key, text):
        self.injected.append((self.tick_no + 1, key, text))

    def fail_link(self, u, v):
        self.g[u][v]["up"] = False; self._log("LINK_DOWN:%s-%s" % ek(u, v), f"cut {u}<->{v}")

    def restore_link(self, u, v):
        self.g[u][v].update(up=True, loss=0.1, silent=0.0, lat_mult=1.0); self._log("RESTORE", f"restore {u}<->{v}")

    def fail_node(self, n):
        self.g.nodes[n]["up"] = False; self._log(f"NODE_DOWN:{n}", f"{n} destroyed")

    def restore_node(self, n):
        self.g.nodes[n]["up"] = True; self._log("RESTORE", f"restore {n}")

    def degrade_link(self, u, v, loss=0.0, latency_mult=1.0, silent=0.0):
        e = self.g[u][v]; e["loss"] = max(e["loss"], loss); e["lat_mult"] = latency_mult; e["silent"] = silent
        self._log("LINK_DEGRADED:%s-%s" % ek(u, v), f"degrade {u}<->{v} loss={loss}% silent={silent}%")

    # ---------------- data plane ----------------
    def _util(self, e, k):
        return self.last_load.get(k, 0.0) / e["bw"]

    def _survival(self, e, k, silent=True):
        load = self.last_load.get(k, 0.0)
        cong = (1 - e["bw"] / load) if load > e["bw"] else 0.0
        s = (1 - e["loss"] / 100) * (1 - cong)
        return s * (1 - e["silent"] / 100) if silent else s

    def _latency(self, e, k):
        return e["latency"] * e["lat_mult"] * (1 + 4 * max(0.0, self._util(e, k) - 0.8))

    def step(self):
        """Advance one tick: offer traffic on every flow, return {flow: (sent, delivered, latency)}."""
        self.tick_no += 1
        load = {}
        for f in self.flows.values():
            if f.path and self.path_ok(f.path):
                for k in edge_keys(f.path):
                    load[k] = load.get(k, 0.0) + f.demand * f.rate
        self.last_load = load
        out = {}
        for f in self.flows.values():
            p, lat = 0.0, None
            if f.path and self.path_ok(f.path):
                p, lat = f.rate, 0.0
                for a, b in zip(f.path, f.path[1:]):
                    e, k = self.g[a][b], ek(a, b)
                    p *= self._survival(e, k); lat += self._latency(e, k)
            d = sum(1 for _ in range(self.ppt) if self.rng.random() < p)
            out[f.id] = (self.ppt, d, lat)
        return out

    # ---------------- probes (what a monitor can observe) ----------------
    def probe_node(self, n):
        return self.node_up(n)

    def probe_link(self, u, v, n=20):
        e = self.g[u][v]
        if not self.link_phys_up(u, v):
            return {"up": False}
        k = ek(u, v)
        ploss = 1 - self._survival(e, k, silent=False)           # silent loss is invisible to probes
        lost = sum(1 for _ in range(n) if self.rng.random() < ploss)
        return {"up": True, "loss": 100.0 * lost / n, "latency": self._latency(e, k) * (1 + self.rng.gauss(0, 0.03))}

    # ---------------- control plane (what the executor may change) ----------------
    def apply_flow(self, fid, path, dst, rate):
        f = self.flows[fid]; f.path, f.dst, f.rate = path, dst, rate

    def set_link_active(self, u, v, active):
        self.g[u][v]["active"] = active

    def snapshot_control(self):
        return ({i: (f.path, f.dst, f.rate) for i, f in self.flows.items()},
                {ek(u, v): d["active"] for u, v, d in self.g.edges(data=True) if d["standby"]})

    def restore_control(self, snap):
        flows, act = snap
        for i, (p, d, r) in flows.items():
            self.apply_flow(i, p, d, r)
        for (u, v), a in act.items():
            self.set_link_active(u, v, a)
