"""Hybrid retrieval: identity + metadata filters -> vector + BM25 -> rerank.

Session: I2  (see docs/SESSION_PLAN.md)

Success check: a DR failover question returns the v4.2 approved chunk
including the 'fence DC1 first' warning, never the superseded v3.1 runbook.
"""


def search(query: str, user: str, filters: dict, k: int = 5) -> list[dict]:
    raise NotImplementedError("Build in Session I2")
