from backend.mongo_repository import db


def get_agent(agent_id):

    return db.agent_definitions.find_one(
        {"_id": agent_id},
        {"_id": 0}
    )


def get_prompt(prompt_id):

    return db.prompt_templates.find_one(
        {"_id": prompt_id},
        {"_id": 0}
    )


def get_guardrail(guardrail_id):

    return db.guardrails.find_one(
        {"_id": guardrail_id},
        {"_id": 0}
    )


def get_template():

    return db.scenario_templates.find_one(
        {},
        {"_id": 0}
    )