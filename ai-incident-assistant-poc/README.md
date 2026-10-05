# AI Incident Assistant: Governed RAG + Read-Only Tools POC

A proof of concept for a **governed, identity-aware AI incident assistant** on a banking platform (API gateway → Kubernetes/Istio → core banking, plus Service Bus and an identity provider).

It answers *"what's going on with this incident?"* by:
- **retrieving** approved, current runbooks (hybrid RAG with metadata filters)
- **querying** live evidence through a **read-only tool gateway** (allowlists, bounded windows, masking, audit)
- **reasoning** with Claude over that evidence only
- **reporting** facts, hypotheses and unknowns separately, each with a citation
- **never** acting on production. State-changing actions go to a human approval gate

📖 **Start here:** [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md)  ·  🗓️ **Build plan:** [`docs/SESSION_PLAN.md`](docs/SESSION_PLAN.md)

## Reference incident
Mobile payment failures rise from 1% to 35% at 09:30. Auth is fine, the gateway is healthy, and core-banking calls time out. A firewall change landed at 09:25. The assistant has to find the boundary, flag the change as a **hypothesis**, and **not** call timed-out payments failed.

## Layout
```
ai-incident-assistant-poc/
├── docs/                 HOW_IT_WORKS.md, SESSION_PLAN.md
├── knowledge/            Approved (and one obsolete) runbooks + known errors → RAG corpus
├── mock_telemetry/       JSON stand-ins for App Insights, K8s, Istio, changes, Service Bus
├── src/
│   ├── tools/gateway.py  Read-only tool gateway (I1)
│   ├── retrieval/        ingest.py + search.py (I2)
│   ├── orchestrator.py   LangGraph flow (I3)
│   ├── report_schema.py  Facts / hypotheses / unknowns (I3)
│   └── guardrails/       grounding, payment safety, injection, masking, approval (I4)
├── eval/                 Scenario YAMLs + scorecard runner (I5)
└── tests/
```

## Progress
| Session | Status |
|---|---|
| I1 Mock world + tool gateway | ⬜ |
| I2 Ingestion + hybrid retrieval | ⬜ |
| I3 Orchestrator + cited report | ⬜ |
| I4 Guardrails | ⬜ |
| I5 Evaluation + Langfuse | ⬜ |

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
pytest
```

## Scope (POC restrictions)
Offline/mock data only · read-only tools · no replay, restarts, policy changes or failover · no customer data · bounded time windows · a human reviews all recommendations.
