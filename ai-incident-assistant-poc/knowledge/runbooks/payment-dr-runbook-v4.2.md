---
title: Payment DR Runbook
service: payment-service
environment: prod
region: [dc1, dc2]
owner: platform-dr-team
version: "4.2"
status: approved
effective_date: 2026-06-01
last_reviewed: 2026-09-01
sensitivity: internal
---
# Payment DR Runbook

## Failover prerequisites
> WARNING: Never activate DC2 for payments while DC1 can still accept writes. Split-brain risk.

1. Stop new DC1 payment admission at the gateway.
2. Revoke DC1 write authority (fence DC1).
3. Confirm in-flight payments have drained or are recorded for reconciliation.
4. Only then activate DC2.

## RTO / RPO
- RTO: 30 minutes
- RPO: 0 (synchronous replication for the payment ledger)

## Reconciliation after failover
Every payment with a timeout or unknown outcome must be checked against the core-banking ledger before any retry or replay.

## Last test
DR test passed on 2026-08-15.
