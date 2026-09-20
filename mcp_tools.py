"""
MCP-style tool layer over the transactions collection.

The graph only talks to `MCPClient`, which speaks the MCP tool contract:
  list_tools() -> [{name, description, inputSchema}]
  call_tool(name, arguments) -> {"content": [{"type": "text", "text": <json>}], "isError": bool}

To move to a real MCP MongoDB server, replace `MongoTransactionsServer` with a
transport (e.g. stdio/HTTP session) exposing the same two methods. Node code
does not change as long as the tool names and schemas match.
"""

import json
from datetime import datetime
from typing import Any, Protocol


class MCPServer(Protocol):
    def list_tools(self) -> list[dict]: ...
    def call_tool(self, name: str, arguments: dict) -> dict: ...


def _clean(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


def _text_result(payload: Any, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": is_error}


class MongoTransactionsServer:
    """In-process stand-in for an MCP MongoDB server."""

    def __init__(self, collection):
        self._col = collection

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "get_transaction",
                "description": "Fetch a single transaction by its transaction_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"transaction_id": {"type": "string"}},
                    "required": ["transaction_id"],
                },
            },
            {
                "name": "get_payer_history",
                "description": (
                    "Fetch a payer's past transactions, newest first. "
                    "Use `before` (ISO-8601) to exclude the current transaction and anything after it."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "payer_id": {"type": "string"},
                        "before": {"type": "string", "description": "ISO-8601 timestamp, exclusive"},
                        "limit": {"type": "integer", "default": 50},
                    },
                    "required": ["payer_id"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict) -> dict:
        try:
            if name == "get_transaction":
                doc = self._col.find_one({"transaction_id": arguments["transaction_id"]})
                if doc is None:
                    return _text_result({"error": f"transaction {arguments['transaction_id']} not found"}, True)
                return _text_result(_clean(doc))

            if name == "get_payer_history":
                query: dict = {"payer_id": arguments["payer_id"]}
                if arguments.get("before"):
                    query["timestamp"] = {"$lt": datetime.fromisoformat(arguments["before"])}
                cursor = self._col.find(query).sort("timestamp", -1).limit(int(arguments.get("limit", 50)))
                return _text_result([_clean(d) for d in cursor])

            return _text_result({"error": f"unknown tool {name}"}, True)
        except (KeyError, ValueError) as exc:
            return _text_result({"error": f"invalid arguments: {exc}"}, True)


class MCPClient:
    """What graph nodes use. Unwraps the MCP result envelope and raises on tool errors."""

    def __init__(self, server: MCPServer):
        self._server = server

    def list_tools(self) -> list[dict]:
        return self._server.list_tools()

    def call_tool(self, name: str, **arguments) -> Any:
        result = self._server.call_tool(name, arguments)
        payload = json.loads(result["content"][0]["text"])
        if result.get("isError"):
            raise RuntimeError(f"MCP tool '{name}' failed: {payload.get('error', payload)}")
        return payload
