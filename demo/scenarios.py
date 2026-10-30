"""Scripted demo cases. Events are (second offset, fault kind, target)."""
SCENARIOS = {
    "predicted": {"title": "1 · Predicted failure", "ticks": 40, "events": [(3, "degrade", "A-B")],
                  "story": "A-B degrades gradually. The risk score crosses the warning line and the system reroutes BEFORE any outage."},
    "sudden": {"title": "2 · Sudden failure", "ticks": 40, "events": [(3, "down", "A-B")],
               "story": "A-B dies instantly. Evidence is gathered for one probe, then the system reroutes via C and proves it."},
    "backup_fails": {"title": "3 · Backup also fails", "ticks": 50, "events": [(3, "down", "A-B"), (20, "degrade", "B-C")],
                     "story": "After rerouting via C, the backup path starts degrading. No alternative path exists, so restarting the link is risky and the operator must approve."},
    "intermittent": {"title": "4 · Intermittent link", "ticks": 40, "events": [(3, "flap", "A-B")],
                     "story": "A-B drops for one second every three. A simple 'detect then reroute' baseline never triggers; the engine spots the pattern."},
    "auto_heal_fails": {"title": "5 · Auto-heal fails, operator recovers", "ticks": 60, "events": [(3, "down", "A-B"), (3, "latent", "B-C")],
                        "story": "The backup path hides a latent fault that only bites under load. The reroute is proven Failed, the fix is blacklisted, the engine escalates and the operator repairs the cable."},
    "server_failure": {"title": "6 · Server failure (bonus)", "ticks": 40, "events": [(3, "server_down", "B")],
                       "story": "Site B's server stops answering on every link. The engine blames the server, not a link, and fails over to the standby."},
}


class ScenarioRunner:
    def __init__(self):
        self.name, self.t0, self.fired = None, 0, set()

    def start(self, name, sim_tick):
        if name not in SCENARIOS:
            raise KeyError(name)
        self.name, self.t0, self.fired = name, sim_tick, set()

    def stop(self):
        self.name = None

    def step(self, sim):
        """Call before sim/orchestrator step; fires events due at this second."""
        if not self.name:
            return []
        fired = []
        for i, (off, kind, target) in enumerate(SCENARIOS[self.name]["events"]):
            if i not in self.fired and sim.t - self.t0 >= off:
                sim.inject(kind, target)
                self.fired.add(i)
                fired.append((kind, target))
        return fired
