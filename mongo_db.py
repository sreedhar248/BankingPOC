"""
Data layer for the transactions collection.

Set MONGODB_URI to use a real MongoDB/Atlas instance; otherwise an in-memory
mongomock database is created and seeded. Both expose the same pymongo
Collection API, so nothing downstream changes when you swap.
"""

import os
from datetime import datetime, timedelta, timezone

DB_NAME = "banking_poc"
COLLECTION_NAME = "transactions"

# Fixed "now" so seeded history and test scenarios are deterministic.
REFERENCE_NOW = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)

# payer_id -> (payer_name, [(days_ago, amount, payee_id), ...])
_PAYER_HISTORY = {
    "PAY-1001": ("Sarah Nguyen", [
        (58, 45.00, "PAYEE-COFFEE"), (51, 120.00, "PAYEE-GROCER"), (44, 310.00, "PAYEE-RENT-SHARE"),
        (37, 62.50, "PAYEE-GROCER"), (30, 185.00, "PAYEE-UTILITY"), (23, 95.00, "PAYEE-GROCER"),
        (16, 240.00, "PAYEE-RENT-SHARE"), (9, 78.00, "PAYEE-COFFEE"),
    ]),
    "PAY-2001": ("Harbour Logistics Pty Ltd", [
        (55, 52_000.00, "PAYEE-FUEL-CO"), (46, 61_500.00, "PAYEE-FLEET-LEASE"),
        (37, 48_750.00, "PAYEE-FUEL-CO"), (28, 67_200.00, "PAYEE-FLEET-LEASE"),
        (19, 55_400.00, "PAYEE-FUEL-CO"), (10, 58_900.00, "PAYEE-FLEET-LEASE"),
    ]),
    "PAY-3001": ("Marcus Chen", [
        (52, 650.00, "PAYEE-LANDLORD"), (43, 720.00, "PAYEE-LANDLORD"), (34, 880.00, "PAYEE-ELECTRICIAN"),
        (25, 940.00, "PAYEE-LANDLORD"), (16, 1_100.00, "PAYEE-LANDLORD"), (7, 1_250.00, "PAYEE-LANDLORD"),
    ]),
    "PAY-4001": ("Priya Raman", [
        (12, 300.00, "PAYEE-FRIEND"),
    ]),
}

# Transactions to be analysed (the "current" event), newest in the collection.
_CURRENT = [
    ("TXN-1001", "PAY-1001", 210.00, "PAYEE-GROCER"),
    ("TXN-2001", "PAY-2001", 61_000.00, "PAYEE-FLEET-LEASE"),
    ("TXN-3001", "PAY-3001", 9_800.00, "PAYEE-NEW-OFFSHORE"),
    ("TXN-4001", "PAY-4001", 5_000.00, "PAYEE-UNKNOWN"),
]


def seed(collection) -> None:
    docs = []
    n = 0
    for payer_id, (payer_name, rows) in _PAYER_HISTORY.items():
        for days_ago, amount, payee_id in rows:
            n += 1
            docs.append({
                "transaction_id": f"HIST-{n:04d}",
                "payer_id": payer_id,
                "payer_name": payer_name,
                "payee_id": payee_id,
                "amount": amount,
                "currency": "AUD",
                "channel": "NPP",
                "timestamp": REFERENCE_NOW - timedelta(days=days_ago),
            })
    names = {pid: name for pid, (name, _) in _PAYER_HISTORY.items()}
    for txn_id, payer_id, amount, payee_id in _CURRENT:
        docs.append({
            "transaction_id": txn_id,
            "payer_id": payer_id,
            "payer_name": names[payer_id],
            "payee_id": payee_id,
            "amount": amount,
            "currency": "AUD",
            "channel": "NPP",
            "timestamp": REFERENCE_NOW,
        })
    collection.insert_many(docs)
    collection.create_index("transaction_id", unique=True)
    collection.create_index([("payer_id", 1), ("timestamp", -1)])


def get_collection():
    uri = os.environ.get("MONGODB_URI")
    if uri:
        from pymongo import MongoClient
        return MongoClient(uri)[DB_NAME][COLLECTION_NAME]

    import mongomock
    collection = mongomock.MongoClient()[DB_NAME][COLLECTION_NAME]
    seed(collection)
    return collection
