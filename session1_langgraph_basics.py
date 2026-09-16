"""
Banking Compliance Agent — Session 1: LangGraph Fundamentals
Flow: ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op

This is the S1 skeleton: pipeline shape only. Anomaly detection is a
toy fixed-threshold placeholder — real detection (velocity, historical
baseline, counterparty risk, AI judgment) comes in later sessions once
MongoDB and the Claude API are wired in.
"""

from typing import TypedDict
from langgraph.graph import StateGraph, END


# ---- State -----------------------------------------------------------

class TransactionState(TypedDict):
    transaction_id: str
    amount: float
    is_anomalous: bool
    alert_message: str


# ---- Nodes -------------------------------------------------------------

def ingest_transaction(state: TransactionState) -> TransactionState:
    """Entry point. In a real flow this is where the NPP event lands."""
    print(f"[ingest] transaction {state['transaction_id']} amount=${state['amount']:,.2f}")
    return state


def detect_anomaly(state: TransactionState) -> TransactionState:
    """
    TOY LOGIC — placeholder only.
    Real detection (Session 2+) needs MongoDB-backed context:
      - velocity (txn count in a time window per payer)
      - historical baseline (deviation from this account's normal pattern)
      - counterparty risk (new/flagged payee)
      - pattern matching (structuring, fan-out)
      - AI judgment (Claude API reasoning over the above, not the raw number)
    """
    state["is_anomalous"] = state["amount"] > 50_000
    return state


def route_after_detection(state: TransactionState) -> str:
    """Conditional edge: branch on the anomaly flag."""
    return "send_alert" if state["is_anomalous"] else "no_op"


def send_alert(state: TransactionState) -> TransactionState:
    """Mock alert. Real version (later sessions) posts via MCP to Slack/JIRA."""
    state["alert_message"] = (
        f"ALERT: transaction {state['transaction_id']} "
        f"(${state['amount']:,.2f}) flagged as anomalous."
    )
    print(f"[send_alert] {state['alert_message']}")
    return state


def no_op(state: TransactionState) -> TransactionState:
    print(f"[no_op] transaction {state['transaction_id']} is clean, no action taken.")
    return state


# ---- Graph assembly ------------------------------------------------------

def build_graph():
    graph = StateGraph(TransactionState)

    graph.add_node("ingest_transaction", ingest_transaction)
    graph.add_node("detect_anomaly", detect_anomaly)
    graph.add_node("send_alert", send_alert)
    graph.add_node("no_op", no_op)

    graph.set_entry_point("ingest_transaction")
    graph.add_edge("ingest_transaction", "detect_anomaly")
    graph.add_conditional_edges(
        "detect_anomaly",
        route_after_detection,
        {"send_alert": "send_alert", "no_op": "no_op"},
    )
    graph.add_edge("send_alert", END)
    graph.add_edge("no_op", END)

    return graph.compile()


# ---- Manual test run -----------------------------------------------------

if __name__ == "__main__":
    app = build_graph()

    test_transactions = [
        {"transaction_id": "TXN-001", "amount": 1_250.00, "is_anomalous": False, "alert_message": ""},
        {"transaction_id": "TXN-002", "amount": 87_500.00, "is_anomalous": False, "alert_message": ""},
    ]

    for txn in test_transactions:
        print("\n--- Running transaction ---")
        result = app.invoke(txn)
        print(f"Result: is_anomalous={result['is_anomalous']}")
