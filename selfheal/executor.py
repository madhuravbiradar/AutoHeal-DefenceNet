"""EXECUTE: applies a plan transactionally (make-before-break ordering) and rolls back on error."""


class Executor:
    def __init__(self, net, adapter):
        self.net, self.adapter = net, adapter
        self.audit = []

    def execute(self, plan):
        snap = self.net.snapshot_control()
        order = {"ACTIVATE_LINK": 0, "REROUTE": 1, "FAILOVER": 1, "FAILBACK": 1, "THROTTLE": 2, "SHED": 2, "DEACTIVATE_LINK": 3}
        cmds = 0
        try:
            for a in sorted(plan.actions, key=lambda x: order[x.kind]):
                flow = self.net.flows.get(a.target)
                dev = self.adapter.commands(a, flow)
                for d, lines in dev.items():
                    cmds += len(lines)
                    self.adapter.push(d, lines)
                if a.kind in ("ACTIVATE_LINK", "DEACTIVATE_LINK"):
                    u, v = a.target.split("-")
                    self.net.set_link_active(u, v, a.kind == "ACTIVATE_LINK")
                else:
                    self.net.apply_flow(a.target, a.path, a.dst, a.rate)
            self.audit.append((self.net.tick_no, plan.name, len(plan.actions), cmds))
            return True, cmds
        except Exception as ex:                                              # roll back everything
            self.net.restore_control(snap)
            return False, str(ex)
