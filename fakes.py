"""Test doubles shared by test_ai_gateway.py and test_api.py."""

import json

from ai_gateway import BackendResult


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeBackend:
    """Stands in for the Claude call: canned JSON reply, fixed token usage, counts its calls."""

    def __init__(self, available=True, input_tokens=300, output_tokens=100, delay=0.0):
        self.calls = 0
        self._available = available
        self._usage = (input_tokens, output_tokens)
        self._delay = delay

    def available(self):
        return self._available

    def __call__(self, **_):
        self.calls += 1
        if self._delay:
            import time

            time.sleep(self._delay)
        text = json.dumps({"investigation": "FAKE-LLM investigation", "alert_message": "FAKE-LLM alert"})
        return BackendResult(text, *self._usage)
