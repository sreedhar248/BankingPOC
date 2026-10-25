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

**Session 6 (complete):** Langfuse tracing. Each scenario is one trace
(graph nodes, the gateway, and the Claude call as a generation with token
counts). Entirely optional: with no `LANGFUSE_*` keys the code runs exactly as
before. See [Session 6: Langfuse tracing](#session-6-langfuse-tracing).

**Session 7 (complete):** the agent is now a deployable service. The graph moved
to `agent.py` (the session scripts are thin runners), `api.py` exposes it over
HTTP with API-key auth, and a `Dockerfile` + `docker-compose.yml` package it. See
[Session 7: deployable service](#session-7-deployable-service).

**Still planned:**
- Additional deterministic signals: velocity, counterparty risk, pattern matching
- Kong
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

Requires **Python 3.10+** (the current Langfuse SDK does not support 3.9).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python session1_langgraph_basics.py
python session2_langgraph_mcp.py
python session3_langgraph_claude.py
pytest                      # all tests; keyless and offline (no API calls)
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
global bucket rather than per-caller keys. Caller authentication, originally a
gap here, is added in Session 7 as a shared-key check on the API (APIM would
replace it with subscription keys / `validate-jwt`).

### Test run (Session 4)

`pytest test_ai_gateway.py` uses a fake backend and a fake clock, so it makes
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

## Session 6: Langfuse tracing

Tracing is wired through `tracing.py`, which is a no-op unless
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` are all
set (copy them into `.env`; use the base URL of the region your project was
created in, e.g. `https://us.cloud.langfuse.com`). Without them nothing is
imported or sent, and the tests run offline.

How it fits together:

- `session3_langgraph_claude.py` wraps each scenario in one root span named
  after the `transaction_id`, tagged with the scenario label, and passes
  Langfuse's LangChain `CallbackHandler` to `app.invoke()`. Every graph node
  becomes a span. `langfuse.flush()` runs before exit so buffered events are sent.
- `ai_gateway.py` opens an `ai-gateway` span around each request, with the
  gateway status (`ok` / `cache_hit` / `throttled` / `no_api_key`) as metadata.
  Around the backend call it records a `claude-call` **generation** with model,
  input messages, output, `usage_details` and `cost_details`. The raw Anthropic
  SDK call is invisible to the LangChain callback handler, and recording it in
  the gateway also covers any future backend (e.g. a real APIM endpoint).
- A throttled call is recorded as a `WARNING` span, not an error, since it is an
  expected, handled outcome.

Resulting trace for TXN-3001 (verified in the project):

```
TXN-3001 (root span)
  LangGraph
    ingest_transaction / detect_anomaly / route_after_detection
    send_alert
      ai-gateway            gateway_status=ok
        claude-call         model, messages, output, 1132 in / 207 out tokens, $0.0065
```

What Langfuse shows, versus what you had before:

| Langfuse shows | Earlier equivalent |
|---|---|
| One trace per transaction: name = `transaction_id`, tag = scenario label, root input/output (`is_anomalous`, `reasons`, `signals`) | The `--- TXN-xxxx: label ---` header and `Result: is_anomalous=...` print lines |
| One span per graph node with its timing and the state it returned. `detect_anomaly` returns `signals`: `avg`, `std`, `ratio`, `z_score`, `history_count` and the thresholds, for clean and anomalous transactions alike | The `[ingest]`, `[detect]`, `[send_alert]`, `[no_op]` print lines (no timing existed; the baseline numbers were only in the `[detect]` print) |
| `ai-gateway` span metadata: `gateway_status`, `input_tokens`, `output_tokens`, `estimated_input_tokens`, `cost_usd`, `limit`, `retry_after_seconds` | The `[gateway] ok / cache_hit / throttled / no_api_key` print lines and `gateway.log` entries |
| `claude-call` generation: model, full system + user prompt, full response, token usage, cost, latency | The `[gateway] ok ... in= out= cost=` line, which had tokens and cost only. The prompt and response were not visible anywhere. |
| Cost and token totals across traces (Langfuse dashboards) | `gateway.usage_summary()`, per process only |
| Throttled call: `WARNING` span with the `429 ...` message | `[gateway] throttled` plus the `[claude_investigator] ... using deterministic fallback` print |

The print lines and `gateway.log` are unchanged and still work with tracing off.

**Notes:**

- Cost in Langfuse is the gateway's placeholder estimate (`PRICING_PER_MTOK`),
  passed explicitly. It is not Langfuse's own model price table or your bill.
- To read traces back via API on a new Langfuse project, use
  `GET /api/public/v2/observations` (SDK: `api.observations.get_many`). The
  older `/api/public/traces` endpoint returns 410 for projects created on or
  after 2026-09-16. Trace name and tags are in the `trace_context` field group.

**Known gaps:**

- **No sampling:** every run is traced.
- **No PII masking:** full prompts and responses (payer names, amounts, payees)
  are sent to Langfuse cloud. The data here is synthetic. Real NPP data would
  need a masking function on the client and a data-residency review first.
- **No evals or scores:** nothing rates the quality of Claude's investigation
  text yet.
- **Fallbacks are not observed:** a Claude reply that fails JSON parsing, or the
  no-key fallback, shows only as the final alert text and a print line. Only the
  gateway status is on the span.
- Traces cover the Session 3 entry point only. Sessions 1 and 2 are untraced.

### Test run (Session 6)

| Check | Result |
|---|---|
| `pytest test_ai_gateway.py` (tracing tests, fake Langfuse client, no network) | span and generation attributes verified for `ok`, `cache_hit`, `throttled`, `no_api_key`; throttling not recorded as an error |
| `session3_langgraph_claude.py` with all keys blank | identical behaviour to Session 4, no errors, `langfuse` never imported |
| `session3_langgraph_claude.py` with keys, 4 scenarios | 4 traces in the Langfuse project; TXN-3001 shows the Claude generation with token counts |

## Session 7: deployable service

The graph now lives in `agent.py` and is served over HTTP by `api.py` (FastAPI).
`session3_langgraph_claude.py` is a thin runner over the same graph; sessions 1 and 2
remain standalone teaching scripts.

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | Liveness probe: `{"status": "ok"}` |
| `POST /analyze` | `X-API-Key` | `{"transaction_id": "..."}` returns `is_anomalous`, `reasons`, `signals`, `investigation`, `alert_message`, `gateway_status` |

`investigation`, `alert_message` and `gateway_status` are `null` for transactions that
were not flagged (Claude is never called for them). `gateway_status` is
`ok` / `cache_hit` / `no_api_key` / `throttled`. Status codes: `401` missing or wrong key,
`404` unknown `transaction_id`, `422` malformed body, `429` see below.

**Auth.** `API_KEYS` is a comma-separated list; a request must send one as `X-API-Key`.
If `API_KEYS` is empty or unset, every request is rejected (fail closed). Comparison is constant-time.

**Throttling.** If the AI gateway throttles the Claude call, the default is to still return the
deterministic fallback alert (HTTP 200, `gateway_status: "throttled"`), because a missed
compliance alert is worse than a less detailed one. Set `THROTTLE_FALLBACK=false` to return
`429` with a `Retry-After` header instead.

### Run it

Locally:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # make a key, put it in .env as API_KEYS=
uvicorn api:app --env-file .env
```

Docker (keys come from the environment at run time and are never baked into the image):

```bash
docker build -t banking-poc .
docker run --rm -p 8000:8000 -e API_KEYS=<your-key> -e ANTHROPIC_API_KEY=<optional> banking-poc
```

Compose (reads `.env` in this folder; refuses to start if `API_KEYS` is unset):

```bash
docker compose up --build                       # API with the in-memory demo data
docker compose --profile mongo up --build       # plus a real MongoDB service
```

For the `mongo` profile, set `MONGODB_URI=mongodb://mongo:27017` and `SEED_DEMO_DATA=true` in `.env`.
`SEED_DEMO_DATA` only loads the demo data into an empty collection.

Tests (keyless and offline, whatever is in your `.env` or shell):

```bash
pip install -r requirements.txt
pytest
```

### Example calls

```bash
curl http://localhost:8000/healthz

curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"transaction_id": "TXN-3001"}'
```

Response for TXN-3001 (no Anthropic key configured, so the deterministic fallback text):

```json
{
  "is_anomalous": true,
  "reasons": ["amount $9,800.00 is 10.6x payer average $923.33 (43.0 std devs above, n=6)"],
  "signals": {"amount": 9800.0, "history_count": 6, "avg": 923.33, "std": 206.61, "ratio": 10.61, "z_score": 42.96, "...": "..."},
  "investigation": "amount $9,800.00 is 10.6x payer average $923.33 (43.0 std devs above, n=6)",
  "alert_message": "ALERT: transaction TXN-3001 ($9,800.00) flagged as anomalous \u2014 amount $9,800.00 is 10.6x ...",
  "gateway_status": "no_api_key"
}
```

With an `ANTHROPIC_API_KEY`, `investigation` and `alert_message` are Claude's text and
`gateway_status` is `ok`; repeating the request returns `cache_hit`. Other outcomes:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/analyze -H "Content-Type: application/json" -d '{"transaction_id":"TXN-3001"}'                          # 401 no key
curl -s -w " %{http_code}\n" -X POST localhost:8000/analyze -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" -d '{"transaction_id":"NOPE"}'   # 404
```

### What each part maps to in production (AKS/EKS + APIM)

| Here | Production equivalent | Notes |
|---|---|---|
| `X-API-Key` checked against `API_KEYS` | APIM subscription keys (`Ocp-Apim-Subscription-Key`), or the `validate-jwt` inbound policy for Entra ID / OAuth tokens | APIM gives per-caller identity, revocation and quotas. Once APIM fronts the service, restrict the backend to APIM traffic (private networking, mTLS, or a header set by APIM) or the bypass defeats the check. Keep a key check as defence in depth if you like. |
| `401` + `WWW-Authenticate` | APIM returns `401` for a missing or invalid subscription key or token | Same client-visible contract. |
| `429` + `Retry-After` (`THROTTLE_FALLBACK=false`) | `rate-limit-by-key` / `llm-token-limit` returning `429` at the gateway | See the Session 4 table. Here the limit protects only the Claude call and is enforced inside the service. |
| `GET /healthz` | Kubernetes `livenessProbe` / `readinessProbe` (`httpGet`), plus the APIM backend or load-balancer probe | Liveness only: it does not check MongoDB or Claude, so a real readiness probe would need a dependency check. |
| `Dockerfile` (non-root uid 10001, no secrets, runtime files only) | Image in ACR / ECR; pod `securityContext` with `runAsNonRoot: true`, `runAsUser: 10001`, `readOnlyRootFilesystem: true` | The healthcheck in the Dockerfile is ignored by Kubernetes; use probes. |
| `docker-compose.yml` `environment:` | Deployment manifest or Helm chart: `ConfigMap` for settings, `Secret` for keys | `restart: unless-stopped` becomes the Deployment's self-healing. |
| `ANTHROPIC_API_KEY`, `API_KEYS` from `.env` | Kubernetes `Secret`, ideally synced from Azure Key Vault (CSI Secrets Store driver) or AWS Secrets Manager | Never in the image or the manifest in plain text. |
| Compose `mongo` service | Managed MongoDB: Atlas, Azure Cosmos DB for MongoDB, or Amazon DocumentDB, with `MONGODB_URI` from a Secret | `mongo:7` with a local volume is for development only. |
| One uvicorn worker; gateway counters and cache in process memory | Replicas behind a Service / Ingress, scaled by HPA | N replicas means N separate rate-limit counters and caches, so the effective limit is N times higher. APIM's distributed counters (or a shared Redis) fix that. |
| TLS is not handled here | TLS terminates at the Ingress or APIM | The service listens on plain HTTP inside the cluster. |

**Known gaps:**

- **Shared static keys:** no per-caller identity, quotas or revocation without changing `API_KEYS` and restarting.
- **`/analyze` itself is not rate limited.** The gateway limits Claude calls only. A caller can still drive MongoDB and the detection logic as fast as the service will go.
- **`/docs` and `/openapi.json` are served without auth** (FastAPI defaults).
- **No Langfuse tracing in the API yet.** Tracing still works from `session3_langgraph_claude.py`; the container does not receive `LANGFUSE_*`.
- **Synchronous requests:** a flagged transaction holds a worker thread for the duration of the Claude call.
- **Image size:** about 400 MB, mostly LangChain and Langfuse dependencies that this service barely uses.
- **Default data is in memory:** the demo data resets on every restart unless `MONGODB_URI` is set.

### Test run (Session 7)

| Check | Result |
|---|---|
| `pytest` (keyless, offline) | 26 tests pass: auth (missing, wrong, second key, fail-closed), clean and insufficient-history verdicts, anomalous with and without a Claude key, cache hit, 404, 422, throttle fallback, 429 with `Retry-After`, plus the existing gateway and tracing tests |
| `docker build` | non-root `appuser`, runtime modules only, no `.env` in the image |
| `docker run`, then `curl /analyze` for TXN-3001 | `200`, `is_anomalous: true`, `gateway_status: "no_api_key"`; no key `401`; unknown id `404`; healthcheck `healthy` |
| 60 parallel requests against the container | 40 `200`, 20 expected `404`, no `500`s, no tracebacks |
| `docker compose --profile mongo up` | 25 demo documents seeded into real MongoDB, TXN-3001 anomalous through it, restart did not re-seed |
| `docker compose up` with a real Anthropic key | TXN-3001 `gateway_status: "ok"` with Claude's investigation, repeat call `cache_hit` |
