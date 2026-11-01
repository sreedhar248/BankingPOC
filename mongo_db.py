"""
Data layer for the transactions collection.

Set MONGODB_URI to use a real MongoDB/Atlas instance (add SEED_DEMO_DATA=true to
load the demo data into an empty collection); otherwise an in-memory
mongomock database is created and seeded. Both expose the same pymongo
Collection API, so nothing downstream changes when you swap.
"""

import os
from datetime import datetime, timedelta, timezone

DB_NAME = "banking_poc"
COLLECTION_NAME = "transactions"

# Fixed "now" so seeded history and test scenarios are deterministic.
REFERENCE_NOW = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)

# payer_id -> (payer_name, [(days_ago, amount, payee_id), ...]); days_ago may be fractional (hours / 24)
_H = 1 / 24
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
    # Four payments in the last 24h to known payees, normal amounts: velocity_burst only.
    "PAY-5001": ("Dana Okafor", [
        (42, 410.00, "PAYEE-GYM"), (35, 430.00, "PAYEE-GROCER"), (28, 400.00, "PAYEE-GYM"),
        (21, 450.00, "PAYEE-GROCER"), (14, 420.00, "PAYEE-GYM"),
        (20 * _H, 440.00, "PAYEE-GROCER"), (14 * _H, 460.00, "PAYEE-GYM"),
        (9 * _H, 430.00, "PAYEE-GROCER"), (4 * _H, 470.00, "PAYEE-GYM"),
    ]),
    # Business that normally pays ~$8k, then two $9.5k+ payments in 30h: structuring only.
    "PAY-6001": ("Lena Fischer", [
        (56, 7_800.00, "PAYEE-SUPPLIER"), (49, 8_200.00, "PAYEE-SUPPLIER"), (42, 8_600.00, "PAYEE-SUPPLIER"),
        (35, 8_100.00, "PAYEE-SUPPLIER"), (28, 8_400.00, "PAYEE-SUPPLIER"),
        (30 * _H, 9_500.00, "PAYEE-SUPPLIER"), (12 * _H, 9_700.00, "PAYEE-SUPPLIER"),
    ]),
    # Small, regular payments; the current one goes to a watchlisted payee.
    "PAY-7001": ("Tom Bradley", [
        (35, 180.00, "PAYEE-COFFEE"), (28, 220.00, "PAYEE-GROCER"), (21, 150.00, "PAYEE-COFFEE"),
        (14, 260.00, "PAYEE-GROCER"), (7, 200.00, "PAYEE-COFFEE"),
    ]),
    # ~$600 payer: 3 payments in the last 24h, then 2.5x the average to a first-time payee.
    "PAY-8001": ("Imran Qureshi", [
        (50, 560.00, "PAYEE-LANDLORD"), (43, 610.00, "PAYEE-GROCER"), (36, 590.00, "PAYEE-LANDLORD"),
        (29, 640.00, "PAYEE-GROCER"), (22, 575.00, "PAYEE-LANDLORD"), (15, 620.00, "PAYEE-GROCER"),
        (20 * _H, 580.00, "PAYEE-GROCER"), (10 * _H, 610.00, "PAYEE-LANDLORD"), (3 * _H, 640.00, "PAYEE-GROCER"),
    ]),
    # Ordinary first payment to a new payee at a normal amount: must stay clean.
    "PAY-9001": ("Aiko Tanaka", [
        (40, 95.00, "PAYEE-CAFE"), (33, 120.00, "PAYEE-GROCER"), (26, 110.00, "PAYEE-CAFE"),
        (19, 130.00, "PAYEE-GROCER"), (12, 100.00, "PAYEE-CAFE"),
    ]),
}

# payee_id -> reason. Only watchlisted payees have a record; any other payee is treated as not watchlisted.
_WATCHLIST = {
    "PAYEE-WATCHLISTED-SHELL": "beneficiary linked to previously reported shell-company activity",
}

# Transactions to be analysed (the "current" event), newest in the collection.
_CURRENT = [
    ("TXN-1001", "PAY-1001", 210.00, "PAYEE-GROCER"),
    ("TXN-2001", "PAY-2001", 61_000.00, "PAYEE-FLEET-LEASE"),
    ("TXN-3001", "PAY-3001", 9_800.00, "PAYEE-NEW-OFFSHORE"),
    ("TXN-4001", "PAY-4001", 5_000.00, "PAYEE-UNKNOWN"),
    ("TXN-5001", "PAY-5001", 450.00, "PAYEE-GROCER"),
    ("TXN-6001", "PAY-6001", 9_900.00, "PAYEE-SUPPLIER"),
    ("TXN-7001", "PAY-7001", 250.00, "PAYEE-WATCHLISTED-SHELL"),
    ("TXN-8001", "PAY-8001", 1_500.00, "PAYEE-NEW-CONTACT"),
    ("TXN-9001", "PAY-9001", 120.00, "PAYEE-NEW-BAKERY"),
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
    collection.database["payees"].insert_many(
        [{"payee_id": pid, "watchlisted": True, "reason": reason} for pid, reason in _WATCHLIST.items()]
    )
    collection.create_index("transaction_id", unique=True)
    collection.create_index([("payer_id", 1), ("timestamp", -1)])


def get_collection():
    uri = os.environ.get("MONGODB_URI")
    if uri:
        from pymongo import MongoClient
        collection = MongoClient(uri)[DB_NAME][COLLECTION_NAME]
        # Opt-in and only into an empty collection, so a real database is never seeded by accident.
        if os.environ.get("SEED_DEMO_DATA", "").lower() == "true" and collection.count_documents({}) == 0:
            seed(collection)
        return collection

    import mongomock
    collection = mongomock.MongoClient()[DB_NAME][COLLECTION_NAME]
    seed(collection)
    return collection
