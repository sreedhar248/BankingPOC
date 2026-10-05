"""Read-only tool gateway: the ONLY path from the assistant to live systems.

Session: I1  (see docs/SESSION_PLAN.md)

Responsibilities:
  1. Authenticate the requesting user (mock: mock_telemetry/users.json)
  2. Authorize environment against the user's RBAC
  3. Expose only named, read-only operations
  4. Validate every parameter against allowlists
  5. Mask sensitive fields BEFORE results reach the model
  6. Append every call (allowed or rejected) to audit.jsonl
"""
from typing import Literal

Environment = Literal["npe", "staging", "prod"]
ALLOWED_REGIONS = {"dc1", "dc2"}
ALLOWED_DEPENDENCIES = {"core-banking", "identity-provider", "service-bus"}
MAX_WINDOW_MINUTES = 120

ALLOWED_TOOLS = {
    "query_requests",
    "query_dependencies",
    "read_pod_health",
    "read_mesh_errors",
    "read_recent_changes",
    "read_queue_metrics",
}


class ToolRejected(Exception):
    """Raised when a call fails RBAC or parameter validation."""


def call_tool(user: str, tool: str, **params) -> dict:
    """Validate -> authorize -> execute (mock) -> mask -> audit -> return.

    Every result must carry a call_id and source so the report can cite it.
    """
    raise NotImplementedError("Build in Session I1")
