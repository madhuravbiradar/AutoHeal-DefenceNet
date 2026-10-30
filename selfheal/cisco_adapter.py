"""Translates abstract recovery actions into Cisco IOS CLI.  Dry-run by default (safe).  Set live=True
and provide credentials to push with netmiko (pip install netmiko) on real devices."""
from .routing import edge_keys


class CiscoAdapter:
    def __init__(self, inv, live=False, creds=None):
        self.inv, self.live, self.creds = inv, live, creds or {}
        self.idx = {}
        for kind, code in (("router", 1), ("switch", 2), ("server", 3), ("pc", 4)):
            for i, n in enumerate(n for n, d in inv.nodes(data=True) if d["kind"] == kind):
                self.idx[n] = f"10.{code}.{i // 250}.{i % 250 + 1}"

    def iface(self, u, v):
        return f"GigabitEthernet0/{sorted(self.inv.neighbors(u)).index(v)}"

    def commands(self, a, flow=None):
        """Return {device: [cli lines]} for one action."""
        out = {}
        if a.kind in ("ACTIVATE_LINK", "DEACTIVATE_LINK"):
            u, v = a.target.split("-")
            cmd = "no shutdown" if a.kind == "ACTIVATE_LINK" else "shutdown"
            out[u] = [f"interface {self.iface(u, v)}", cmd]
            out[v] = [f"interface {self.iface(v, u)}", cmd]
        elif a.kind in ("REROUTE", "FAILOVER", "FAILBACK") and a.path:
            src, dst = a.path[0], a.path[-1]
            for i, n in enumerate(a.path[1:-1], 1):
                if self.inv.nodes[n]["kind"] != "router":
                    continue
                nh = a.path[i + 1]
                out[n] = [f"ip access-list extended SH-{a.target}",
                          f" permit ip host {self.idx[src]} host {self.idx[dst]}",
                          f"route-map SELFHEAL permit {100 + int(a.target[1:])}",
                          f" match ip address SH-{a.target}",
                          f" set ip next-hop {self.idx[nh]}",
                          f"interface {self.iface(n, a.path[i - 1])}",
                          " ip policy route-map SELFHEAL"]
        elif a.kind in ("THROTTLE", "SHED") and flow is not None:
            r1 = next((n for n in (flow.path or []) if self.inv.nodes[n]["kind"] == "router"), None)
            if r1:
                act = "drop" if a.kind == "SHED" else f"police cir {int(flow.demand * a.rate * 1000)} conform-action transmit exceed-action drop"
                out[r1] = [f"class-map match-all SH-{a.target}", f" match access-group name SH-{a.target}",
                           "policy-map SELFHEAL-QOS", f" class SH-{a.target}", f"  {act}"]
        return out

    def push(self, device, lines):
        if not self.live:
            return False
        from netmiko import ConnectHandler                                   # pragma: no cover
        with ConnectHandler(device_type="cisco_ios", host=self.idx[device], **self.creds) as c:
            c.send_config_set(lines)
        return True
