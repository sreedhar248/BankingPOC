import pytest

# Every test runs keyless and offline, even on a machine that has real keys exported.
_ENV_TO_CLEAR = (
    "ANTHROPIC_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "MONGODB_URI",
    "SEED_DEMO_DATA",
    "GATEWAY_RPM",
    "GATEWAY_TPM",
    "API_KEYS",
    "THROTTLE_FALLBACK",
)


@pytest.fixture(autouse=True)
def keyless_env(monkeypatch):
    for name in _ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
