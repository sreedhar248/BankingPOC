"""
Banking Compliance Agent — Session 3: Claude-powered investigation
Flow: ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op

Same deterministic detection as Session 2 (payer-baseline ratio + z-score) —
that decision is NOT handed to the LLM (see README: "Why detection logic
stays deterministic"). What's new: once a transaction is flagged, send_alert
calls Claude to investigate *why* in plain language and draft the alert
message, instead of just formatting the numeric reasons.
"""

from statistics import mean, pstdev
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

from claude_investigator import investigate
from mcp_tools import MCPClient, MongoTransactionsServer
from mongo_db import get_collection

load_dotenv()

MIN_HISTORY = 3        # need at least this many prior txns to trust a baseline
RATIO_THRESHOLD = 3.0  # amount >= 3x payer's historical average
ZSCORE_THRESHOLD = 3.0 # ...and >= 3 std devs above it


class TransactionState(TypedDict):
    transaction_id: str
    transaction: dict
    history: list
    is_anomalous: bool
    reasons: list
    investigation: str
    alert_message: str


def build_graph(mcp: MCPClient):
    def ingest_transaction(state: TransactionState) -> dict:
        txn = mcp.call_tool("get_transaction", transaction_id=state["transaction_id"])
        history = mcp.call_tool(
            "get_payer_history", payer_id=txn["payer_id"], before=txn["timestamp"], limit=50
        )
        print(
            f"[ingest] {txn['transaction_id']} payer={txn['payer_id']} "
            f"amount=${txn['amount']:,.2f} prior_txns={len(history)}"
        )
        return {"transaction": txn, "history": history}

    def detect_anomaly(state: TransactionState) -> dict:
        amounts = [h["amount"] for h in state["history"]]
        amount = state["transaction"]["amount"]

        if len(amounts) < MIN_HISTORY:
            reasons = [f"insufficient history ({len(amounts)} prior txns) — no baseline check possible"]
            print(f"[detect] {reasons[0]}")
            return {"is_anomalous": False, "reasons": reasons}

        avg = mean(amounts)
        std = pstdev(amounts)
        ratio = amount / avg if avg else float("inf")
        z = (amount - avg) / std if std else float("inf")
        print(f"[detect] baseline avg=${avg:,.2f} std=${std:,.2f} -> ratio={ratio:.1f}x z={z:.1f}")

        if ratio >= RATIO_THRESHOLD and z >= ZSCORE_THRESHOLD:
            return {
                "is_anomalous": True,
                "reasons": [
                    f"amount ${amount:,.2f} is {ratio:.1f}x payer average ${avg:,.2f} "
                    f"({z:.1f} std devs above, n={len(amounts)})"
                ],
            }
        return {"is_anomalous": False, "reasons": ["consistent with payer history"]}

    def route_after_detection(state: TransactionState) -> str:
        return "send_alert" if state["is_anomalous"] else "no_op"

    def send_alert(state: TransactionState) -> dict:
        result = investigate(state["transaction"], state["history"], state["reasons"])
        print(f"[send_alert] investigation: {result['investigation']}")
        print(f"[send_alert] alert: {result['alert_message']}")
        return {"investigation": result["investigation"], "alert_message": result["alert_message"]}

    def no_op(state: TransactionState) -> dict:
        print(f"[no_op] {state['transaction_id']}: {'; '.join(state['reasons'])}. No action taken.")
        return {}

    graph = StateGraph(TransactionState)
    graph.add_node("ingest_transaction", ingest_transaction)
    graph.add_node("detect_anomaly", detect_anomaly)
    graph.add_node("send_alert", send_alert)
    graph.add_node("no_op", no_op)

    graph.set_entry_point("ingest_transaction")
    graph.add_edge("ingest_transaction", "detect_anomaly")
    graph.add_conditional_edges(
        "detect_anomaly", route_after_detection, {"send_alert": "send_alert", "no_op": "no_op"}
    )
    graph.add_edge("send_alert", END)
    graph.add_edge("no_op", END)
    return graph.compile()


if __name__ == "__main__":
    mcp = MCPClient(MongoTransactionsServer(get_collection()))
    app = build_graph(mcp)

    scenarios = [
        ("TXN-1001", "normal retail payment, in line with history", False),
        ("TXN-2001", "$61k business payment — normal for this payer", False),
        ("TXN-3001", "$9.8k from a payer who averages ~$900", True),
        ("TXN-4001", "new payer, only 1 prior txn — no baseline", False),
    ]

    failures = 0
    for txn_id, label, expected in scenarios:
        print(f"\n--- {txn_id}: {label} ---")
        result = app.invoke({"transaction_id": txn_id})
        ok = result["is_anomalous"] == expected
        failures += not ok
        print(f"Result: is_anomalous={result['is_anomalous']} (expected {expected}) {'PASS' if ok else 'FAIL'}")

    raise SystemExit(1 if failures else 0)
