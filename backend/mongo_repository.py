from pymongo import MongoClient

from backend.scenario_loader import (
    read_scenarios,
    find_scenario
)

client = MongoClient(
    "mongodb://mongodb:27017"
)

db = client["payment_simulator"]

scenarios = db["scenarios"]

scenario_templates = db["scenario_templates"]

agents = db["agent_definitions"]

prompts = db["prompt_templates"]

guardrails = db["guardrails"]

templates = db["scenario_templates"]

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