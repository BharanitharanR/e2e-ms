import json
from pathlib import Path

SCENARIO_DIR = Path("backend/scenarios")


def read_scenarios():

    scenarios = []

    for file in SCENARIO_DIR.glob("*.json"):

        with open(file) as f:

            doc = json.load(f)

        doc["_file"] = file.name

        scenarios.append(doc)

    return scenarios


def find_scenario(scenario_id):

    for s in read_scenarios():

        if (
            s.get("id") == scenario_id
            or s.get("_file", "").rstrip(".json") == scenario_id
        ):
            return s

    return None