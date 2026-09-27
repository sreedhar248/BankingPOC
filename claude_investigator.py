"""
Claude-based investigation and alert drafting for flagged transactions.

Detection (is this transaction anomalous?) stays deterministic — see
detect_anomaly in session3_langgraph_claude.py. This module only reasons
about a transaction *after* it has already been flagged: it explains why,
in plain language, and drafts the alert text. The flag itself is never
revisited here, which keeps the compliance decision auditable.

If ANTHROPIC_API_KEY isn't set, investigate() falls back to a deterministic
summary built from the flag reasons, so the graph still runs end-to-end
without a key or API cost.
"""

import json
import os

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


def investigate(transaction: dict, history: list[dict], reasons: list[str]) -> dict:
    """Return {"investigation": str, "alert_message": str} for a flagged transaction."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[claude_investigator] no ANTHROPIC_API_KEY set, using deterministic fallback")
        return _fallback(transaction, reasons)

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    user_content = json.dumps(
        {"transaction": transaction, "payer_history": history, "flag_reasons": reasons},
        default=str,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text
    try:
        result = json.loads(text)
        if "investigation" in result and "alert_message" in result:
            return result
    except json.JSONDecodeError:
        pass

    print("[claude_investigator] could not parse Claude response, using deterministic fallback")
    return _fallback(transaction, reasons)
