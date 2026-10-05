"""LangGraph orchestrator.

Session: I3  (see docs/SESSION_PLAN.md)

Graph:
  classify -> authorize -> retrieve_knowledge -> gather_evidence
           -> reason (Claude) -> grounding_check -> audit -> report

Principles (same as compliance POC S3):
  - Evidence is gathered deterministically by tools; Claude explains and drafts.
  - Retrieved text and tool output go in clearly tagged DATA blocks, never in
    the system prompt. They are not instructions.
"""


def investigate(user: str, question: str) -> dict:
    raise NotImplementedError("Build in Session I3")
