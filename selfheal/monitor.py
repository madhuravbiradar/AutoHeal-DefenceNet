"""MONITOR + DETECT: active probing of every node and link, debounced state machines (no flapping),
and per-flow delivery telemetry.  The healer sees the network only through this module."""
from dataclasses import dataclass, field
from .routing import ek

LOSS_T, LAT_T = 10.0, 2.0                  # degraded if loss >= 10% or latency >= 2x baseline
CONFIRM_BAD, CONFIRM_OK = 2, 3             # consecutive samples needed to declare fault / clear


class _FSM:
    def __init__(self):
        self.state, self.pending, self.count = "OK", None, 0

    def update(self, sample):
        if sample == self.state:
            self.pending, self.count = None, 0
            return False
        if sample == self.pending:
            self.count += 1
        else:
            self.pending, self.count = sample, 1
        if self.count >= (CONFIRM_OK if sample == "OK" else CONFIRM_BAD):
            self.state, self.pending, self.count = sample, None, 0
            return True
        return False


@dataclass
class HealthView:
    tick: int = 0
    version: int = 0
    nodes_down: set = field(default_factory=set)
    links_down: set = field(default_factory=set)
    links_degraded: dict = field(default_factory=dict)   # edge -> {"loss", "lat_ratio"}
    flow_ratio: dict = field(default_factory=dict)       # last-tick delivery ratio
    flow_ewma: dict = field(default_factory=dict)
    bad_streak: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)          # (tick, "NODE"/"LINK", key, new_state)


class Monitor:
    def __init__(self, net, probes=20):
        self.net, self.n = net, probes
        self.base_lat = {ek(u, v): d["latency"] for u, v, d in net.g.edges(data=True)}
        self.node_fsm = {n: _FSM() for n in net.g.nodes}
        self.link_fsm = {ek(u, v): _FSM() for u, v in net.g.edges}
        self.view = HealthView()

    def reset_flows(self, fids):
        for f in fids:
            self.view.bad_streak[f] = 0

    def observe(self, flow_stats, tick):
        v = self.view
        v.tick, v.changes = tick, []
        for n, fsm in self.node_fsm.items():
            if fsm.update("OK" if self.net.probe_node(n) else "DOWN"):
                (v.nodes_down.discard if fsm.state == "OK" else v.nodes_down.add)(n)
                v.version += 1; v.changes.append((tick, "NODE", n, fsm.state))
        for k, fsm in self.link_fsm.items():
            p = self.net.probe_link(*k, n=self.n)
            meas = None
            if not p["up"]:
                sample = "DOWN"
            else:
                meas = {"loss": p["loss"], "lat_ratio": p["latency"] / self.base_lat[k]}
                bad = meas["loss"] >= LOSS_T or meas["lat_ratio"] >= LAT_T
                sample = "DEGRADED" if bad else "OK"
            if fsm.update(sample):
                v.links_down.discard(k)
                v.links_degraded.pop(k, None)
                if fsm.state == "DOWN":
                    v.links_down.add(k)
                v.version += 1; v.changes.append((tick, "LINK", k, fsm.state))
            if fsm.state == "DEGRADED" and meas:             # keep measurements fresh (smoothed)
                old = v.links_degraded.get(k)
                v.links_degraded[k] = {"loss": meas["loss"] if not old else 0.5 * old["loss"] + 0.5 * meas["loss"],
                                       "lat_ratio": meas["lat_ratio"]}
        for fid, (sent, deliv, _) in flow_stats.items():
            r = deliv / sent
            v.flow_ratio[fid] = r
            v.flow_ewma[fid] = 0.5 * r + 0.5 * v.flow_ewma.get(fid, 1.0)
            v.bad_streak[fid] = v.bad_streak.get(fid, 0) + 1 if r < 0.9 else 0
        return v
