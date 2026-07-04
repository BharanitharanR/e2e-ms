# backend/agent_repository.py
"""Repository helpers for agent/prompt/guardrail/template documents.

Uses the collection objects already exported by mongo_repository.py (either
real MongoDB collections or the _InMemoryCollection fallback). Never imports
the non-existent `db` alias that previously caused an ImportError and silently
disabled every /ai/* endpoint via the try/except in main.py.
"""
from backend.mongo_repository import agents, prompts, guardrails, templates


def get_agent(agent_id: str) -> dict | None:
    """Return an agent definition by its _id, or None."""
    return agents.find_one({"_id": agent_id}, {"_id": 0})


def get_prompt(prompt_id: str) -> dict | None:
    """Return a prompt template document by its _id, or None."""
    return prompts.find_one({"_id": prompt_id}, {"_id": 0})


def get_guardrail(guardrail_id: str) -> dict | None:
    """Return a guardrail document by its _id, or None."""
    return guardrails.find_one({"_id": guardrail_id}, {"_id": 0})


def get_template() -> dict | None:
    """Return the first (default) scenario template document, or None."""
    return templates.find_one({}, {"_id": 0})
