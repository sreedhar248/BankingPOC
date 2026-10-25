"""
HTTP API tests using FastAPI's TestClient: no network, no real keys, no Claude calls.
Run: pytest test_api.py
"""

import mongomock
import pytest
from fastapi.testclient import TestClient

import mongo_db
from agent import build_graph
from ai_gateway import AIGateway
from api import create_app
from fakes import FakeBackend, FakeClock
from mcp_tools import MCPClient, MongoTransactionsServer

AUTH = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def api_keys(monkeypatch):
    monkeypatch.setenv("API_KEYS", "test-key, other-key")


@pytest.fixture
def collection():
    col = mongomock.MongoClient()["test"]["transactions"]
    mongo_db.seed(col)
    # A second anomalous transaction for PAY-3001 (same history as TXN-3001), for throttling tests.
    col.insert_one({
        "transaction_id": "TXN-3002", "payer_id": "PAY-3001", "payer_name": "Marcus Chen",
        "payee_id": "PAY-NEW-OFFSHORE-2", "amount": 12_000.0, "currency": "AUD", "channel": "NPP",
        "timestamp": mongo_db.REFERENCE_NOW,
    })
    return col


@pytest.fixture
def make_client(collection):
    def _make(backend=None, requests_per_minute=100, fallback_on_throttle=True):
        backend = backend or FakeBackend(available=False)  # default: no Claude key
        gateway = AIGateway(
            backend, requests_per_minute=requests_per_minute, tokens_per_minute=1_000_000, clock=FakeClock()
        )
        graph = build_graph(
            MCPClient(MongoTransactionsServer(collection)), gateway=gateway, fallback_on_throttle=fallback_on_throttle
        )
        return TestClient(create_app(graph)), backend

    return _make


def analyze(client, txn_id, headers=AUTH):
    return client.post("/analyze", json={"transaction_id": txn_id}, headers=headers)


# ---- health and auth ----------------------------------------------------------


def test_healthz_needs_no_key(make_client):
    client, _ = make_client()
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_key_is_401(make_client):
    client, _ = make_client()
    response = analyze(client, "TXN-3001", headers={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "ApiKey"


def test_wrong_key_is_401(make_client):
    client, _ = make_client()
    assert analyze(client, "TXN-3001", headers={"X-API-Key": "nope"}).status_code == 401


def test_any_configured_key_works(make_client):
    client, _ = make_client()
    assert analyze(client, "TXN-1001", headers={"X-API-Key": "other-key"}).status_code == 200


def test_fails_closed_when_no_keys_configured(make_client, monkeypatch):
    monkeypatch.delenv("API_KEYS")
    client, _ = make_client()
    assert analyze(client, "TXN-1001", headers={"X-API-Key": ""}).status_code == 401
    assert analyze(client, "TXN-1001").status_code == 401


# ---- verdicts -----------------------------------------------------------------


def test_clean_transaction(make_client):
    client, backend = make_client()
    body = analyze(client, "TXN-1001").json()
    assert body["is_anomalous"] is False
    assert body["reasons"] == ["consistent with payer history"]
    assert body["signals"]["avg"] == 141.94 and body["signals"]["history_count"] == 8
    assert body["investigation"] is None and body["alert_message"] is None and body["gateway_status"] is None
    assert backend.calls == 0, "Claude is never called for a clean transaction"


def test_insufficient_history_is_not_flagged(make_client):
    client, _ = make_client()
    body = analyze(client, "TXN-4001").json()
    assert body["is_anomalous"] is False and "insufficient history" in body["reasons"][0]


def test_anomalous_without_a_claude_key_uses_the_deterministic_fallback(make_client):
    client, backend = make_client()  # backend reports "no key"
    response = analyze(client, "TXN-3001")
    assert response.status_code == 200
    body = response.json()
    assert body["is_anomalous"] is True
    assert body["gateway_status"] == "no_api_key"
    assert body["investigation"] == "; ".join(body["reasons"])
    assert body["alert_message"].startswith("ALERT: transaction TXN-3001")
    assert body["signals"]["ratio"] == 10.61 and backend.calls == 0


def test_anomalous_with_claude(make_client):
    client, backend = make_client(FakeBackend())
    body = analyze(client, "TXN-3001").json()
    assert body["is_anomalous"] is True and body["gateway_status"] == "ok"
    assert body["investigation"] == "FAKE-LLM investigation" and body["alert_message"] == "FAKE-LLM alert"
    assert backend.calls == 1


def test_repeat_request_is_served_from_the_gateway_cache(make_client):
    client, backend = make_client(FakeBackend())
    analyze(client, "TXN-3001")
    assert analyze(client, "TXN-3001").json()["gateway_status"] == "cache_hit"
    assert backend.calls == 1


# ---- errors -------------------------------------------------------------------


def test_unknown_transaction_is_404(make_client):
    client, backend = make_client(FakeBackend())
    response = analyze(client, "TXN-DOES-NOT-EXIST")
    assert response.status_code == 404 and "TXN-DOES-NOT-EXIST" in response.json()["detail"]
    assert backend.calls == 0


@pytest.mark.parametrize("payload", [{}, {"transaction_id": ""}, {"transaction_id": 123}, {"transaction_id": "x" * 65}])
def test_bad_request_body_is_422(make_client, payload):
    client, _ = make_client()
    assert client.post("/analyze", json=payload, headers=AUTH).status_code == 422


def test_unauthenticated_request_is_rejected_before_validation_or_lookup(make_client):
    client, backend = make_client(FakeBackend())
    assert client.post("/analyze", json={}, headers={}).status_code == 401
    assert backend.calls == 0


# ---- throttling ---------------------------------------------------------------


def test_throttled_call_returns_the_fallback_result_by_default(make_client):
    client, _ = make_client(FakeBackend(), requests_per_minute=1)
    assert analyze(client, "TXN-3001").json()["gateway_status"] == "ok"

    response = analyze(client, "TXN-3002")
    assert response.status_code == 200
    body = response.json()
    assert body["is_anomalous"] is True and body["gateway_status"] == "throttled"
    assert body["investigation"] == "; ".join(body["reasons"])


def test_throttled_call_is_429_with_retry_after_when_fallback_is_disabled(make_client):
    client, _ = make_client(FakeBackend(), requests_per_minute=1, fallback_on_throttle=False)
    assert analyze(client, "TXN-3001").status_code == 200

    response = analyze(client, "TXN-3002")
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    assert "requests-per-minute" in response.json()["detail"]
