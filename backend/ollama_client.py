import json
import requests


class OllamaClient:

    def __init__(self):

        self.base_url = (
            "http://ollama:11434/api/generate"
        )

    def generate(
        self,
        model,
        prompt
    ):

        payload = {
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False
        }

        response = requests.post(
            self.base_url,
            json=payload,
            timeout=1600
        )

        response.raise_for_status()

        result = response.json()

        return json.loads(
            result["response"]
        )