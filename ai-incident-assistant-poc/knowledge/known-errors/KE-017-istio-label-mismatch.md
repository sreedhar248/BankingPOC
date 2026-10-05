---
title: KE-017 Istio subset label mismatch
service: payment-service
environment: prod
owner: platform-mesh-team
version: "1.0"
status: approved
effective_date: 2026-02-01
last_reviewed: 2026-08-01
sensitivity: internal
---
# KE-017: Istio subset label mismatch
Symptom: 503 with response flag NR (no route) after deployment.
Cause: DestinationRule subset label does not match pod labels.
Fix: correct the labels via GitOps (change approval required).
