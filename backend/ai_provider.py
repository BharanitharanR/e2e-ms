import logging

logger = logging.getLogger(__name__)


def generate_with_fallback(prompt: str, provider_function):
    """
    Execute the selected provider.

    For now this project uses only Groq.
    """

    logger.info("AI Provider: Groq")

    return provider_function(prompt)