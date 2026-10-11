"""
Local stand-in for an Azure APIM policy layer in front of the Claude API.

Policy order mirrors a typical APIM inbound pipeline:
  1. cache lookup          (hits skip everything below, consume no quota)
  2. rate limits           (requests/min AND tokens/min, checked separately)
  3. forward to backend    (the Claude API call)
  4. meter + log + cache store

Token limiting follows the azure-openai-token-limit model: the prompt size is
estimated up front to decide admission, then actual prompt + completion tokens
(as reported by the backend) are counted against the window afterwards.

State is in-process only. Real APIM keeps these counters/caches in a
distributed store and keys them per caller (subscription, IP, ...).
"""

import hashlib
import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import tracing

WINDOW_SECONDS = 60

# Placeholder public list prices in USD per million tokens (input, output).
PRICING_PER_MTOK = {"haiku": (1.00, 5.00), "sonnet": (3.00, 15.00)}
DEFAULT_PRICING = PRICING_PER_MTOK["sonnet"]


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = next(
        (rates for family, rates in PRICING_PER_MTOK.items() if family in model), DEFAULT_PRICING
    )
    return input_tokens / 1e6 * rate_in + output_tokens / 1e6 * rate_out


class RateLimitExceeded(Exception):
    """Equivalent of APIM's 429 Too Many Requests with a Retry-After header."""

    def __init__(self, limit_name: str, retry_after: float):
        self.limit_name = limit_name
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"429 {limit_name} limit exceeded, retry after {self.retry_after:.0f}s")


@dataclass
class BackendResult:
    text: str
    input_tokens: int
    output_tokens: int


@dataclass
class GatewayResult:
    text: Optional[str]  # None only when status == "no_api_key"
    status: str          # "ok" | "cache_hit" | "no_api_key"


class AnthropicBackend:
    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def __call__(self, *, model: str, system: str, user_content: str, max_tokens: int) -> BackendResult:
        from anthropic import Anthropic

        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return BackendResult(
            text=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class AIGateway:
    def __init__(
        self,
        backend=None,
        requests_per_minute: int = 10,
        tokens_per_minute: int = 20_000,
        cache_ttl_seconds: int = 3600,
        clock=time.monotonic,
    ):
        self._backend = backend or AnthropicBackend()
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._request_times: deque = deque()
        self._token_events: deque = deque()  # (timestamp, tokens)
        self._cache: dict = {}               # key -> (expires_at, text)
        self.log: list = []

    # ---- policies --------------------------------------------------------

    @staticmethod
    def _cache_key(transaction_id: str, reasons: list) -> str:
        return hashlib.sha256(json.dumps([transaction_id, sorted(reasons)]).encode()).hexdigest()

    def _prune(self, now: float) -> None:
        while self._request_times and now - self._request_times[0] >= WINDOW_SECONDS:
            self._request_times.popleft()
        while self._token_events and now - self._token_events[0][0] >= WINDOW_SECONDS:
            self._token_events.popleft()

    def _check_limits(self, now: float, estimated_input: int) -> None:
        self._prune(now)

        if len(self._request_times) >= self.requests_per_minute:
            raise RateLimitExceeded("requests-per-minute", self._request_times[0] + WINDOW_SECONDS - now)

        used = sum(tokens for _, tokens in self._token_events)
        if used + estimated_input > self.tokens_per_minute:
            retry_after = float(WINDOW_SECONDS)
            for ts, tokens in self._token_events:
                used -= tokens
                if used + estimated_input <= self.tokens_per_minute:
                    retry_after = ts + WINDOW_SECONDS - now
                    break
            raise RateLimitExceeded("tokens-per-minute", retry_after)

    # ---- logging / metering ---------------------------------------------

    def _record(self, status: str, transaction_id: str, **fields) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "transaction_id": transaction_id,
            "status": status,
            **fields,
        }
        self.log.append(entry)

        if status == "ok":
            detail = (
                f"in={entry['input_tokens']} out={entry['output_tokens']} "
                f"(est_in={entry['estimated_input_tokens']}) cost=${entry['cost_usd']:.4f}"
            )
        elif status == "cache_hit":
            detail = "served from cache, backend not called"
        elif status == "throttled":
            detail = f"429 {entry['limit']} limit, retry_after={entry['retry_after_seconds']:.0f}s"
        else:
            detail = "no API key configured, backend not called"
        print(f"[gateway] {status:<10} txn={transaction_id} {detail}")

    def usage_summary(self) -> dict:
        ok = [e for e in self.log if e["status"] == "ok"]
        return {
            "backend_calls": len(ok),
            "cache_hits": sum(e["status"] == "cache_hit" for e in self.log),
            "throttled": sum(e["status"] == "throttled" for e in self.log),
            "input_tokens": sum(e["input_tokens"] for e in ok),
            "output_tokens": sum(e["output_tokens"] for e in ok),
            "cost_usd": sum(e["cost_usd"] for e in ok),
        }

    # ---- entry point -----------------------------------------------------

    def invoke(self, **request) -> GatewayResult:
        """
        Run one request through the policy pipeline (see _invoke for the arguments),
        inside an "ai-gateway" Langfuse span carrying the gateway status as metadata.
        Throttling is an expected outcome, so it is re-raised after the span closes
        instead of being recorded as a span error.
        """
        result, throttled = None, None
        with tracing.observation("ai-gateway", metadata={"transaction_id": request["transaction_id"]}) as span:
            try:
                result = self._invoke(**request)
            except RateLimitExceeded as exc:
                throttled = exc
                span.update(level="WARNING", status_message=str(exc))
            entry = self.log[-1]
            span.update(
                metadata={"gateway_status": entry["status"], **{k: v for k, v in entry.items() if k != "status"}}
            )
        if throttled:
            raise throttled
        return result

    def _invoke(
        self, *, transaction_id: str, reasons: list, model: str, system: str, user_content: str, max_tokens: int
    ) -> GatewayResult:
        now = self._clock()

        key = self._cache_key(transaction_id, reasons)
        cached = self._cache.get(key)
        if cached and now < cached[0]:
            self._record("cache_hit", transaction_id)
            return GatewayResult(cached[1], "cache_hit")
        self._cache.pop(key, None)

        if not self._backend.available():
            self._record("no_api_key", transaction_id)
            return GatewayResult(None, "no_api_key")

        estimated_input = estimate_tokens(system + user_content)
        try:
            self._check_limits(now, estimated_input)
        except RateLimitExceeded as exc:
            self._record(
                "throttled", transaction_id, limit=exc.limit_name, retry_after_seconds=exc.retry_after
            )
            raise

        # Recorded here (not inside AnthropicBackend) so any backend is traced, e.g. a future APIM one.
        # Cost is our placeholder estimate, passed explicitly so Langfuse shows it even for models
        # missing from its price table.
        with tracing.observation(
            "claude-call",
            as_type="generation",
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            model_parameters={"max_tokens": max_tokens},
        ) as generation:
            result = self._backend(model=model, system=system, user_content=user_content, max_tokens=max_tokens)
            generation.update(
                output=result.text,
                usage_details={"input": result.input_tokens, "output": result.output_tokens},
                cost_details={
                    "input": estimate_cost(model, result.input_tokens, 0),
                    "output": estimate_cost(model, 0, result.output_tokens),
                    "total": estimate_cost(model, result.input_tokens, result.output_tokens),
                },
            )

        done = self._clock()
        self._request_times.append(done)
        self._token_events.append((done, result.input_tokens + result.output_tokens))
        self._cache[key] = (done + self.cache_ttl_seconds, result.text)
        self._record(
            "ok",
            transaction_id,
            estimated_input_tokens=estimated_input,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=estimate_cost(model, result.input_tokens, result.output_tokens),
        )
        return GatewayResult(result.text, "ok")


_default_gateway: Optional[AIGateway] = None


def get_gateway() -> AIGateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = AIGateway(
            requests_per_minute=int(os.environ.get("GATEWAY_RPM", 10)),
            tokens_per_minute=int(os.environ.get("GATEWAY_TPM", 20_000)),
        )
    return _default_gateway
