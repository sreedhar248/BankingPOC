"""
Banking Compliance Agent — Session 3: Claude-powered investigation
Flow: ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op

Runner for the four demo scenarios. The graph itself lives in agent.py (shared
with the HTTP API in api.py); this script adds .env loading and Langfuse tracing.
"""

from dotenv import load_dotenv

import tracing
from agent import build_graph, default_mcp

load_dotenv()


if __name__ == "__main__":
    app = build_graph(default_mcp())

    scenarios = [
        ("TXN-1001", "normal retail payment, in line with history", False),
        ("TXN-2001", "$61k business payment — normal for this payer", False),
        ("TXN-3001", "$9.8k from a payer who averages ~$900", True),
        ("TXN-4001", "new payer, only 1 prior txn — no baseline", False),
    ]

    failures = 0
    for txn_id, label, expected in scenarios:
        print(f"\n--- {txn_id}: {label} ---")
        with tracing.trace(name=txn_id, tags=[label], input={"transaction_id": txn_id}) as span:
            result = app.invoke({"transaction_id": txn_id}, config={"callbacks": tracing.callbacks()})
            span.update(output={k: result[k] for k in ("is_anomalous", "reasons", "signals")})
        ok = result["is_anomalous"] == expected
        failures += not ok
        print(f"Result: is_anomalous={result['is_anomalous']} (expected {expected}) {'PASS' if ok else 'FAIL'}")

    tracing.flush()
    raise SystemExit(1 if failures else 0)
