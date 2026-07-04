import os
from pymongo import MongoClient

from backend.scenario_loader import (
    read_scenarios,
    find_scenario
)


class _InMemoryCollection:
    def __init__(self):
        self._docs = {}

    def find(self, *_args, **_kwargs):
        return list(self._docs.values())

    def find_one(self, query, *_args, **_kwargs):
        # {} → return first document (used by get_template())
        if not query:
            return next(iter(self._docs.values()), None)
        # Match on either "id" or "_id" — seed files use "_id" as the key
        if "_id" in query:
            return self._docs.get(query["_id"])
        if "id" in query:
            return self._docs.get(query["id"])
        return None

    def replace_one(self, query, doc, upsert=False):
        key = query.get("_id") or query.get("id") or doc.get("_id") or doc.get("id")
        if key is not None and (upsert or key in self._docs):
            self._docs[key] = doc


def _mongo_or_fallback_collections():
    uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    db_name = os.getenv("MONGO_DB", "payment_simulator")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=1200, connectTimeoutMS=1200)
        client.admin.command("ping")
        db = client[db_name]
        return (
            db["scenarios"],
            db["scenario_templates"],
            db["agent_definitions"],
            db["prompt_templates"],
            db["guardrails"],
            db["scenario_templates"],
        )
    except Exception:
        scenarios_local = _InMemoryCollection()
        templates_local = _InMemoryCollection()
        agents_local = _InMemoryCollection()
        prompts_local = _InMemoryCollection()
        guardrails_local = _InMemoryCollection()
        return (
            scenarios_local,
            templates_local,
            agents_local,
            prompts_local,
            guardrails_local,
            templates_local,
        )


scenarios, scenario_templates, agents, prompts, guardrails, templates = _mongo_or_fallback_collections()

def get_scenarios():

    mongo_items = list(
        scenarios.find(
            {},
            {"_id": 0}
        )
    )

    if mongo_items:
        return mongo_items

    return read_scenarios()


def get_scenario_by_id(scenario_id):

    doc = scenarios.find_one(
        {"id": scenario_id},
        {"_id": 0}
    )

    if doc:
        return doc

    return find_scenario(scenario_id)

def save_scenario(doc):

    print("================================")
    print("SAVE_SCENARIO CALLED")
    print(type(doc))
    print(repr(doc))
    print("================================")
    if "id" not in doc:
        raise ValueError(
               f"Scenario must contain id. Actual document = {repr(doc)}"
        )

    scenarios.replace_one(
        {"id": doc["id"]},
        doc,
        upsert=True
    )

    return doc