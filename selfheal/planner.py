"""PLAN: builds several complete recovery strategies, predicts the outcome of each on a capacity model
of the surviving network, and picks the best.  Priority-aware (command traffic > bulk), capacity-aware,
and aware of the cost of using standby links, failing over to replica servers and shedding traffic."""
from collections import defaultdict
from dataclasses import dataclass, field
from .diagnosis import believed_graph
from .routing import ek, edge_keys, host_paths

LOSS_EXCLUDE = 25.0        # links lossier than this are treated as unusable
HEADROOM = 0.85            # plan to use at most 85% of link capacity
STANDBY_PENALTY, FAILOVER_PENALTY, SUSPECT_LOSS = 8.0, 12.0, 35.0

STRATEGIES = [
    dict(name="REROUTE_ONLY", standby=False, failover=False, preempt=False),
    dict(name="REROUTE+STANDBY", standby=True, failover=False, preempt=False),
    dict(name="REROUTE+STANDBY+FAILOVER", standby=True, failover=True, preempt=False),
    dict(name="FULL_RECOVERY(+PREEMPT)", standby=True, failover=True, preempt=True),
]


@dataclass
class Action:
    kind: str                # REROUTE FAILOVER FAILBACK THROTTLE SHED ACTIVATE_LINK DEACTIVATE_LINK
    target: str
    path: list = None
    dst: str = None
    rate: float = 1.0
    reason: str = ""


@dataclass
class Plan:
    name: str
    actions: list
    score: float
    metrics: dict
    assign: dict = field(repr=False, default_factory=dict)


class Planner:
    def __init__(self, inv, view, flows, suspects):
        self.inv, self.view, self.suspects = inv, view, set(suspects)
        self.flows = {f.id: f for f in flows}
        self.replica = inv.graph["replica"]

    # ---------- capacity / cost model ----------
    def _cap(self, k):
        loss = self.view.links_degraded.get(k, {}).get("loss", 0.0)
        return self.inv.edges[k]["bw"] * (1 - min(loss, 90) / 100)

    def _graph(self, standby):
        H = believed_graph(self.inv, self.view, include_standby=standby, exclude_edges=self.suspects)
        H.remove_edges_from([(u, v) for u, v, d in H.edges(data=True) if d["loss"] >= LOSS_EXCLUDE])
        for u, v, d in H.edges(data=True):
            d["w"] = d["latency"] * d["lat_ratio"] + 1.5 * d["loss"] + (STANDBY_PENALTY if d["standby"] else 0)
        return H

    @staticmethod
    def _cost(H, path):
        return sum(H[a][b]["w"] for a, b in zip(path, path[1:]))

    def _fits(self, path, demand, load):
        return all(load[k] + demand <= self._cap(k) * HEADROOM for k in edge_keys(path))

    # ---------- strategy construction ----------
    def _build(self, st, affected):
        H, cache = self._graph(st["standby"]), {}
        assign, load = {}, defaultdict(float)
        for f in self.flows.values():
            if f.id not in affected:
                assign[f.id] = [f.path, f.dst, f.rate]
                if f.path and f.rate > 0:
                    for k in edge_keys(f.path):
                        load[k] += f.demand * f.rate
        for f in sorted((self.flows[i] for i in affected), key=lambda x: (-x.priority, x.id)):
            opts = []
            for d in [f.orig_dst] + ([self.replica[f.orig_dst]] if st["failover"] else []):
                if d in self.view.nodes_down:
                    continue
                for p in host_paths(H, self.inv, f.src, d, k=3, weight="w", cache=cache):
                    opts.append((self._cost(H, p) + (FAILOVER_PENALTY if d != f.orig_dst else 0), p, d))
            opts.sort(key=lambda o: o[0])
            if not opts:
                assign[f.id] = [None, f.dst, f.rate]; continue
            chosen = next(((p, d, 1.0) for c, p, d in opts if self._fits(p, f.demand, load)), None)
            if chosen is None:
                _, p, d = opts[0]
                if st["preempt"]:
                    if self._preempt(f, p, assign, load):
                        chosen = (p, d, 1.0)
                    else:
                        res = min(self._cap(k) * HEADROOM - load[k] for k in edge_keys(p))
                        r = max(0.0, res) / f.demand
                        chosen = (p, d, 0.0 if r < 0.1 else min(1.0, r))
                else:
                    chosen = (p, d, 1.0)                            # best effort: oversubscribe
            p, d, r = chosen
            assign[f.id] = [p, d, r]
            for k in edge_keys(p):
                load[k] += f.demand * r
        return assign

    def _preempt(self, f, path, assign, load):
        keys, done = edge_keys(path), []

        def deficit():
            return {k for k in keys if load[k] + f.demand > self._cap(k) * HEADROOM}
        while deficit():
            dfc = deficit()
            cands = [(self.flows[i].priority, -self.flows[i].demand * a[2], i) for i, a in assign.items()
                     if a[0] and a[2] > 0 and self.flows[i].priority < f.priority and dfc & set(edge_keys(a[0]))]
            if not cands:
                for i, old in done:                                 # undo
                    assign[i][2] = old
                    for k in edge_keys(assign[i][0]):
                        load[k] += self.flows[i].demand * old
                return False
            i = min(cands)[2]
            old = assign[i][2]
            for k in edge_keys(assign[i][0]):
                load[k] -= self.flows[i].demand * old
            assign[i][2] = 0.0
            done.append((i, old))
        return True

    # ---------- outcome prediction ----------
    def evaluate(self, assign):
        load = defaultdict(float)
        for i, (p, d, r) in assign.items():
            if p and r > 0:
                for k in edge_keys(p):
                    load[k] += self.flows[i].demand * r
        wsum = wsrv = lat_sum = n_srv = 0.0
        for i, (p, d, r) in assign.items():
            f = self.flows[i]; w = 2 ** (f.priority - 1); wsum += w
            s, lat = self._served(p, d, r, load)
            wsrv += w * s
            if s > 0.5:
                lat_sum += lat; n_srv += 1
        return {"served": wsrv / wsum, "avg_latency": lat_sum / max(1, n_srv)}

    def _served(self, p, d, r, load):
        if not p or r <= 0 or d in self.view.nodes_down:
            return 0.0, 0.0
        s, lat = r, 0.0
        for a, b in zip(p, p[1:]):
            k = ek(a, b)
            if a in self.view.nodes_down or b in self.view.nodes_down or k in self.view.links_down:
                return 0.0, 0.0
            deg = self.view.links_degraded.get(k, {})
            s *= 1 - deg.get("loss", 0.0) / 100
            if k in self.suspects:
                s *= 1 - SUSPECT_LOSS / 100
            bw = self.inv.edges[k]["bw"]
            if load[k] > bw:
                s *= bw / load[k]
            lat += self.inv.edges[k]["latency"] * deg.get("lat_ratio", 1.0)
        return s, lat

    def _score(self, assign, m):
        std = {k for i, (p, d, r) in assign.items() if p and r > 0 for k in edge_keys(p)
               if self.inv.edges[k]["standby"] and not self.inv.edges[k]["active"]}
        changed = sum(1 for i, a in assign.items() if a != [self.flows[i].path, self.flows[i].dst, self.flows[i].rate])
        failovers = sum(1 for i, a in assign.items() if a[1] != self.flows[i].orig_dst)   # flows living on a replica
        m.update(standby_used=len(std), changed=changed, failovers=failovers)
        return 1000 * m["served"] - 1.0 * m["avg_latency"] - 0.05 * changed - 5 * len(std) - 1.0 * failovers

    def _actions(self, assign):
        acts, used, empty = [], set(), defaultdict(float)
        for i, (p, d, r) in assign.items():
            if p and r > 0 and self._served(p, d, r, empty)[0] > 0:      # dead paths do not pin standby links
                used.update(edge_keys(p))
        for k in sorted(used):
            e = self.inv.edges[k]
            if e["standby"] and not e["active"]:
                acts.append(Action("ACTIVATE_LINK", "%s-%s" % k, reason="needed to bypass failure / restore connectivity"))
        for i, (p, d, r) in assign.items():
            f = self.flows[i]
            if [p, d, r] == [f.path, f.dst, f.rate]:
                continue
            if r == 0 and f.rate > 0:
                kind, why = "SHED", "preempted by higher-priority traffic; no capacity"
            elif 0 < r < 1 and r != f.rate:
                kind, why = "THROTTLE", "partial capacity"
            elif d != f.dst:
                kind, why = ("FAILBACK", "primary server healthy again") if d == f.orig_dst else ("FAILOVER", f"{f.dst} unreachable/down; use replica")
            else:
                kind, why = "REROUTE", "path impacted by fault or sub-optimal after recovery"
            acts.append(Action(kind, i, p, d, r, why))
        for u, v, e in self.inv.edges(data=True):
            if e["standby"] and e["active"] and ek(u, v) not in used:
                acts.append(Action("DEACTIVATE_LINK", "%s-%s" % ek(u, v), reason="standby link no longer needed"))
        return acts

    def plan(self, affected):
        cur = {i: [f.path, f.dst, f.rate] for i, f in self.flows.items()}
        cm = self.evaluate(cur); cur_score = self._score(cur, cm)
        plans = []
        for st in STRATEGIES:
            a = self._build(st, affected)
            m = self.evaluate(a)
            plans.append(Plan(st["name"], self._actions(a), self._score(a, m), m, a))
        best = max(plans, key=lambda p: (round(p.score, 6), -plans.index(p)))
        return best, plans, Plan("CURRENT(do nothing)", [], cur_score, cm, cur)
