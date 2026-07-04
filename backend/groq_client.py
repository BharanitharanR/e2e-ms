import os

from groq import Groq

from backend.ai_config import get_api_key


class GroqClient:

    def __init__(self):

        #
        # Priority:
        #   1. Saved key from AI Settings
        #   2. Environment variable
        #

        api_key = get_api_key("groq")

        if not api_key:
            api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise RuntimeError(
                "Groq API key not configured. Configure it in AI Settings or set GROQ_API_KEY."
            )

        self.client = Groq(api_key=api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.2,
    ) -> str:

        messages = []

        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )

        return response.choices[0].message.content