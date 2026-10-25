"""
The compliance agent graph, shared by the session runners and the HTTP API.
Flow: ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op

Detection is deterministic (payer-baseline ratio + z-score) and is NOT handed to
the LLM (see README: "Why detection logic stays deterministic"). Once a
transaction is flagged, send_alert asks Claude, via the AI gateway, to explain
why in plain language and draft the alert.
"""

import math
from statistics import mean, pstdev
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END

from ai_gateway import AIGateway
from claude_investigator import investigate
from mcp_tools import MCPClient, MCPToolError, MongoTransactionsServer
from mongo_db import get_collection

MIN_HISTORY = 3        # need at least this many prior txns to trust a baseline
RATIO_THRESHOLD = 3.0  # amount >= 3x payer's historical average
ZSCORE_THRESHOLD = 3.0 # ...and >= 3 std devs above it


class TransactionNotFound(LookupError):
    pass


class TransactionState(TypedDict):
    transaction_id: str
    transaction: dict
    history: list
    is_anomalous: bool
    reasons: list
    signals: dict
    investigation: str
    alert_message: str
    gateway_status: str


def _finite(value: float):
    """Round for display; undefined values (divide by zero) become None so they serialize cleanly."""
    return round(value, 2) if math.isfinite(value) else None


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
        print(
            f"[ingest] {txn['transaction_id']} payer={txn['payer_id']} "
            f"amount=${txn['amount']:,.2f} prior_txns={len(history)}"
        )
        return {"transaction": txn, "history": history}

    def detect_anomaly(state: TransactionState) -> dict:
        amounts = [h["amount"] for h in state["history"]]
        amount = state["transaction"]["amount"]
        signals = {
            "amount": amount,
            "history_count": len(amounts),
            "min_history": MIN_HISTORY,
            "ratio_threshold": RATIO_THRESHOLD,
            "zscore_threshold": ZSCORE_THRESHOLD,
        }

        if len(amounts) < MIN_HISTORY:
            reasons = [f"insufficient history ({len(amounts)} prior txns) — no baseline check possible"]
            print(f"[detect] {reasons[0]}")
            return {"is_anomalous": False, "reasons": reasons, "signals": signals}

        avg = mean(amounts)
        std = pstdev(amounts)
        ratio = amount / avg if avg else float("inf")
        z = (amount - avg) / std if std else float("inf")
        print(f"[detect] baseline avg=${avg:,.2f} std=${std:,.2f} -> ratio={ratio:.1f}x z={z:.1f}")
        signals.update(avg=_finite(avg), std=_finite(std), ratio=_finite(ratio), z_score=_finite(z))

        if ratio >= RATIO_THRESHOLD and z >= ZSCORE_THRESHOLD:
            return {
                "is_anomalous": True,
                "reasons": [
                    f"amount ${amount:,.2f} is {ratio:.1f}x payer average ${avg:,.2f} "
                    f"({z:.1f} std devs above, n={len(amounts)})"
                ],
                "signals": signals,
            }
        return {"is_anomalous": False, "reasons": ["consistent with payer history"], "signals": signals}

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
