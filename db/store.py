"""SQLite evidence store: every incident is saved with fault, evidence, plan, decision, proof, recovery time, approver."""
import json
import sqlite3
import threading
from datetime import datetime


class Store:
    def __init__(self, path="data/selfheal.db"):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS incidents(
              id INTEGER PRIMARY KEY, opened_at TEXT, opened_tick INTEGER, fault TEXT, target TEXT,
              status TEXT, decision TEXT, verdict TEXT, recovery_seconds INTEGER, approved_by TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS alerts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, tick INTEGER, time TEXT, level TEXT, kind TEXT,
              link TEXT, risk REAL, message TEXT);
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
              full_name TEXT NOT NULL, organization TEXT, role TEXT NOT NULL, salt TEXT NOT NULL,
              pw_hash TEXT NOT NULL, created_at TEXT);
            CREATE TABLE IF NOT EXISTS audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, user TEXT, role TEXT, action TEXT, detail TEXT);
            """)

    def next_incident_id(self):
        with self.lock:
            return (self.db.execute("SELECT COALESCE(MAX(id),0) FROM incidents").fetchone()[0]) + 1

    def save_incident(self, inc):
        proof = inc.get("proof") or {}
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (inc["id"], inc["opened_at"], inc["opened_tick"], inc["fault"], inc["target"], inc["status"],
                 inc.get("decision"), proof.get("verdict"), inc.get("recovery_ticks"), inc.get("approved_by"),
                 json.dumps(inc, default=str)))
            self.db.commit()

    def incidents(self, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT data FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_incident(self, iid):
        with self.lock:
            r = self.db.execute("SELECT data FROM incidents WHERE id=?", (iid,)).fetchone()
        return json.loads(r[0]) if r else None

    def add_alert(self, a):
        with self.lock:
            self.db.execute("INSERT INTO alerts(tick,time,level,kind,link,risk,message) VALUES (?,?,?,?,?,?,?)",
                            (a["tick"], a["time"], a["level"], a["kind"], a["link"], a["risk"], a["message"]))
            self.db.commit()

    def alerts(self, limit=50):
        with self.lock:
            rows = self.db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def add_audit(self, user, role, action, detail=""):
        with self.lock:
            self.db.execute("INSERT INTO audit(time,user,role,action,detail) VALUES (?,?,?,?,?)",
                            (datetime.now().isoformat(timespec="seconds"), user, role, action, detail))
            self.db.commit()

    def audit(self, limit=50):
        with self.lock:
            rows = self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def add_user(self, username, email, full_name, organization, role, salt, pw_hash):
        with self.lock:
            self.db.execute("INSERT INTO users(username,email,full_name,organization,role,salt,pw_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (username, email, full_name, organization, role, salt, pw_hash,
                             datetime.now().isoformat(timespec="seconds")))
            self.db.commit()

    def find_user(self, identifier):
        """Look up by username OR email (case-insensitive)."""
        ident = identifier.strip().lower()
        with self.lock:
            r = self.db.execute("SELECT * FROM users WHERE lower(username)=? OR lower(email)=?", (ident, ident)).fetchone()
        return dict(r) if r else None

    def wipe(self):
        with self.lock:
            self.db.executescript("DELETE FROM incidents; DELETE FROM alerts; DELETE FROM audit;")
            self.db.commit()
