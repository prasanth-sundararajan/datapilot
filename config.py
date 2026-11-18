"""Model client configuration for DataPilot.

Reads settings from .env (copy your reviewLens .env here, or start from .env.example).
Provider is picked from LLM_PROVIDER, or auto-detected from whichever API key is present.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Model capabilities we rely on. Passing this explicitly lets any model name work,
# including names newer than the installed autogen-ext release.
_MODEL_INFO = {
    "vision": False,
    "function_calling": True,
    "json_output": True,
    "structured_output": True,
    "family": "unknown",
    "multiple_system_messages": True,
}

_PROVIDERS = {
    # provider: (api-key env vars, default model, base_url)
    "openai": (["OPENAI_API_KEY"], "gpt-4.1", None),
    "anthropic": (["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"], "claude-sonnet-5-5", None),
    "groq": (["GROQ_API_KEY"], "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1"),
    "gemini": (["GEMINI_API_KEY", "GOOGLE_API_KEY"], "gemini-2.5-flash",
               "https://generativelanguage.googleapis.com/v1beta/openai/"),
    "openrouter": (["OPENROUTER_API_KEY"], "openai/gpt-4o-mini", "https://openrouter.ai/api/v1"),
    "ollama": ([], "llama3.1", "http://localhost:11434/v1"),
}


def _first_env(names: list[str]) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return None


def detect_provider() -> str | None:
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    for name, (keys, _, _) in _PROVIDERS.items():
        if keys and _first_env(keys):
            return name
    return None


def describe() -> str:
    p = detect_provider()
    if not p:
        return "no API key found"
    _, default_model, _ = _PROVIDERS.get(p, ([], "?", None))
    return f"{p} / {os.getenv('LLM_MODEL') or default_model}"


def make_model_client():
    """Return an AutoGen ChatCompletionClient for the configured provider."""
    provider = detect_provider()
    if provider is None:
        raise RuntimeError(
            "No LLM API key found. Put OPENAI_API_KEY / ANTHROPIC_API_KEY / GROQ_API_KEY / "
            "GEMINI_API_KEY in datapilot/.env (see .env.example)."
        )
    if provider not in _PROVIDERS:
        raise RuntimeError(f"Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(_PROVIDERS)}")

    keys, default_model, base_url = _PROVIDERS[provider]
    model = os.getenv("LLM_MODEL") or default_model
    api_key = _first_env(keys) if keys else "ollama"
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or base_url
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))

    if provider == "anthropic":
        from autogen_ext.models.anthropic import AnthropicChatCompletionClient

        return AnthropicChatCompletionClient(
            model=model, api_key=api_key, model_info=_MODEL_INFO, temperature=temperature, max_tokens=4096
        )

    from autogen_ext.models.openai import OpenAIChatCompletionClient

    kwargs = dict(model=model, api_key=api_key, model_info=_MODEL_INFO, temperature=temperature)
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIChatCompletionClient(**kwargs)
