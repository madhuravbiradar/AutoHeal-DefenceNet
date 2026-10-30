"""Predictive monitoring: rolling averages + trend -> explainable 0-100 risk score (no ML)."""
from statistics import mean

from simulator.network import LAT_LIMIT, LOSS_LIMIT

WARN, CRIT = 45, 75


def slope(ys):
    n = len(ys)
    if n < 3:
        return 0.0
    mx, my = (n - 1) / 2, mean(ys)
    den = sum((x - mx) ** 2 for x in range(n))
    return sum((x - mx) * (y - my) for x, y in zip(range(n), ys)) / den


class Predictor:
    def assess(self, readings, base_latency):
        rs = list(readings)
        out = {"risk": 0.0, "avg_latency": base_latency, "avg_loss": 0.0, "trend": 0.0, "level": "ok"}
        if not rs:
            return out
        if rs[-1]["loss_pct"] >= 50:                       # hard down
            out.update(risk=100.0, avg_loss=100.0, level="critical")
            return out
        ok = [r for r in rs if r["loss_pct"] < 50][-5:]
        last3 = ok[-3:]
        avg_lat = mean(r["latency_ms"] for r in last3)
        avg_loss = mean(r["loss_pct"] for r in last3)
        trend = slope([r["latency_ms"] for r in ok]) if len(ok) >= 4 else 0.0
        floor = base_latency + 10
        lat_s = max(0.0, min(100.0, (avg_lat - floor) / (LAT_LIMIT - floor) * 100))
        loss_s = max(0.0, min(100.0, avg_loss / LOSS_LIMIT * 100))
        bonus = min(25.0, max(0.0, trend) * 2.5)
        risk = min(100.0, 0.8 * max(lat_s, loss_s) + (bonus if max(lat_s, loss_s) > 15 else 0))
        if not rs[-1]["healthy"]:
            risk = max(risk, 80.0)
        level = "critical" if risk >= CRIT else "warning" if risk >= WARN else "ok"
        out.update(risk=round(risk, 1), avg_latency=round(avg_lat, 1), avg_loss=round(avg_loss, 2),
                   trend=round(trend, 2), level=level)
        return out
