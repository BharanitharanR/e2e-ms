import json

from backend.agent_repository import (
    get_agent,
    get_prompt,
    get_template,
    get_guardrail
)

from backend.ollama_client import (
    OllamaClient
)

from backend.mongo_repository import (
    save_scenario
)


def build_prompt(
    agent_id,
    user_input
):

    agent = get_agent(
        agent_id
    )

    prompt_doc = get_prompt(
        agent["prompt_template_id"]
    )

    template = get_template()

    if template is None:

        raise Exception(
            "No scenario template found in MongoDB"
        )

    prompt = prompt_doc[
        "template"
    ]

    prompt = prompt.replace(
        "{{template}}",
        json.dumps(
            template,
            indent=2
        )
    )

    prompt = prompt.replace(
        "{{user_input}}",
        user_input
    )

    return (
        agent,
        prompt
    )

import time


def execute_agent(
    agent_id,
    user_input
):

    agent, prompt = build_prompt(
        agent_id,
        user_input
    )

    client = OllamaClient()

    generated = client.generate(
        model=agent["model"],
        prompt=prompt
    )

    print("GENERATED:")
    print(generated)

    if not generated.get("id"):

        generated["id"] = (
            f"gen_{int(time.time())}"
        )

    save_scenario(
        generated
    )

    return generated

def analyze_execution(
    scenario,
    execution_result
):

    agent = get_agent(
        "test_execution_agent"
    )

    prompt_doc = get_prompt(
        agent["prompt_template_id"]
    )

    prompt = prompt_doc["template"]

    prompt = prompt.replace(
        "{{scenario}}",
        json.dumps(
            scenario,
            indent=2
        )
    )

    prompt = prompt.replace(
        "{{execution_result}}",
        json.dumps(
            execution_result,
            indent=2
        )
    )

    client = OllamaClient()

    return client.generate(
        model=agent["model"],
        prompt=prompt
    )