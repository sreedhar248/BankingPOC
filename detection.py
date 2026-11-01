"""
Deterministic anomaly rules. Pure functions, no I/O, no LLM.

Each rule is a plain, explainable condition with a severity. A transaction is
flagged when ANY high-severity rule fires, or when TWO OR MORE medium-severity
rules fire together. There are no weights and no learned model, so every alert can be
explained by naming the rules that fired and the numbers behind them.

  baseline_spike     high    amount far above this payer's own average (ratio and z-score)
  watchlisted_payee  high    payee is on the watchlist
  structuring        high    repeated payments just under the reporting threshold
  velocity_burst     medium  many payments from this payer in a short window
  new_payee_large    medium  first payment to this payee, and well above the payer's average

The thresholds below are illustrative, not calibrated against real data.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, pstdev

MIN_HISTORY = 3          # prior txns needed before any baseline-based rule applies
RATIO_THRESHOLD = 3.0    # baseline_spike: amount >= 3x the payer's average...
ZSCORE_THRESHOLD = 3.0   # ...and >= 3 standard deviations above it

VELOCITY_WINDOW_HOURS = 24
VELOCITY_MAX_TXNS = 4    # velocity_burst: this many payments in the window, counting the current one

NEW_PAYEE_RATIO = 2.0    # new_payee_large: first-time payee and amount >= 2x the payer's average

STRUCTURING_THRESHOLD = 10_000   # a reporting threshold that repeated payments might sit just under
STRUCTURING_BAND_FLOOR = 0.9     # "just under" means 90% of the threshold up to the threshold
STRUCTURING_WINDOW_HOURS = 48
STRUCTURING_MIN_TXNS = 3         # near-threshold payments in the window, counting the current one


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str  # "high" | "medium"
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.name, "severity": self.severity, "detail": self.detail}


@dataclass
class Detection:
    is_anomalous: bool
    reasons: list
    signals: dict
    rules: list  # list of Rule


def _finite(value: float):
    """Round for display; undefined values (divide by zero) become None so they serialize cleanly."""
    return round(value, 2) if math.isfinite(value) else None


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def evaluate(transaction: dict, history: list, payee_profile: dict) -> Detection:
    """
    transaction: the current transaction (amount, payee_id, timestamp as ISO-8601).
    history: this payer's prior transactions, strictly before the current one.
    payee_profile: {"payee_id", "watchlisted", "reason"?} for the current payee.
    """
    amount = transaction["amount"]
    now = _ts(transaction["timestamp"])
    amounts = [h["amount"] for h in history]
    has_baseline = len(amounts) >= MIN_HISTORY
    rules: list = []

    signals = {
        "amount": amount,
        "history_count": len(amounts),
        "min_history": MIN_HISTORY,
        "ratio_threshold": RATIO_THRESHOLD,
        "zscore_threshold": ZSCORE_THRESHOLD,
    }

    # -- baseline: how does this amount compare with the payer's own history? --
    avg = None
    if has_baseline:
        avg = mean(amounts)
        std = pstdev(amounts)
        ratio = amount / avg if avg else float("inf")
        z = (amount - avg) / std if std else float("inf")
        signals.update(avg=_finite(avg), std=_finite(std), ratio=_finite(ratio), z_score=_finite(z))
        if ratio >= RATIO_THRESHOLD and z >= ZSCORE_THRESHOLD:
            rules.append(Rule(
                "baseline_spike", "high",
                f"amount ${amount:,.2f} is {ratio:.1f}x payer average ${avg:,.2f} "
                f"({z:.1f} std devs above, n={len(amounts)})",
            ))

    # -- velocity: how many payments from this payer in the recent window? --
    window_start = now - timedelta(hours=VELOCITY_WINDOW_HOURS)
    recent = [h for h in history if _ts(h["timestamp"]) >= window_start]
    velocity_count = 1 + len(recent)
    signals["velocity"] = {
        "window_hours": VELOCITY_WINDOW_HOURS,
        "txn_count": velocity_count,
        "max_txns": VELOCITY_MAX_TXNS,
        "total_amount": round(amount + sum(h["amount"] for h in recent), 2),
    }
    if velocity_count >= VELOCITY_MAX_TXNS:
        rules.append(Rule(
            "velocity_burst", "medium",
            f"{velocity_count} payments from this payer in the last {VELOCITY_WINDOW_HOURS}h "
            f"(limit {VELOCITY_MAX_TXNS - 1})",
        ))

    # -- counterparty: who is being paid? --
    payee_id = transaction["payee_id"]
    first_time = payee_id not in {h["payee_id"] for h in history}
    watchlisted = bool(payee_profile.get("watchlisted"))
    signals["counterparty"] = {
        "payee_id": payee_id,
        "first_time_payee": first_time,
        "watchlisted": watchlisted,
        "watchlist_reason": payee_profile.get("reason"),
    }
    if watchlisted:
        why = payee_profile.get("reason")
        rules.append(Rule(
            "watchlisted_payee", "high",
            f"payee {payee_id} is on the watchlist" + (f" ({why})" if why else ""),
        ))
    if has_baseline and first_time and avg and amount >= NEW_PAYEE_RATIO * avg:
        rules.append(Rule(
            "new_payee_large", "medium",
            f"first payment to {payee_id}, and amount ${amount:,.2f} is {amount / avg:.1f}x "
            f"payer average ${avg:,.2f}",
        ))

    # -- pattern: repeated payments just under a reporting threshold (structuring) --
    floor = STRUCTURING_THRESHOLD * STRUCTURING_BAND_FLOOR

    def near_threshold(value: float) -> bool:
        return floor <= value < STRUCTURING_THRESHOLD

    pattern_start = now - timedelta(hours=STRUCTURING_WINDOW_HOURS)
    near_count = int(near_threshold(amount)) + sum(
        near_threshold(h["amount"]) for h in history if _ts(h["timestamp"]) >= pattern_start
    )
    signals["pattern"] = {
        "threshold": STRUCTURING_THRESHOLD,
        "band_floor": floor,
        "window_hours": STRUCTURING_WINDOW_HOURS,
        "near_threshold_count": near_count,
        "min_txns": STRUCTURING_MIN_TXNS,
    }
    if near_threshold(amount) and near_count >= STRUCTURING_MIN_TXNS:
        rules.append(Rule(
            "structuring", "high",
            f"{near_count} payments of ${floor:,.0f}-${STRUCTURING_THRESHOLD - 0.01:,.2f} within "
            f"{STRUCTURING_WINDOW_HOURS}h, just under the ${STRUCTURING_THRESHOLD:,} threshold",
        ))

    # -- decision --
    mediums = [r for r in rules if r.severity == "medium"]
    is_anomalous = any(r.severity == "high" for r in rules) or len(mediums) >= 2

    if is_anomalous:
        reasons = [r.detail for r in rules]
    elif not has_baseline:
        reasons = [f"insufficient history ({len(amounts)} prior txns) — no baseline check possible"]
    else:
        reasons = ["consistent with payer history"]

    return Detection(is_anomalous, reasons, signals, rules)
