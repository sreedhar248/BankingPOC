"""
The compliance agent graph, shared by the session runners and the HTTP API.
Flow: ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op

Detection is deterministic (the rules in detection.py) and is NOT handed to
the LLM (see README: "Why detection logic stays deterministic"). Once a
transaction is flagged, send_alert asks Claude, via the AI gateway, to explain
why in plain language and draft the alert.
"""

from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END

from ai_gateway import AIGateway
from claude_investigator import investigate
from detection import evaluate
from mcp_tools import MCPClient, MCPToolError, MongoTransactionsServer
from mongo_db import get_collection


class TransactionNotFound(LookupError):
    pass


class TransactionState(TypedDict):
    transaction_id: str
    transaction: dict
    history: list
    payee_profile: dict
    is_anomalous: bool
    reasons: list
    signals: dict
    rules: list
    investigation: str
    alert_message: str
    gateway_status: str


def default_mcp() -> MCPClient:
    """MCP client over the configured data source (mongomock, or MONGODB_URI if set)."""
    return MCPClient(MongoTransactionsServer(get_collection()))


def build_graph(mcp: MCPClient, gateway: Optional[AIGateway] = None, fallback_on_throttle: bool = True):
    """
    gateway: AI gateway for the Claude call (default: the process-wide one).
    fallback_on_throttle: if the gateway throttles the call, send the deterministic alert (True)
    or let RateLimitExceeded propagate to the caller (False).
    """

    def ingest_transaction(state: TransactionState) -> dict:
        try:
            txn = mcp.call_tool("get_transaction", transaction_id=state["transaction_id"])
        except MCPToolError as exc:
            if exc.code == "not_found":
                raise TransactionNotFound(state["transaction_id"]) from exc
            raise
        history = mcp.call_tool(
            "get_payer_history", payer_id=txn["payer_id"], before=txn["timestamp"], limit=50
        )
        payee_profile = mcp.call_tool("get_payee_profile", payee_id=txn["payee_id"])
        print(
            f"[ingest] {txn['transaction_id']} payer={txn['payer_id']} payee={txn['payee_id']} "
            f"amount=${txn['amount']:,.2f} prior_txns={len(history)}"
        )
        return {"transaction": txn, "history": history, "payee_profile": payee_profile}

    def detect_anomaly(state: TransactionState) -> dict:
        detection = evaluate(state["transaction"], state["history"], state["payee_profile"])
        sig = detection.signals
        if "avg" in sig:
            print(
                f"[detect] baseline avg=${sig['avg']:,.2f} std=${sig['std']:,.2f} -> "
                f"ratio={sig['ratio']}x z={sig['z_score']}"
            )
        else:
            print(f"[detect] {detection.reasons[0]}")
        for rule in detection.rules:
            print(f"[detect] rule {rule.name} ({rule.severity}): {rule.detail}")
        return {
            "is_anomalous": detection.is_anomalous,
            "reasons": detection.reasons,
            "signals": detection.signals,
            "rules": [rule.as_dict() for rule in detection.rules],
        }

    def route_after_detection(state: TransactionState) -> str:
        return "send_alert" if state["is_anomalous"] else "no_op"

    def send_alert(state: TransactionState) -> dict:
        result = investigate(
            state["transaction"],
            state["history"],
            state["reasons"],
            gateway=gateway,
            fallback_on_throttle=fallback_on_throttle,
        )
        print(f"[send_alert] investigation: {result['investigation']}")
        print(f"[send_alert] alert: {result['alert_message']}")
        return {
            "investigation": result["investigation"],
            "alert_message": result["alert_message"],
            "gateway_status": result["gateway_status"],
        }

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
