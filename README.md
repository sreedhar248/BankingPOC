# Banking Compliance Agent (POC)

An agentic AI proof-of-concept that detects NPP (New Payments Platform)
transaction anomalies and raises alerts — built as a portfolio piece
demonstrating platform/architecture thinking for AI-driven banking
workflows, not just LLM prompting.

## Status

**Session 1 (complete):** LangGraph pipeline skeleton with toy threshold
detection. Proves the pipeline shape end-to-end.

**Planned (Sessions 2–7):**
- MCP-backed MongoDB queries for real transaction history/context
- Claude API reasoning replacing the fixed threshold (velocity,
  historical baseline, counterparty risk, pattern matching)
- APIM AI Gateway in front of the service
- Kong
- Langfuse tracing/observability
- Full end-to-end POC build

## Architecture

### Flow (Session 1)

```
ingest_transaction -> detect_anomaly -> [conditional] -> send_alert / no_op
```

### Target flow (later sessions)

```
NPP transaction event
   -> APIM (auth, rate-limit, routing)
   -> EKS pod (LangGraph agent, containerized via Docker, exposed via FastAPI)
   -> MongoDB (transaction history/context) + Claude API (reasoning)
   -> MCP -> Slack/JIRA alert
```

### Deployment layers

FastAPI, Docker/EKS, and APIM are **sequential layers**, not
alternative choices — each wraps the one below it:

| Layer | Role |
|---|---|
| APIM | Gatekeeper: auth, rate-limiting, logging, routing |
| Docker + EKS | Runs the app continuously, at scale, always-on |
| FastAPI | Wraps the LangGraph code so it's reachable over HTTP |
| Python (LangGraph) | The agent logic itself |

### Why detection logic stays deterministic

Anomaly *detection* is deliberately kept out of the LLM's hands. A
rules/statistical layer flags transactions; Claude's role is reasoning
over already-flagged transactions — investigation, explanation, and
alert drafting — not the initial compliance decision. This keeps the
audit trail defensible, which matters for anything touching banking
compliance.

## Setup

```bash
pip install -r requirements.txt
python session1_langgraph_basics.py
```

## Test run (Session 1)

| Transaction | Amount | Result |
|---|---|---|
| TXN-001 | $1,250 | clean -> `no_op` |
| TXN-002 | $87,500 | anomalous -> `send_alert` |
