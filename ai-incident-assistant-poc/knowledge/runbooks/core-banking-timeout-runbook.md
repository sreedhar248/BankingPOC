---
title: Core Banking Dependency Timeout
service: payment-service
environment: prod
region: [dc1, dc2]
owner: core-integration-team
version: "2.0"
status: approved
effective_date: 2026-03-01
last_reviewed: 2026-09-10
sensitivity: internal
---
# Core Banking Dependency Timeout

## Symptoms
Payment API returns 504. Dependency telemetry shows core-banking calls exceeding the 30s timeout.

## Important: ambiguous outcome
> WARNING: A timeout means the caller got no response. The debit MAY have been executed. Do NOT retry or replay until reconciliation confirms the outcome.

## Checks
1. Dependency failure rate and duration by region.
2. Recent network/firewall changes on the DC to core-banking path.
3. Core-banking side health (engage core-integration-team).
4. Mesh upstream resets to the core-banking egress.

## Safe actions
- Engage network team and core-integration-team.
- Hold client retries if possible (gateway policy change needs approval).
- Start reconciliation for the impacted window.
