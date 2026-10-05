"""Fixed incident report structure: facts, hypotheses and unknowns kept separate.

Session: I3  (see docs/SESSION_PLAN.md)
"""
from pydantic import BaseModel


class Evidence(BaseModel):
    call_id: str          # tool call or retrieval chunk id
    source: str           # e.g. app-insights, runbook v4.2
    timestamp: str


class Fact(BaseModel):
    statement: str
    evidence: list[Evidence]   # must be non-empty


class Hypothesis(BaseModel):
    statement: str
    supporting_evidence: list[Evidence]
    how_to_validate: str


class IncidentReport(BaseModel):
    customer_impact: str
    confirmed_facts: list[Fact]
    likely_failure_boundary: str
    hypotheses: list[Hypothesis]
    unknowns: list[str]
    recent_changes: list[str]
    safe_immediate_actions: list[str]
    teams_to_engage: list[str]
    actions_requiring_approval: list[str]
    recovery_verification: list[str]
