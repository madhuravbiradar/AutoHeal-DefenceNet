"""Plan-B feasibility check (30 min): can Python reach Packet Tracer's REST API on this laptop?

 1. Packet Tracer > Options > Preferences > Administrative: enable external access.
 2. Add a Network Controller to a tiny A/B/C router topology.
 3. python tools/pt_check.py [port]

Not wired into the engine: if this works, write a PacketTracerSource that emits the same
link/node reading dicts as simulator/network.py and an executor that sends the reroute command.
"""
import json, sys, urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "58000"     # use the port shown in your PT preferences
for path in ("/", "/api/v1/host", "/api/v1/network-device"):
    url = f"http://localhost:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body = r.read(400).decode(errors="replace")
            print(f"OK   {url} -> {r.status}\n     {body[:200]!r}")
    except Exception as e:
        print(f"FAIL {url} -> {e}")
print("\nIf nothing answers: stay on Plan A (the simulator). Use Packet Tracer screenshots in the slides.")
