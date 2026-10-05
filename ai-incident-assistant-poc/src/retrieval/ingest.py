"""Knowledge ingestion: approve-gate -> contextual chunking -> metadata -> embed.

Session: I2  (see docs/SESSION_PLAN.md)

Rules:
  - Quarantine docs with no owner, status != approved, or expired review.
  - Chunk by heading / procedure step; keep WARNING blocks with their action.
  - Every chunk keeps: title, service, environment, region, owner, version,
    status, effective_date, last_reviewed, sensitivity, heading_path.
"""


def ingest(knowledge_dir: str = "knowledge") -> list[dict]:
    raise NotImplementedError("Build in Session I2")
