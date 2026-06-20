import json
import requests


class OllamaClient:

    def __init__(self):
        self.base_url = "http://ollama:11434/api/generate"
        self.model = "qwen3:8b"

    def generate(self, user_prompt):

        prompt = f"""
Return ONLY valid JSON.

Schema:

{{
  "id":"",
  "name":"",
  "description":"",
  "event_type":"authorization",
  "request":{{
    "transaction_id":"",
    "pan":"",
    "amount":0,
    "mcc":"",
    "merchant_name":"",
    "merchant_country":"",
    "pos_entry_mode":"",
    "terminal_id":""
  }},
  "expected_network_response_code":"00",
  "expected_customer_decision":"APPROVED"
}}

User Request:
{user_prompt}
"""

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }

        response = requests.post(
            self.base_url,
            json=payload,
            timeout=60
        )

        response.raise_for_status()

        result = response.json()

        return json.loads(
            result["response"]
        )