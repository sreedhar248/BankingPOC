FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Runtime modules only: no tests, session runners, or .env (secrets come from the environment at run time).
COPY agent.py api.py ai_gateway.py claude_investigator.py mcp_tools.py mongo_db.py tracing.py ./

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

# One worker: the gateway's rate-limit counters and cache are per-process (see README).
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
