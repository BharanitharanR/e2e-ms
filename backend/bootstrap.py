import json
from pathlib import Path

from backend.mongo_repository import (
    scenarios,
    scenario_templates,
    agents,
    prompts,
    guardrails
)


def _seed_directory(collection, directory):

    path = Path(directory)

    if not path.exists():

        print(
            f"Directory not found: {directory}"
        )

        return

    for file in path.glob("*.json"):

        print(
            f"Loading {file}"
        )

        with open(file) as f:

            doc = json.load(f)

        key = (
            "_id"
            if "_id" in doc
            else "id"
        )

        collection.replace_one(
            {key: doc[key]},
            doc,
            upsert=True
        )


def seed_scenarios():

    _seed_directory(
        scenarios,
        "backend/scenarios"
    )


def seed_templates():

    _seed_directory(
        scenario_templates,
        "backend/seed/templates"
    )


def seed_agents():

    _seed_directory(
        agents,
        "backend/seed/agents"
    )


def seed_prompts():

    _seed_directory(
        prompts,
        "backend/seed/prompts"
    )


def seed_guardrails():

    _seed_directory(
        guardrails,
        "backend/seed/guardrails"
    )


def bootstrap():

    print("Starting bootstrap")

    seed_scenarios()

    seed_templates()

    seed_agents()

    seed_prompts()

    seed_guardrails()

    print("Bootstrap complete")