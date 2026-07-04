# backend/ai_provider.py
"""Provider abstraction: Claude-first with Ollama fallback.

Both providers MUST return a bare scenario dict (with a valid `id` field).
The caller is responsible for any save/persist logic.
"""
import logging

logger = logging.getLogger(__name__)


def generate_with_fallback(prompt: str, claude_function):
    """Try Claude first; on ANY failure fall back to the local Ollama agent.

    Both branches MUST return the same bare scenario-dict shape:
        {"id": ..., "name": ..., "request": {...}, ...}

    Args:
        prompt:          Plain-text user description / instruction.
        claude_function: Callable(prompt) -> bare scenario dict.
                         Should wrap _call_claude and normalize its output.

    Returns:
        A bare scenario dict.

    Raises:
        RuntimeError: If both Claude and Ollama fail.
    """
    try:
        logger.info("AI provider: trying Claude")
        return claude_function(prompt)
    except Exception as claude_error:
        logger.warning("Claude failed (%s); falling back to Ollama agent", claude_error)
        try:
            from backend.agent_service import execute_agent  # lazy import avoids cycles
            return execute_agent("scenario_generator", prompt)
        except Exception as ollama_error:
            raise RuntimeError(
                f"Both AI providers failed — Claude: {claude_error}; "
                f"Ollama: {ollama_error}"
            ) from ollama_error
