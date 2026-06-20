from pymongo import MongoClient
from datetime import datetime

client = MongoClient("mongodb://mongodb:27017")

db = client["payment_simulator"]

scenarios = db["scenarios"]


def save_scenario(document):

    document["created_at"] = datetime.utcnow()

    scenarios.replace_one(
        {"id": document["id"]},
        document,
        upsert=True
    )

    return document


def get_all_scenarios():

    return list(
        scenarios.find(
            {},
            {"_id": 0}
        )
    )