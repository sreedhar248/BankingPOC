"""Unit tests for the deterministic rules in detection.py. Pure data in, verdict out: no DB, no network."""

import json
from datetime import datetime, timedelta, timezone

from detection import evaluate

NOW = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)
CLEAN_PAYEE = {"payee_id": "P-KNOWN", "watchlisted": False}


def hist(amount, hours_ago, payee="P-KNOWN"):
    return {"amount": amount, "payee_id": payee, "timestamp": (NOW - timedelta(hours=hours_ago)).isoformat()}


def txn(amount, payee="P-KNOWN"):
    return {"amount": amount, "payee_id": payee, "timestamp": NOW.isoformat()}


def names(detection):
    return {rule.name for rule in detection.rules}


# ~$100 payer with a little variation, last paid 10-40 days ago (nothing in any short window)
BASE = [hist(a, 24 * d) for a, d in zip((90, 100, 110, 100), (10, 20, 30, 40))]


def test_normal_payment_is_clean():
    d = evaluate(txn(105), BASE, CLEAN_PAYEE)
    assert not d.is_anomalous and not d.rules
    assert d.reasons == ["consistent with payer history"]


# ---- baseline_spike -----------------------------------------------------------


def test_baseline_spike_is_high_and_flags():
    d = evaluate(txn(400), BASE, CLEAN_PAYEE)
    assert d.is_anomalous and names(d) == {"baseline_spike"}
    assert d.rules[0].severity == "high"
    assert d.reasons[0].startswith("amount $400.00 is 4.0x payer average $100.00")
    assert d.signals["ratio"] == 4.0


def test_baseline_spike_needs_both_ratio_and_zscore():
    noisy = [hist(a, 24 * d) for a, d in zip((10, 500, 10, 500), (10, 20, 30, 40))]  # avg 255, very spread out
    d = evaluate(txn(800), noisy, CLEAN_PAYEE)  # 3.1x the average, but only ~2.2 std devs above it
    assert d.signals["ratio"] > 3 and d.signals["z_score"] < 3
    assert "baseline_spike" not in names(d)


def test_zero_variance_history_still_serializes():
    flat = [hist(100, 24 * d) for d in (10, 20, 30)]
    d = evaluate(txn(400), flat, CLEAN_PAYEE)
    assert "baseline_spike" in names(d) and d.signals["z_score"] is None
    json.dumps(d.signals)  # no inf/nan leaks into the response


def test_too_little_history_means_no_baseline_checks():
    d = evaluate(txn(10_000), BASE[:2], {"payee_id": "P-NEW", "watchlisted": False})
    assert not d.is_anomalous and "avg" not in d.signals
    assert d.reasons == ["insufficient history (2 prior txns) — no baseline check possible"]


# ---- velocity_burst (medium) --------------------------------------------------


def test_velocity_burst_fires_at_the_limit_but_alone_does_not_flag():
    d = evaluate(txn(100), BASE + [hist(100, h) for h in (1, 2, 3)], CLEAN_PAYEE)  # 3 recent + current = 4
    assert names(d) == {"velocity_burst"} and d.rules[0].severity == "medium"
    assert d.signals["velocity"]["txn_count"] == 4
    assert not d.is_anomalous, "one medium rule is a signal, not an alert"
    assert d.reasons == ["consistent with payer history"]


def test_velocity_below_the_limit_does_not_fire():
    d = evaluate(txn(100), BASE + [hist(100, h) for h in (1, 2)], CLEAN_PAYEE)
    assert "velocity_burst" not in names(d) and d.signals["velocity"]["txn_count"] == 3


def test_velocity_window_edge_is_inclusive():
    inside = evaluate(txn(100), BASE + [hist(100, h) for h in (1, 2, 24)], CLEAN_PAYEE)
    outside = evaluate(txn(100), BASE + [hist(100, h) for h in (1, 2, 24.01)], CLEAN_PAYEE)
    assert "velocity_burst" in names(inside) and "velocity_burst" not in names(outside)


# ---- new_payee_large (medium) -------------------------------------------------


def test_new_payee_at_a_normal_amount_is_fine():
    d = evaluate(txn(120, payee="P-NEW"), BASE, {"payee_id": "P-NEW", "watchlisted": False})
    assert not d.rules and d.signals["counterparty"]["first_time_payee"] is True


def test_known_payee_at_2_5x_is_fine():
    d = evaluate(txn(250), BASE, CLEAN_PAYEE)
    assert not d.rules and d.signals["counterparty"]["first_time_payee"] is False


def test_new_payee_at_2_5x_is_a_medium_signal_only():
    d = evaluate(txn(250, payee="P-NEW"), BASE, {"payee_id": "P-NEW", "watchlisted": False})
    assert names(d) == {"new_payee_large"} and not d.is_anomalous
    assert d.rules[0].detail == "first payment to P-NEW, and amount $250.00 is 2.5x payer average $100.00"


def test_two_medium_rules_together_flag():
    history = BASE + [hist(100, h) for h in (1, 2, 3)]
    d = evaluate(txn(250, payee="P-NEW"), history, {"payee_id": "P-NEW", "watchlisted": False})
    assert names(d) == {"velocity_burst", "new_payee_large"} and d.is_anomalous
    assert len(d.reasons) == 2


# ---- watchlisted_payee (high) -------------------------------------------------


def test_watchlisted_payee_flags_even_a_tiny_amount():
    profile = {"payee_id": "P-BAD", "watchlisted": True, "reason": "linked to shell company"}
    d = evaluate(txn(10, payee="P-BAD"), BASE, profile)
    assert d.is_anomalous and names(d) == {"watchlisted_payee"}
    assert d.reasons == ["payee P-BAD is on the watchlist (linked to shell company)"]


def test_watchlisted_payee_flags_without_any_history():
    d = evaluate(txn(10, payee="P-BAD"), [], {"payee_id": "P-BAD", "watchlisted": True})
    assert d.is_anomalous and names(d) == {"watchlisted_payee"}


# ---- structuring (high) -------------------------------------------------------

# a payer who normally pays ~$8k, so the amounts themselves are not a baseline spike
BIG = [hist(a, 24 * d) for a, d in zip((7800, 8200, 8600, 8100), (10, 20, 30, 40))]


def test_three_payments_just_under_the_threshold_in_48h_is_structuring():
    d = evaluate(txn(9900), BIG + [hist(9500, 30), hist(9700, 12)], CLEAN_PAYEE)
    assert d.is_anomalous and names(d) == {"structuring"} and d.rules[0].severity == "high"
    assert d.signals["pattern"]["near_threshold_count"] == 3
    assert "3 payments" in d.reasons[0] and "$10,000" in d.reasons[0]


def test_two_near_threshold_payments_is_not_enough():
    d = evaluate(txn(9900), BIG + [hist(9700, 12)], CLEAN_PAYEE)
    assert "structuring" not in names(d) and d.signals["pattern"]["near_threshold_count"] == 2


def test_near_threshold_payment_outside_the_window_does_not_count():
    d = evaluate(txn(9900), BIG + [hist(9500, 49), hist(9700, 12)], CLEAN_PAYEE)
    assert "structuring" not in names(d)


def test_current_payment_must_itself_be_near_the_threshold():
    history = BIG + [hist(9500, 30), hist(9700, 12)]
    assert "structuring" not in names(evaluate(txn(8500), history, CLEAN_PAYEE))
    assert "structuring" not in names(evaluate(txn(10_000), history, CLEAN_PAYEE)), "the threshold itself is not 'under'"


def test_band_floor_is_inclusive():
    history = BIG + [hist(9000, 30), hist(9000, 12)]
    assert "structuring" in names(evaluate(txn(9000), history, CLEAN_PAYEE))
    assert "structuring" not in names(evaluate(txn(8999.99), history, CLEAN_PAYEE))
