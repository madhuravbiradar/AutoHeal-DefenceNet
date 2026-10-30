"""Executor (simulated): applies a plan to the network model. Swap this module to drive real devices."""


def execute(sim, plan):
    a, p = plan["action"], plan["params"]
    if a == "reroute":
        sim.set_route(p["path"])
        return {"ok": True, "detail": f"Route is now {' → '.join(p['path'])}"}
    if a == "restart_link":
        ok = sim.restart_link(p["link"])
        return {"ok": ok, "detail": f"Link {p['link']} restarted" if ok else f"Restart of {p['link']} did not clear the fault"}
    if a == "switch_server":
        ok = sim.switch_server(p["node"])
        return {"ok": ok, "detail": f"Site {p['node']} now on standby server" if ok else "Standby switch failed"}
    if a == "flag_link":
        sim.avoid_links.add(p["link"])
        return {"ok": True, "detail": f"{p['link']} excluded from routing"}
    if a == "repair_link":
        sim.repair_link(p["link"])
        sim.avoid_links.discard(p["link"])
        sim.set_route(sim.best_route())
        return {"ok": True, "detail": f"Field repair on {p['link']}; route {' → '.join(sim.route)}"}
    return {"ok": False, "detail": f"unknown action {a}"}
