from backend.ollama_client import OllamaClient

def generate_with_fallback(prompt, claude_function):

    try:
        print("Trying Claude...")

        return claude_function(prompt)

    except Exception as claude_error:

        print(f"Claude failed: {claude_error}")

        try:

            print("Falling back to Ollama...")

            client = OllamaClient()

            return execute_agent(
                        "scenario_generator",
                        user_prompt
                    )

        except Exception as ollama_error:

            raise RuntimeError(
                f"Claude failed: {claude_error}. "
                f"Ollama failed: {ollama_error}"
            )