"""Confidence Gate - the four rules that decide whether the system may act alone.

 1. high confidence + low risk      -> AUTO_HEAL
 2. moderate confidence             -> GATHER_EVIDENCE (more probes before acting)
 3. high risk                       -> OPERATOR_APPROVAL
 4. low confidence + high risk      -> MANUAL_REVIEW
"""
RISK_HIGH, CONF_HIGH, CONF_LOW, MAX_GATHER = 40, 0.8, 0.5, 4


def decide(confidence, risk, mode="auto", gathers=0):
    if mode != "auto":
        return {"decision": "OPERATOR_APPROVAL", "rule": 0,
                "reason": "Automation is paused: every action needs operator approval"}
    if confidence < CONF_LOW and risk >= RISK_HIGH:
        return {"decision": "MANUAL_REVIEW", "rule": 4,
                "reason": f"Low confidence ({confidence}) and high risk ({risk}): hand over to a human"}
    if risk >= RISK_HIGH:
        return {"decision": "OPERATOR_APPROVAL", "rule": 3,
                "reason": f"High risk ({risk} ≥ {RISK_HIGH}): operator must approve"}
    if confidence >= CONF_HIGH:
        return {"decision": "AUTO_HEAL", "rule": 1,
                "reason": f"High confidence ({confidence}) and low risk ({risk}): safe to auto-heal"}
    if gathers >= MAX_GATHER:
        return {"decision": "OPERATOR_APPROVAL", "rule": 2,
                "reason": f"Confidence still {confidence} after {gathers} evidence rounds: ask the operator"}
    return {"decision": "GATHER_EVIDENCE", "rule": 2,
            "reason": f"Moderate confidence ({confidence}): collecting more probes before acting"}
