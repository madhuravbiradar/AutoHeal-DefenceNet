"""The naive 'detect -> reroute' baseline the engine is compared against.

Typical failover logic: after 2 consecutive failed probes, switch to the shortest path that avoids
currently-failing links. No prediction, no confidence, no verification, no memory, no evidence.
"""
import networkx as nx

from simulator.network import DST, SRC, lk


class Baseline:
    def __init__(self, sim, dead_after=2):
        self.sim, self.dead_after, self.misses, self.claims = sim, dead_after, 0, 0

    def step(self):
        sim = self.sim
        sim.tick()
        self.misses = 0 if sim.last_probe["ok"] else self.misses + 1
        if self.misses >= self.dead_after:
            h = nx.Graph()
            for l, r in sim.latest.items():
                if r["healthy"]:
                    h.add_edge(*l.split("-"))
            try:
                path = nx.shortest_path(h, SRC, DST)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return
            if path != sim.route:
                sim.set_route(path)
                self.claims += 1          # it "declares success" without checking
                self.misses = 0
