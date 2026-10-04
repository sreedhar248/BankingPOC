"""
Claude-based investigation and alert drafting for flagged transactions.

Detection (is this transaction anomalous?) stays deterministic — see
detect_anomaly in session3_langgraph_claude.py. This module only reasons
about a transaction *after* it has already been flagged: it explains why,
in plain language, and drafts the alert text. The flag itself is never
revisited here, which keeps the compliance decision auditable.

All Claude calls go through ai_gateway.AIGateway (rate limits, metering,
caching). If there's no ANTHROPIC_API_KEY, or the gateway throttles the call,
investigate() falls back to a deterministic summary built from the flag
reasons, so the alert still goes out.
"""

import json
import os
from typing import Optional

from ai_gateway import AIGateway, RateLimitExceeded, get_gateway

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """You are a banking compliance analyst assistant. You are given \
a transaction that a deterministic rules engine has already flagged as \
anomalous, plus the payer's recent transaction history and the specific \
numeric reasons it was flagged. Do not second-guess the flag itself — it is \
final; your job is only to explain it and draft the alert.

Write:
1. "investigation": 2-3 sentences a compliance analyst could read to quickly \
understand why this transaction stands out from the payer's normal pattern, \
in plain English (not just repeating the raw numbers).
2. "alert_message": one sentence suitable for a Slack/JIRA alert, addressed \
to the on-call analyst.

Respond with ONLY a JSON object of the form:
{"investigation": "...", "alert_message": "..."}"""


def _fallback(transaction: dict, reasons: list[str]) -> dict:
    joined = "; ".join(reasons)
    return {
        "investigation": joined,
        "alert_message": (
            f"ALERT: transaction {transaction['transaction_id']} "
            f"(${transaction['amount']:,.2f}) flagged as anomalous — {joined}."
        ),
    }


def investigate(
    transaction: dict, history: list[dict], reasons: list[str], gateway: Optional[AIGateway] = None
) -> dict:
    """Return {"investigation": str, "alert_message": str} for a flagged transaction."""
    gateway = gateway or get_gateway()
    user_content = json.dumps(
        {"transaction": transaction, "payer_history": history, "flag_reasons": reasons},
        default=str,
    )

    try:
        response = gateway.invoke(
            transaction_id=transaction["transaction_id"],
            reasons=reasons,
            model=MODEL,
            system=SYSTEM_PROMPT,
            user_content=user_content,
            max_tokens=400,
        )
    except RateLimitExceeded as exc:
        print(f"[claude_investigator] {exc}; using deterministic fallback")
        return _fallback(transaction, reasons)

    if response.text is None:
        print("[claude_investigator] no ANTHROPIC_API_KEY set, using deterministic fallback")
        return _fallback(transaction, reasons)

    # Claude sometimes wraps the JSON in a ```json fence despite the prompt; take the outermost {...}.
    start, end = response.text.find("{"), response.text.rfind("}")
    candidate = response.text[start : end + 1] if 0 <= start < end else response.text
    try:
        result = json.loads(candidate)
        if "investigation" in result and "alert_message" in result:
            return result
    except json.JSONDecodeError:
        pass

    print(f"[claude_investigator] could not parse Claude response ({response.text[:300]!r}), using deterministic fallback")
    return _fallback(transaction, reasons)
