# Sunday Session Plan: AI Incident Assistant POC

An extra Sunday track that runs alongside the Banking Compliance Agent sessions. Each session is about 2–3 hours, built with Claude Code, and pushed to GitHub at the end.

It reuses what you've already built: the **LangGraph** flow pattern (S1), the **MCP-style tool layer** (S2), **Claude explaining instead of deciding** (S3), the **AI gateway** (S4) and **Langfuse** (S6).

| # | Session | You build | Done when |
|---|---|---|---|
| **I1** | Mock world + tool gateway | `mock_telemetry/` loader, `src/tools/gateway.py` with allowlists, bounded windows, RBAC check, masking, audit log | Valid calls return masked data. Bad env, oversized window, free-form query and unauthorized user are all rejected and logged |
| **I2** | Knowledge ingestion + hybrid retrieval | `src/retrieval/ingest.py` (approval/owner/expiry gate, heading-based chunks, metadata), `src/retrieval/search.py` (embeddings + BM25 + filters + rerank) | DR question returns the **v4.2 approved** chunk *with* the "fence first" warning. The obsolete v3.1 runbook is never returned |
| **I3** | Orchestrator + cited report | `src/orchestrator.py` LangGraph: classify → retrieve → gather_evidence → reason (Claude) → report. `src/report_schema.py` (facts / hypotheses / unknowns) | Reference incident gives a report with every fact citing a tool call ID and timestamp |
| **I4** | Guardrails | `src/guardrails/`: grounding check, payment-safety wording, prompt-injection neutralizer, approval gate | Injected log line is ignored. "Replay payments" is never recommended. Unsupported claims are stripped or flagged |
| **I5** | Evaluation harness + Langfuse | `eval/run_eval.py` over `eval/scenarios/*.yaml`, scorecard output, Langfuse traces | All 9 critical scenarios scored. Safety and authorization at 100%. Scorecard written to `eval/results.md` |

## Optional stretch (I6)
- Put the orchestrator behind FastAPI and reuse the compliance POC's `ai_gateway.py` for token limits and caching
- Add an "approval request" output that drafts a change ticket for a human (never runs it)

## Rules for every session
1. Detection and evidence are deterministic. Claude **explains and drafts**, it doesn't invent evidence (same principle as compliance POC S3).
2. No real client names, systems or data. Everything is generic and mocked.
3. Tests use fake clocks and data, so there's no real waiting and no API cost in unit tests.
4. Update `README.md` progress table at the end of each session.
