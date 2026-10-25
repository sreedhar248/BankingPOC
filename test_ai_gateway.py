"""
Exercises the simulated APIM gateway with a fake backend and fake clock:
no API calls, no waiting. Run: pytest test_ai_gateway.py
"""

import threading
from contextlib import contextmanager

import langfuse
import pytest

import tracing
from ai_gateway import AIGateway, RateLimitExceeded
from claude_investigator import MODEL, investigate
from fakes import FakeBackend, FakeClock


def txn(n):
    return {"transaction_id": f"TXN-{n}", "amount": 9800.0}


def used_llm(result):
    return result["investigation"].startswith("FAKE-LLM")


def test_request_limit_throttles_then_recovers():
    clock, backend = FakeClock(), FakeBackend()
    gw = AIGateway(backend, requests_per_minute=3, tokens_per_minute=1_000_000, clock=clock)

    results = [investigate(txn(i), [], [f"reason {i}"], gateway=gw) for i in range(1, 6)]

    assert all(used_llm(r) for r in results[:3]) and backend.calls == 3
    assert not any(used_llm(r) for r in results[3:]), "calls 4-5 should be throttled and fall back"
    assert [r["gateway_status"] for r in results] == ["ok", "ok", "ok", "throttled", "throttled"]
    assert results[3]["investigation"] == "reason 4", "fallback should carry the flag reason"

    clock.advance(61)
    assert used_llm(investigate(txn(6), [], ["reason 6"], gateway=gw)), "window passed, calls succeed again"


def test_token_limit_is_separate_from_request_limit():
    clock, backend = FakeClock(), FakeBackend()  # 300 in + 100 out = 400 tokens per call
    gw = AIGateway(backend, requests_per_minute=100, tokens_per_minute=1000, clock=clock)

    results = [investigate(txn(i), [], [f"reason {i}"], gateway=gw) for i in range(1, 5)]

    assert used_llm(results[0]) and used_llm(results[1]) and backend.calls == 2
    assert gw.log[2]["limit"] == "tokens-per-minute"
    assert gw.log[3]["status"] == "throttled"


def test_cache_serves_repeats_without_consuming_quota():
    clock, backend = FakeClock(), FakeBackend()
    gw = AIGateway(backend, requests_per_minute=1, tokens_per_minute=1_000_000, clock=clock)

    first = investigate(txn(1), [], ["big jump"], gateway=gw)
    second = investigate(txn(1), [], ["big jump"], gateway=gw)
    assert backend.calls == 1
    assert (first["gateway_status"], second["gateway_status"]) == ("ok", "cache_hit")
    assert second["investigation"] == first["investigation"]
    assert gw.log[-1]["status"] == "cache_hit", "a cache hit is not throttled even at 1 request/min"

    investigate(txn(1), [], ["different reason"], gateway=gw)
    assert gw.log[-1]["status"] == "throttled", "different reasons miss the cache (here: hits the rpm=1 limit)"

    clock.advance(3601)
    investigate(txn(1), [], ["big jump"], gateway=gw)
    assert backend.calls == 2, "after the 1h TTL the entry expires and the backend is called again"


def test_no_api_key_falls_back_through_the_gateway():
    backend = FakeBackend(available=False)
    gw = AIGateway(backend, clock=FakeClock())

    result = investigate(txn(1), [], ["big jump"], gateway=gw)

    assert backend.calls == 0
    assert result["investigation"] == "big jump"
    assert result["gateway_status"] == "no_api_key"
    assert gw.log[-1]["status"] == "no_api_key"


def test_throttle_can_propagate_instead_of_falling_back():
    gw = AIGateway(FakeBackend(), requests_per_minute=1, clock=FakeClock())
    investigate(txn(1), [], ["a"], gateway=gw)
    with pytest.raises(RateLimitExceeded) as excinfo:
        investigate(txn(2), [], ["b"], gateway=gw, fallback_on_throttle=False)
    assert excinfo.value.limit_name == "requests-per-minute" and excinfo.value.retry_after > 0


def test_concurrent_requests_are_safe_and_keep_their_own_records():
    # Smoke test of the threaded path the HTTP API uses. The race the gateway lock guards against
    # (a deque mutated mid-sum) is too narrow to reproduce reliably, so this does not prove the lock.
    backend = FakeBackend(delay=0.002)  # sleep inside the call so threads interleave
    gw = AIGateway(backend, requests_per_minute=10_000, tokens_per_minute=10_000_000, clock=FakeClock())
    errors, mismatches = [], []

    def worker(worker_id):
        for i in range(25):
            tid = f"TXN-{worker_id}-{i}"
            try:
                result = gw.invoke(
                    transaction_id=tid, reasons=[tid], model=MODEL, system="s", user_content="u", max_tokens=10
                )
                if result.record["transaction_id"] != tid:
                    mismatches.append((tid, result.record["transaction_id"]))
            except Exception as exc:  # any crash under concurrency is a failure
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors and not mismatches
    assert gw.usage_summary()["backend_calls"] == 200


# ---- Langfuse tracing (fake client, no network) -------------------------------


class Recorded:
    def __init__(self, name, as_type, attrs):
        self.name, self.as_type, self.attrs, self.error = name, as_type, dict(attrs), None

    def update(self, **kwargs):
        self.attrs.update(kwargs)


class RecordingClient:
    def __init__(self):
        self.observations = []

    @contextmanager
    def start_as_current_observation(self, *, name, as_type="span", **attrs):
        obs = Recorded(name, as_type, attrs)
        self.observations.append(obs)
        try:
            yield obs
        except BaseException as exc:
            obs.error = exc
            raise


def test_tracing_is_off_without_keys():
    assert not tracing.enabled() and tracing.callbacks() == []


def test_tracing_records_span_and_generation_for_every_gateway_status(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(langfuse, "get_client", lambda: client)
    monkeypatch.setattr(tracing, "enabled", lambda: True)

    def observed(gateway, n, reasons):
        before = len(client.observations)
        investigate(txn(n), [], reasons, gateway=gateway)
        return client.observations[before:]

    gw = AIGateway(FakeBackend(), requests_per_minute=1, tokens_per_minute=1_000_000, clock=FakeClock())

    span, generation = observed(gw, 1, ["big jump"])
    assert (span.name, generation.name) == ("ai-gateway", "claude-call")
    assert span.attrs["metadata"]["gateway_status"] == "ok" and span.attrs["metadata"]["input_tokens"] == 300
    assert generation.as_type == "generation" and generation.attrs["model"] == MODEL
    assert generation.attrs["input"][0]["role"] == "system"
    assert "FAKE-LLM" in generation.attrs["output"]
    assert generation.attrs["usage_details"] == {"input": 300, "output": 100}
    assert generation.attrs["cost_details"]["total"] > 0

    (span,) = observed(gw, 1, ["big jump"])
    assert span.attrs["metadata"]["gateway_status"] == "cache_hit"

    (span,) = observed(gw, 1, ["different reason"])
    assert span.attrs["metadata"]["gateway_status"] == "throttled" and span.attrs["level"] == "WARNING"
    assert span.error is None, "throttling is expected, not a span error"

    gw = AIGateway(FakeBackend(available=False), clock=FakeClock())
    (span,) = observed(gw, 2, ["big jump"])
    assert span.attrs["metadata"]["gateway_status"] == "no_api_key"
