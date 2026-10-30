# Self-Healing Network — hackathon demo

A closed-loop network control plane: **predict → diagnose → plan → confidence gate → execute → prove → record**,
running on a simulated 3-site network (A, B, C) with a live operations dashboard.

```
 Simulator (NetworkX digital twin)          readings: {link, latency_ms, loss_pct, healthy, tick}
        │  + failure injection                         {node, responding}
        ▼
 Predictor ─► Diagnosis ─► Planner ─► Confidence Gate ─► Executor ─► Proof Engine
 (risk 0-100)  (rules +     (scored     (4 rules)         (changes     (Verified /
               evidence)    candidates                    the graph)   Partial / Failed)
                            + fallback)                       │
        ▼                                                     ▼
 SQLite evidence records  ◄─────────────────────────  Orchestrator (one incident at a time)
        ▼
 FastAPI (roles: operator / viewer)  ◄──►  HTML dashboard (polls the API)
```

## Quick start (2 minutes)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                                          # opens http://localhost:8000
```

Open http://localhost:8000 → you land on **Sign in** (`/login`); new users use **Create an account** (`/signup`).
Required fields are marked with a red **\***. Demo accounts: **operator / operator123** (approve, pause, inject, run scenarios) · **viewer / viewer123** (read-only).
Self-registered *viewer* accounts need nothing extra; an *operator* account needs the access code `HEAL-OPS-2026`
(change it with the env var `SELFHEAL_OPERATOR_CODE`). Accounts live in `data/selfheal.db`; passwords are salted PBKDF2 hashes.

No web server needed for a quick check: `python run_demo.py -v` replays all scenarios and prints the
engine-vs-baseline comparison with full incident timelines. Tests: `python -m unittest discover -s tests -v`.

## Demo script (≈5 min) — click the scenario buttons, operator login

| # | Scenario | What you see | What to say |
|---|----------|--------------|-------------|
| 1 | Predicted failure | A-B turns amber, risk climbs, system reroutes via C **before** any outage, proof = Verified, downtime 0 s | "It acted on a trend, not on a failure." |
| 2 | Sudden failure | Link goes red, 1 probe of evidence gathering, auto-reroute, proof, incident saved | "Moderate confidence waits; high confidence + low risk acts alone." |
| 3 | Backup also fails | After the reroute B-C degrades; no alternative path, so a restart is high risk → **Approve** button appears. Click it | "High risk goes to a human. Every approval is audited." |
| 4 | Intermittent link | A-B drops 1 s in every 3. Engine labels it *intermittent_link* and reroutes | "A detect-then-reroute script never triggers on 1-second glitches." |
| 5 | Auto-heal fails, operator recovers | Reroute proven **Failed**, fix blacklisted, re-diagnosis, restart approved and Failed, escalated; use *Field repair* in the console | "It never repeats a failed fix and it knows when to hand over." |
| 6 | Server failure (bonus) | All links to B unhealthy + heartbeat lost → *server_failure*, standby switch | "Same symptoms, different root cause." |

Finish with **Run comparison** (right panel) — engine vs a naive baseline on all six scenarios.
Use the speed selector to run 2×/4× faster. Record a backup screen capture of a full run.

## What's where

| Path | Role |
|------|------|
| `simulator/network.py` | NetworkX graph, per-second readings, fault injection (degrade, down, flap, latent, server_down) |
| `engine/predict.py` | rolling averages + trend → risk score, warning at 45, critical at 75 |
| `engine/diagnose.py` | if-then rules; each diagnosis carries its evidence |
| `engine/planner.py` | candidates (reroute, restart link, switch server, flag link), weighted score, fallback, failed-fix blacklist |
| `engine/gate.py` | the four Confidence Gate rules |
| `engine/executor.py`, `engine/proof.py` | apply the plan; probe 4× → Verified / Partial / Failed |
| `engine/orchestrator.py` | the state machine; reassess once after a failed proof, then escalate |
| `db/store.py` | SQLite: incidents (fault, evidence, plan, decision, proof, recovery time, approver), alerts, audit log |
| `api/service.py`, `api/main.py` | engine loop + auth (service) and the FastAPI endpoints |
| `dashboard/signin.html`, `signup.html`, `auth.css` | sign-in and sign-up pages (required fields marked with *) |
| `dashboard/index.html` | network map, risk bars, latency chart, incident card with approve/reject/manual console |
| `demo/` | scenarios, naive baseline, headless comparison |
| `tools/pt_check.py` | 30-minute Packet Tracer feasibility probe (Plan B) |

API (all under `/api`, Bearer token from `POST /login`): `POST signup, login, logout` · `GET state, alerts, incidents, incidents/{id}, audit, scenarios, compare` ·
operator only: `POST incidents/{id}/approve | reject | manual`, `pause`, `resume`, `inject`, `reset`, `scenarios/{name}/start`, `speed`.
Interactive docs at `/docs`.

## Design decisions worth defending

* **Rule-based, explainable, no ML** — every verdict shows its evidence, every plan its risk and fallback.
* **Confidence ≠ plan quality** — gate confidence = diagnosis confidence × (plan success estimate / 0.7), so a futile fix is routed to a human.
* **One incident at a time** — serialised changes avoid fixes fighting each other (change-management style).
* **Proof before success** — an incident only closes as *resolved* after 4 post-fix probes pass.
* **Failed-fix memory** — a fix proven Failed is never proposed again.

## Honest limitations (say these before the judges ask)

* The network is **simulated**; the executor changes a model, not real routers. Swap `engine/executor.py` and the simulator's reading source to drive real gear.
* **Packet Tracer is not integrated.** `tools/pt_check.py` only tests whether its REST API is reachable on your version. Plan A (simulator) is the full demo.
* On a *sudden* hard failure the engine and the baseline both lose ~2 s (it waits for a second probe on purpose). The advantage shows up on predicted, intermittent, backup-fails, latent-fault and server cases, and in proof, evidence and escalation.
* The dashboard polls once a second; auth is demo-grade (in-memory tokens, fixed users).
