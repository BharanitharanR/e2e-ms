import json
import requests
import uuid


class OllamaClient:

    def __init__(self):
        self.base_url = "http://ollama:11434/api/generate"
        self.model = "qwen3:8b"

    def generate(self, user_prompt):

        prompt = f"""
You generate payment simulator scenarios.

Return ONLY valid JSON.

No markdown.
No explanations.
No code blocks.

Required schema:

{{
  "id": "generated_id",
  "name": "Scenario Name",
  "description": "Description",
  "event_type": "authorization",
  "request": {{
    "transaction_id": "TXN001",
    "pan": "4111111111111111",
    "amount": 2500,
    "mcc": "5411",
    "merchant_name": "Merchant",
    "merchant_country": "USA",
    "pos_entry_mode": "051",
    "terminal_id": "TERM0001"
  }},
  "expected_network_response_code": "00",
  "expected_customer_decision": "APPROVED"
}}

User Request:
{user_prompt}
"""

        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False
        }

        response = requests.post(
            self.base_url,
            json=payload,
            timeout=120
        )

        response.raise_for_status()

        result = response.json()

        scenario = json.loads(
            result["response"]
        )

        if not scenario.get("id"):
            scenario["id"] = str(uuid.uuid4())

        return scenario