# Banking Compliance Agent (POC)

An agentic AI proof-of-concept that detects NPP (New Payments Platform)
transaction anomalies and raises alerts — built as a portfolio piece
demonstrating platform/architecture thinking for AI-driven banking
workflows, not just LLM prompting.

## Status

**Session 1 (complete):** LangGraph pipeline skeleton with toy threshold
detection. Proves the pipeline shape end-to-end.

**Session 2 (complete):** MCP-style tool layer over a MongoDB `transactions`
collection (mongomock, seeded with multi-payer NPP history). `ingest_transaction`
now loads the transaction and payer history via MCP tools by `transaction_id`;
`detect_anomaly` flags amounts far above the payer's own baseline (>=3x average
and >=3 std devs, min 3 prior txns) instead of a fixed $50k threshold.
Files: `mongo_db.py` (data + seed), `mcp_tools.py` (tool contract),
`session2_langgraph_mcp.py` (graph). Set `MONGODB_URI` to use real MongoDB.

**Session 3 (complete):** Claude API investigation layer. Detection is
unchanged and still deterministic (see below) — Claude is only called
*after* `detect_anomaly` flags a transaction, to explain the flag in plain
language and draft the alert text. Falls back to a deterministic summary
if `ANTHROPIC_API_KEY` isn't set, so the graph runs with or without a key.
Files: `claude_investigator.py`, `session3_langgraph_claude.py`. Copy
`.env.example` to `.env` and add your key to enable real reasoning.

**Planned (Sessions 4–7):**
- Additional deterministic signals: velocity, counterparty risk, pattern matching
- APIM AI Gateway in front of the service
- Kong
- Langfuse tracing/observability
- Full end-to-end POC build

See [VISUAL_FLOW.md](VISUAL_FLOW.md) for a plain-language diagram and a full
technical architecture diagram of everything built so far.

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
python session2_langgraph_mcp.py
python session3_langgraph_claude.py
```

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` to enable Claude
investigation in Session 3 (it falls back to a deterministic summary without one).

## Test run (Session 1)

| Transaction | Amount | Result |
|---|---|---|
| TXN-001 | $1,250 | clean -> `no_op` |
| TXN-002 | $87,500 | anomalous -> `send_alert` |

## Test run (Session 2)

| Transaction | Scenario | Result |
|---|---|---|
| TXN-1001 | $210, retail payer avg ~$142 | clean -> `no_op` |
| TXN-2001 | $61k, business payer avg ~$57k | clean -> `no_op` (fixed threshold would false-positive) |
| TXN-3001 | $9.8k, payer avg ~$923 (10.6x) | anomalous -> `send_alert` (fixed threshold would miss) |
| TXN-4001 | new payer, 1 prior txn | `no_op`, insufficient history |

## Test run (Session 3)

Same four scenarios as Session 2; detection results are identical (it's the
same deterministic logic). What's new: TXN-3001's alert now comes from
`send_alert` calling Claude to investigate and draft the message (or the
deterministic fallback, if no API key is set).
