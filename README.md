# Autonomous Self-Healing Network (SelfHeal 2.0)

Detects failures, finds **what** failed and **where**, diagnoses the cause, picks the best recovery plan and
applies it automatically so packet flow resumes. Built around a 7-router ring, 14 access switches, 112 PCs, 12 servers.

## Run
    pip install -r requirements.txt
    python run_demo.py            # full narrated scenario + comparison with/without healing
    python run_demo.py --quiet    # report only
    python -m unittest discover -s tests -v

    python demo_resume.py --fault all   # follow ONE packet stream: break -> self-heal -> resume (link/router/server/jam)

Outputs: `output/incident_report.json`, `output/timeline.csv`.

Typical result: a failure is detected within 1 s and traffic is flowing again on a new path within ~2 s,
then verified for 5 s of 100% delivery.

## Pipeline (selfheal/)
| Stage | File | What it does |
|---|---|---|
| Monitor / Detect | `monitor.py` | Probes every node and link each tick (heartbeat, loss, latency) + per-flow delivery. Debounced state machines: 2 bad samples to declare, 3 good to clear (no flapping). |
| Diagnose | `diagnosis.py` | Root-cause correlation (dead router => its links are *symptoms*), classification (cable cut, router/switch/server failure, jamming, congestion, latency degradation), impact (affected flows), isolation, ring partition, single points of failure, **tomography** to find silent failures the probes cannot see. |
| Plan | `planner.py` | Builds 4 complete strategies (reroute / +standby links / +server failover / +priority preemption), predicts each outcome on a capacity model, scores, and picks the best. Refuses to act if no plan beats the current state. |
| Execute | `executor.py`, `cisco_adapter.py` | Transactional, make-before-break, automatic rollback. Emits Cisco IOS CLI (dry-run by default; `live=True` + netmiko for real devices). |
| Verify | `healer.py` | Confirms flows recover (>=95% delivery), tracks incidents (MTTD/MTTR), fail-back after 3 s hold-down, releases standby links, escalates unrecoverable cases to "manual action required". |

## Design rules
* The healer sees only probe results (never ground truth), as on real hardware.
* Only routers carry transit traffic; switches/servers/PCs are access nodes.
* Priority 3 = command traffic, 2 = normal, 1 = bulk. Under overload, bulk is shed first.
* Server pairs (SRVn, SRVn+6) are replicas; flows fail over to the replica when the primary dies.

## Using it on a real network
Replace `SimNetwork` probes with ping/SNMP/telemetry and let `CiscoAdapter(live=True, creds=...)` push the CLI.
The detection, diagnosis and planning code is unchanged.

## Honest limits
Latency/bandwidth numbers and fault behaviour are simulated. Jamming vs. cable-cut is inferred from
loss/latency/utilisation signatures (heuristic, with confidence score), not measured physically. Hosts behind a dead
single-homed switch cannot be recovered remotely; the system reports them instead of hiding them.
