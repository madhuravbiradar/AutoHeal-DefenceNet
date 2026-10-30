"""Simulated 3-site network (the 'digital twin').

Everything the engine knows arrives as *readings* produced here:
    link reading : {link, latency_ms, loss_pct, load_pct, healthy, tick, time}
    node reading : {node, responding, tick}
The engine never looks at the injected faults (ground truth); only the dashboard
console and the headless operator policy may.
"""
import random
from collections import deque
from datetime import datetime

import networkx as nx

SRC, DST, DEMAND = "A", "B", 40          # the monitored service flow A -> B (Mbps)
SITES = {"A": "Site A · HQ", "B": "Site B · Data Center", "C": "Site C · Branch"}
# link -> (base latency ms, capacity Mbps, background load Mbps)
TOPOLOGY = {"A-B": (20, 100, 10), "A-C": (25, 80, 25), "B-C": (30, 80, 20)}
LAT_LIMIT, LOSS_LIMIT = 120.0, 5.0       # a reading is "healthy" below both limits
SOFT_FAULTS = {"degrade", "flap"}        # faults a link restart can clear
FAULT_KINDS = {"degrade", "down", "flap", "latent", "server_down"}


def lk(u, v):
    return "-".join(sorted((u, v)))


class Simulator:
    def __init__(self, seed=7):
        self.seed = seed
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        self.rng = random.Random(self.seed)
        self.t = 0
        self.g = nx.Graph()
        for n, name in SITES.items():
            self.g.add_node(n, name=name)
        for l, (lat, cap, bg) in TOPOLOGY.items():
            u, v = l.split("-")
            self.g.add_edge(u, v, name=l, base_latency=lat, capacity=cap, bg_load=bg)
        self.faults = {}            # ground truth: link -> {mode, since}
        self.server_down = set()    # ground truth: nodes whose active server is dead
        self.standby = set()        # nodes running on their standby server
        self.avoid_links = set()    # routing policy written by the executor
        self.route = [SRC, DST]
        self.history = {l: deque(maxlen=60) for l in TOPOLOGY}
        self.node_hist = {n: deque(maxlen=60) for n in SITES}
        self.latest, self.node_latest = {}, {}
        self.service_down_ticks = 0
        self.last_probe = None
        self.log = deque(maxlen=200)
        self.tick()                  # initial healthy reading set (so state() always works)
        self.t = 0
        self.service_down_ticks = 0
        for h in list(self.history.values()) + list(self.node_hist.values()):
            h.clear()

    def route_links(self, route=None):
        r = route or self.route
        return [lk(a, b) for a, b in zip(r, r[1:])]

    # ------------------------------------------------------------------- tick
    def tick(self):
        self.t += 1
        stamp = datetime.now().strftime("%H:%M:%S")
        on_route = set(self.route_links())
        for n in SITES:
            r = {"node": n, "responding": n not in self.server_down, "tick": self.t}
            self.node_latest[n] = r
            self.node_hist[n].append(r)
        for name, (base, cap, bg) in TOPOLOGY.items():
            u, v = name.split("-")
            load = bg + (DEMAND if name in on_route else 0)
            lat = base + self.rng.gauss(0, 1.5)
            loss = abs(self.rng.gauss(0.15, 0.1))
            f = self.faults.get(name)
            if f:
                age, mode = self.t - f["since"], f["mode"]
                if mode == "degrade":
                    lat += 9 * age
                    loss += 0.5 * age
                elif mode == "down":
                    lat, loss = 999.0, 100.0
                elif mode == "flap" and age % 3 == 0:      # 1-tick outage every 3 s
                    lat, loss = 999.0, 100.0
                elif mode == "latent" and load / cap > 0.5:  # only bites under traffic
                    lat += 105
                    loss += 25
            if u in self.server_down or v in self.server_down:
                lat, loss = 999.0, 100.0
            lat, loss = min(lat, 999.0), min(loss, 100.0)
            r = {"link": name, "latency_ms": round(lat, 1), "loss_pct": round(loss, 2),
                 "load_pct": round(100 * load / cap, 1),
                 "healthy": lat < LAT_LIMIT and loss < LOSS_LIMIT,
                 "tick": self.t, "time": stamp}
            self.latest[name] = r
            self.history[name].append(r)
        self.last_probe = self.probe()
        if not self.last_probe["ok"]:
            self.service_down_ticks += 1
        return self.last_probe

    def probe(self):
        """End-to-end probe of the live route (what a ping/iperf would measure)."""
        rs = [self.latest[l] for l in self.route_links()]
        keep = 1.0
        for r in rs:
            keep *= 1 - r["loss_pct"] / 100
        return {"tick": self.t, "path": "→".join(self.route),
                "latency_ms": round(sum(r["latency_ms"] for r in rs), 1),
                "loss_pct": round((1 - keep) * 100, 2),
                "ok": all(r["healthy"] for r in rs)}

    # ------------------------------------------------- fault injection / fixes
    def inject(self, kind, target):
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind}")
        if kind == "server_down":
            self.server_down.add(target)
        else:
            self.faults[target] = {"mode": kind, "since": self.t}
        self.log.append(f"t={self.t} INJECT {kind} {target}")

    def restart_link(self, link):
        f = self.faults.get(link)
        if f is None:
            return True
        if f["mode"] in SOFT_FAULTS:
            del self.faults[link]
            return True
        return False               # hard / latent faults survive a restart

    def switch_server(self, node):
        if node in self.server_down:
            self.server_down.discard(node)
            self.standby.add(node)
        return node not in self.server_down

    def repair_link(self, link):   # field crew replaces the cable / port
        self.faults.pop(link, None)

    def set_route(self, path):
        self.route = list(path)

    def best_route(self):
        """Shortest route avoiding links with a (ground-truth) fault - used by field repairs."""
        h = nx.Graph()
        for l, (lat, _, _) in TOPOLOGY.items():
            if l not in self.faults:
                h.add_edge(*l.split("-"), w=lat)
        try:
            return nx.shortest_path(h, SRC, DST, weight="w")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return self.route

    # ---------------------------------------------------------------- snapshot
    def state(self):
        links = []
        for l, (base, cap, bg) in TOPOLOGY.items():
            r = dict(self.latest[l])
            r.update(base_latency=base, capacity=cap, on_route=l in self.route_links(),
                     injected=self.faults.get(l, {}).get("mode"))
            links.append(r)
        nodes = [{"id": n, "name": SITES[n], "responding": self.node_latest[n]["responding"],
                  "standby": n in self.standby, "injected": "server_down" if n in self.server_down else None}
                 for n in SITES]
        return {"tick": self.t, "route": self.route, "nodes": nodes, "links": links,
                "service": self.last_probe, "downtime_ticks": self.service_down_ticks}
