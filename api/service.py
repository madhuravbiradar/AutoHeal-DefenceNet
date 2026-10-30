"""Runtime service: owns the simulator, engine, store, scenario runner and the background loop.
Kept free of web-framework imports so it can be unit-tested and reused by the CLI demo."""
import hashlib
import os
import secrets
import threading
import time

from db.store import Store
from demo.compare import compare
from demo.scenarios import SCENARIOS, ScenarioRunner
from engine.orchestrator import Orchestrator
from simulator.network import FAULT_KINDS, SITES, TOPOLOGY, Simulator

_SALT = b"selfheal-demo-salt"


def _hash(pw):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), _SALT, 50_000).hex()


USERS = {"operator": {"pw": _hash("operator123"), "role": "operator"},
         "viewer": {"pw": _hash("viewer123"), "role": "viewer"}}


class Runtime:
    def __init__(self, db_path=None, tick_seconds=None):
        self.lock = threading.RLock()
        self.sim = Simulator(7)
        self.store = Store(db_path or os.environ.get("SELFHEAL_DB", "data/selfheal.db"))
        self.orch = Orchestrator(self.sim, self.store)
        self.runner = ScenarioRunner()
        self.tick_seconds = float(tick_seconds or os.environ.get("TICK_SECONDS", 1.0))
        self.tokens = {}
        self._thread = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ loop
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="engine-loop")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            with self.lock:
                self.runner.step(self.sim)
                self.orch.step()
            time.sleep(self.tick_seconds)

    # ------------------------------------------------------------------ auth
    def login(self, username, password):
        u = USERS.get(username)
        if not u or not secrets.compare_digest(u["pw"], _hash(password)):
            return None
        tok = secrets.token_urlsafe(24)
        self.tokens[tok] = {"user": username, "role": u["role"]}
        self.store.add_audit(username, u["role"], "login")
        return {"token": tok, "user": username, "role": u["role"]}

    def who(self, token):
        return self.tokens.get(token)

    # ----------------------------------------------------------------- views
    def state(self):
        with self.lock:
            s = self.sim.state()
            v = self.orch.view()
            for l in s["links"]:
                l["risk"] = v["assess"][l["link"]]["risk"]
                l["trend"] = v["assess"][l["link"]]["trend"]
            return {**s, "mode": v["mode"], "alerts": v["alerts"], "active": v["active"],
                    "failed_fixes": v["failed_fixes"], "known_faults": v["known_faults"],
                    "scenario": self.runner.name, "tick_seconds": self.tick_seconds,
                    "history": {l: [[r["tick"], r["latency_ms"], r["loss_pct"]] for r in list(h)[-40:]]
                                for l, h in self.sim.history.items()}}

    def incidents(self, limit=50):
        with self.lock:
            live = {i["id"]: i for i in self.orch.incidents.values()}
        rows = self.store.incidents(limit)
        return [live.get(r["id"], r) for r in rows]

    def incident(self, iid):
        with self.lock:
            return self.orch.incidents.get(iid) or self.store.get_incident(iid)

    # --------------------------------------------------------------- actions
    def _audit(self, who, action, detail=""):
        self.store.add_audit(who["user"], who["role"], action, detail)

    def approve(self, who, iid):
        with self.lock:
            self.orch.approve(iid, who["user"])
        self._audit(who, "approve", f"incident {iid}")

    def reject(self, who, iid):
        with self.lock:
            self.orch.reject(iid, who["user"])
        self._audit(who, "reject", f"incident {iid}")

    def manual(self, who, iid, action, params):
        if action not in ("repair_link", "restart_link", "switch_server", "reroute"):
            raise ValueError("unsupported manual action")
        with self.lock:
            self.orch.manual(iid, who["user"], action, params)
        self._audit(who, "manual", f"incident {iid}: {action} {params}")

    def set_mode(self, who, mode):
        with self.lock:
            self.orch.set_mode("auto" if mode == "auto" else "paused")
        self._audit(who, "mode", mode)

    def inject(self, who, kind, target):
        if kind not in FAULT_KINDS or (target not in SITES if kind == "server_down" else target not in TOPOLOGY):
            raise ValueError("bad fault kind or target")
        with self.lock:
            self.sim.inject(kind, target)
        self._audit(who, "inject", f"{kind} {target}")

    def reset(self, who, wipe=False):
        with self.lock:
            self.runner.stop()
            self.sim.reset()
            if wipe:
                self.store.wipe()
            self.orch.reset()
        self._audit(who, "reset", "wipe" if wipe else "")

    def start_scenario(self, who, name):
        if name not in SCENARIOS:
            raise KeyError(name)
        with self.lock:
            self.sim.reset()
            self.orch.reset()
            self.runner.start(name, self.sim.t)
        self._audit(who, "scenario", name)

    def set_speed(self, who, seconds):
        self.tick_seconds = max(0.1, min(5.0, float(seconds)))
        self._audit(who, "speed", str(self.tick_seconds))

    @staticmethod
    def scenarios():
        return [{"id": k, "title": v["title"], "story": v["story"]} for k, v in SCENARIOS.items()]

    @staticmethod
    def compare():
        return compare()
