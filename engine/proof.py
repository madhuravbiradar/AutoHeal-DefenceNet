"""Proof engine: after a fix, probe loss / latency / stability and return Verified / Partial / Failed."""
from statistics import mean, pstdev

PROBES = 4


def verdict(probes):
    n = len(probes)
    loss = mean(p["loss_pct"] for p in probes)
    lat = mean(p["latency_ms"] for p in probes)
    jitter = pstdev([p["latency_ms"] for p in probes])
    oks = sum(1 for p in probes if p["ok"])
    if oks == n and loss < 1.5 and jitter < 25:
        v = "Verified"
    elif oks >= 1 and loss < 10:
        v = "Partial"
    else:
        v = "Failed"
    return {"verdict": v, "probes": probes, "avg_latency_ms": round(lat, 1), "avg_loss_pct": round(loss, 2),
            "jitter_ms": round(jitter, 1), "probes_ok": f"{oks}/{n}",
            "criteria": "Verified = all probes healthy, loss < 1.5%, jitter < 25 ms; Partial = service up but impaired; Failed = otherwise"}
