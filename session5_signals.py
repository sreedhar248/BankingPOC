"""
Banking Compliance Agent — Session 5: velocity, counterparty risk and pattern signals

Runs nine scenarios through the shared graph (agent.py) and checks both the verdict and
which rules fired. The rules live in detection.py. Flagged transactions are explained by
Claude if ANTHROPIC_API_KEY is set (about four calls), otherwise by the deterministic fallback.
"""

from dotenv import load_dotenv

from agent import build_graph, default_mcp

load_dotenv()

SCENARIOS = [
    # txn_id, label, flagged?, rules expected to fire
    ("TXN-1001", "normal retail payment", False, set()),
    ("TXN-2001", "$61k business payment, normal for this payer", False, set()),
    ("TXN-3001", "$9.8k from a ~$900 payer to a new payee", True, {"baseline_spike", "new_payee_large"}),
    ("TXN-4001", "new payer, only 1 prior txn", False, set()),
    ("TXN-5001", "5th payment in 24h, normal amounts (signal only)", False, {"velocity_burst"}),
    ("TXN-6001", "third payment in 48h just under $10,000", True, {"structuring"}),
    ("TXN-7001", "$250 to a watchlisted payee", True, {"watchlisted_payee"}),
    ("TXN-8001", "burst of payments, then 2.5x average to a new payee", True, {"velocity_burst", "new_payee_large"}),
    ("TXN-9001", "first payment to a new payee, normal amount", False, set()),
]

if __name__ == "__main__":
    app = build_graph(default_mcp())

    failures = 0
    summary = []
    for txn_id, label, expected_flag, expected_rules in SCENARIOS:
        print(f"\n--- {txn_id}: {label} ---")
        result = app.invoke({"transaction_id": txn_id})
        fired = {r["rule"] for r in result["rules"]}
        ok = result["is_anomalous"] == expected_flag and fired == expected_rules
        failures += not ok
        verdict = "FLAGGED" if result["is_anomalous"] else "clean"
        rules = ", ".join(f"{r['rule']}({r['severity'][0].upper()})" for r in result["rules"]) or "-"
        summary.append((txn_id, verdict, rules, "PASS" if ok else "FAIL"))
        print(f"Result: {verdict}, rules: {rules} -> {'PASS' if ok else 'FAIL'}")

    print("\n=== Summary ===")
    for txn_id, verdict, rules, status in summary:
        print(f"{txn_id}  {verdict:<8} {rules:<40} {status}")
    raise SystemExit(1 if failures else 0)
