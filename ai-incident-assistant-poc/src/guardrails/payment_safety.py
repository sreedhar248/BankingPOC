"""Payment safety: never call timed-out payments 'failed'; never recommend retry/replay before reconciliation; never failover without fencing.

Session: I4  (see docs/SESSION_PLAN.md)
"""
