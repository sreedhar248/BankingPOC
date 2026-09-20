"""
Banking Compliance Agent — Session 2: MCP / MongoDB integration
Flow: ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op

Input is just a transaction_id. ingest_transaction loads the transaction and the
payer's prior history through MCP tools (never raw pymongo). detect_anomaly
compares the amount to that payer's own baseline instead of a fixed threshold.
"""

from statistics import mean, pstdev
from typing import TypedDict

from langgraph.graph import StateGraph, END

from mcp_tools import MCPClient, MongoTransactionsServer
from mongo_db import get_collection

MIN_HISTORY = 3        # need at least this many prior txns to trust a baseline
RATIO_THRESHOLD = 3.0  # amount >= 3x payer's historical average
ZSCORE_THRESHOLD = 3.0 # ...and >= 3 std devs above it


class TransactionState(TypedDict):
    transaction_id: str
    payer_id: str
    amount: float
    timestamp: str
    history: list
    is_anomalous: bool
    reasons: list
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
        return {
            "payer_id": txn["payer_id"],
            "amount": txn["amount"],
            "timestamp": txn["timestamp"],
            "history": history,
        }

    def detect_anomaly(state: TransactionState) -> dict:
        amounts = [h["amount"] for h in state["history"]]
        if len(amounts) < MIN_HISTORY:
            reasons = [f"insufficient history ({len(amounts)} prior txns) — no baseline check possible"]
            print(f"[detect] {reasons[0]}")
            return {"is_anomalous": False, "reasons": reasons}

        avg = mean(amounts)
        std = pstdev(amounts)
        ratio = state["amount"] / avg if avg else float("inf")
        z = (state["amount"] - avg) / std if std else float("inf")
        print(f"[detect] baseline avg=${avg:,.2f} std=${std:,.2f} -> ratio={ratio:.1f}x z={z:.1f}")

        if ratio >= RATIO_THRESHOLD and z >= ZSCORE_THRESHOLD:
            return {
                "is_anomalous": True,
                "reasons": [
                    f"amount ${state['amount']:,.2f} is {ratio:.1f}x payer average ${avg:,.2f} "
                    f"({z:.1f} std devs above, n={len(amounts)})"
                ],
            }
        return {"is_anomalous": False, "reasons": ["consistent with payer history"]}

    def route_after_detection(state: TransactionState) -> str:
        return "send_alert" if state["is_anomalous"] else "no_op"

    def send_alert(state: TransactionState) -> dict:
        msg = (
            f"ALERT: transaction {state['transaction_id']} (${state['amount']:,.2f}) "
            f"flagged as anomalous — {'; '.join(state['reasons'])}."
        )
        print(f"[send_alert] {msg}")
        return {"alert_message": msg}

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
    print("MCP tools:", [t["name"] for t in mcp.list_tools()])
    app = build_graph(mcp)

    scenarios = [
        ("TXN-1001", "normal retail payment, in line with history", False),
        ("TXN-2001", "$61k business payment — normal for this payer (old $50k threshold would false-positive)", False),
        ("TXN-3001", "$9.8k from a payer who averages ~$900 (old $50k threshold would miss it)", True),
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
