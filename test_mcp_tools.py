import mongomock
import pytest

import mongo_db
from mcp_tools import MCPClient, MCPToolError, MongoTransactionsServer


@pytest.fixture
def mcp():
    collection = mongomock.MongoClient()["test"]["transactions"]
    mongo_db.seed(collection)
    return MCPClient(MongoTransactionsServer(collection))


def test_payee_profile_tool_is_listed():
    server = MongoTransactionsServer(mongomock.MongoClient()["t"]["transactions"])
    assert {t["name"] for t in server.list_tools()} == {"get_transaction", "get_payer_history", "get_payee_profile"}


def test_watchlisted_payee_profile(mcp):
    profile = mcp.call_tool("get_payee_profile", payee_id="PAYEE-WATCHLISTED-SHELL")
    assert profile["watchlisted"] is True and "shell" in profile["reason"]
    assert "_id" not in profile


def test_unknown_payee_is_simply_not_watchlisted(mcp):
    assert mcp.call_tool("get_payee_profile", payee_id="PAYEE-NEVER-SEEN") == {
        "payee_id": "PAYEE-NEVER-SEEN",
        "watchlisted": False,
    }


def test_unknown_transaction_raises_a_coded_error(mcp):
    with pytest.raises(MCPToolError) as excinfo:
        mcp.call_tool("get_transaction", transaction_id="NOPE")
    assert excinfo.value.code == "not_found" and excinfo.value.tool == "get_transaction"
