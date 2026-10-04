"""
Exercises the simulated APIM gateway with a fake backend and fake clock:
no API calls, no waiting. Run: python test_ai_gateway.py
"""

import json

from ai_gateway import AIGateway, BackendResult
from claude_investigator import investigate


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeBackend:
    def __init__(self, available=True, input_tokens=300, output_tokens=100):
        self.calls = 0
        self._available = available
        self._usage = (input_tokens, output_tokens)

    def available(self):
        return self._available

    def __call__(self, **_):
        self.calls += 1
        text = json.dumps({"investigation": "FAKE-LLM investigation", "alert_message": "FAKE-LLM alert"})
        return BackendResult(text, *self._usage)


def txn(n):
    return {"transaction_id": f"TXN-{n}", "amount": 9800.0}


failures = 0


def check(label, condition):
    global failures
    failures += not condition
    print(f"  {'PASS' if condition else 'FAIL'}: {label}")


def used_llm(result):
    return result["investigation"].startswith("FAKE-LLM")


print("=== 1. Request limit: 3 requests/min, 5 rapid calls ===")
clock, backend = FakeClock(), FakeBackend()
gw = AIGateway(backend, requests_per_minute=3, tokens_per_minute=1_000_000, clock=clock)
results = [investigate(txn(i), [], [f"reason {i}"], gateway=gw) for i in range(1, 6)]
check("calls 1-3 reached the backend", all(used_llm(r) for r in results[:3]) and backend.calls == 3)
check("calls 4-5 were throttled and fell back cleanly (no crash)", not any(used_llm(r) for r in results[3:]))
check("fallback still carries the flag reason", results[3]["investigation"] == "reason 4")
clock.advance(61)
check("after the 60s window passes, calls succeed again", used_llm(investigate(txn(6), [], ["reason 6"], gateway=gw)))
print("  usage:", gw.usage_summary())

print("\n=== 2. Token limit: 1000 tokens/min, ~400 tokens per call, request limit high ===")
clock, backend = FakeClock(), FakeBackend()
gw = AIGateway(backend, requests_per_minute=100, tokens_per_minute=1000, clock=clock)
results = [investigate(txn(i), [], [f"reason {i}"], gateway=gw) for i in range(1, 5)]
check("first 2 calls fit in the token budget", used_llm(results[0]) and used_llm(results[1]) and backend.calls == 2)
check("3rd call rejected by the TOKEN limit, not the request limit", gw.log[2]["limit"] == "tokens-per-minute")
check("4th call also throttled", gw.log[3]["status"] == "throttled")

print("\n=== 3. Cache: same transaction + same reasons within the hour ===")
clock, backend = FakeClock(), FakeBackend()
gw = AIGateway(backend, requests_per_minute=1, tokens_per_minute=1_000_000, clock=clock)
first = investigate(txn(1), [], ["big jump"], gateway=gw)
second = investigate(txn(1), [], ["big jump"], gateway=gw)
check("repeat call served from cache (backend called once)", backend.calls == 1 and second == first)
check("cache hit not throttled even with rpm=1 (consumes no quota)", gw.log[-1]["status"] == "cache_hit")
investigate(txn(1), [], ["different reason"], gateway=gw)
check("different reasons = cache miss (here: throttled by rpm=1)", gw.log[-1]["status"] == "throttled")
clock.advance(3601)
investigate(txn(1), [], ["big jump"], gateway=gw)
check("after the 1h TTL the entry expires and the backend is called again", backend.calls == 2)

print("\n=== 4. No API key: fallback through the gateway ===")
backend = FakeBackend(available=False)
gw = AIGateway(backend, clock=FakeClock())
result = investigate(txn(1), [], ["big jump"], gateway=gw)
check("deterministic fallback returned, backend never called", backend.calls == 0 and result["investigation"] == "big jump")
check("logged as no_api_key", gw.log[-1]["status"] == "no_api_key")

print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
raise SystemExit(1 if failures else 0)
