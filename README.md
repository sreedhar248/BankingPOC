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

**Session 4 (complete):** Simulated APIM AI Gateway (`ai_gateway.py`) in front
of the Claude call: request and token rate limits, per-call metering/logging,
and response caching. `claude_investigator.investigate()` now routes through
it. See [Session 4: simulated APIM AI Gateway](#session-4-simulated-apim-ai-gateway).

**Planned (Sessions 5–7):**
- Additional deterministic signals: velocity, counterparty risk, pattern matching
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
python test_ai_gateway.py   # Session 4 gateway tests (no API calls)
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

## Session 4: simulated APIM AI Gateway

No Azure subscription was available, so `ai_gateway.py` implements the
*pattern* of an APIM policy pipeline locally, in the same order APIM would
run it (cache lookup, then limits, then backend call, then metering). Every
control below has a direct APIM policy equivalent, so the design carries over
to a real instance as configuration rather than a rewrite.

| Local control (`ai_gateway.py`) | Real Azure APIM equivalent | Notes / differences |
|---|---|---|
| Requests-per-minute limit (`requests_per_minute`, sliding 60s window) | [`rate-limit-by-key`](https://learn.microsoft.com/azure/api-management/rate-limit-by-key-policy) (`calls`, `renewal-period`, `counter-key`) | Ours is one global counter; APIM keys it per subscription, IP, or any expression (`counter-key`). |
| Tokens-per-minute limit (`tokens_per_minute`; prompt estimated up front, actual usage counted after) | [`azure-openai-token-limit`](https://learn.microsoft.com/azure/api-management/azure-openai-token-limit-policy) / generic [`llm-token-limit`](https://learn.microsoft.com/azure/api-management/llm-token-limit-policy) (`tokens-per-minute`, `estimate-prompt-tokens`) | Same two-phase model: estimate the prompt to decide admission, then count real usage. We estimate with `chars/4`; APIM uses a real tokenizer. |
| `RateLimitExceeded` with `retry_after` | HTTP `429 Too Many Requests` + `Retry-After` header | The investigator catches it and falls back to the deterministic alert, so a throttled call degrades instead of failing. |
| `gateway.log` entries + `usage_summary()` (timestamp, transaction_id, input/output tokens, estimated cost, status) | APIM diagnostic logging to Azure Monitor / Application Insights, plus [`azure-openai-emit-token-metric`](https://learn.microsoft.com/azure/api-management/azure-openai-emit-token-metric-policy) / `llm-emit-token-metric` for per-caller token and cost metrics | Cost uses placeholder list prices (`PRICING_PER_MTOK`), not billing data. |
| Response cache keyed on `transaction_id` + flag reasons, 1h TTL | [`cache-lookup` / `cache-store`](https://learn.microsoft.com/azure/api-management/cache-lookup-policy) (exact match, `duration="3600"`, `vary-by`) | We are exact-match only. [`azure-openai-semantic-cache-lookup`](https://learn.microsoft.com/azure/api-management/azure-openai-semantic-cache-lookup-policy) is the fuzzy version: it matches on embedding similarity and needs a Redis-compatible cache plus an embeddings deployment. |
| Cache checked *before* limits | Policy order in `<inbound>`: `cache-lookup` before the limit policies | Cache hits consume no request or token quota. |
| `AnthropicBackend` | APIM backend entity / `set-backend-service` | Swapping it for a call to the APIM endpoint is the actual migration step. |
| No key or throttled: deterministic fallback | No APIM policy; application-level degradation | APIM can only return the 429 or an error. The fallback lives in the app. |

**Known gaps vs real APIM:** counters and cache are in-process (APIM uses a
distributed store, so limits hold across instances); there is a single
global bucket rather than per-caller keys; and there is no authentication
policy (`validate-jwt` / subscription keys) yet.

### Test run (Session 4)

`python test_ai_gateway.py` uses a fake backend and a fake clock, so it makes
no API calls and doesn't wait a real minute:

| Scenario | Result |
|---|---|
| 5 rapid calls at 3 requests/min | calls 1-3 reach the backend; 4-5 throttled (`429 requests-per-minute`) and fall back cleanly; after the window passes, calls succeed again |
| ~400 tokens/call at 1000 tokens/min | 2 calls fit; the 3rd is rejected by the **token** limit even though the request limit has headroom |
| Same transaction + reasons twice | 2nd served from cache, backend called once, and it consumes no quota (even at 1 request/min) |
| Different reasons for the same transaction | cache miss |
| Call after the 1h TTL | cache entry expired, backend called again |
| No API key | deterministic fallback, backend never called |

The Session 3 scenarios run unchanged through the gateway against the real
Claude API (TXN-3001: ~1,100 input / ~220 output tokens, about $0.007 at the
placeholder Sonnet rates).
