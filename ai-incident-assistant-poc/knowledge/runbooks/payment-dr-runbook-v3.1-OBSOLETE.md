---
title: Payment DR Runbook
service: payment-service
environment: prod
region: [dc1, dc2]
owner: platform-dr-team
version: "3.1"
status: superseded
effective_date: 2024-01-10
last_reviewed: 2024-06-01
sensitivity: internal
---
# Payment DR Runbook (OLD)

## Failover
1. Activate DC2.
2. Replay any failed payments from the queue.
