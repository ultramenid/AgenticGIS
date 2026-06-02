"""Provider registry for the API-key connection method.

Built-in providers are pre-configured with their chat endpoint, wire format
(``anthropic`` or ``openai``), a sensible current default model, and the
customary environment variable for the key. The ``Custom`` pseudo-provider lets
the user set their own base URL and format.
"""

BUILT_INS = [
    {
        "id": "anthropic",
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "format": "anthropic",
        "default_model": "claude-opus-4-8",
        "key_env": "ANTHROPIC_API_KEY",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com",
        "format": "openai",
        "default_model": "gpt-4.1",
        "key_env": "OPENAI_API_KEY",
    },
    {
        "id": "groq",
        "label": "Groq",
        "base_url": "https://api.groq.com/openai",
        "format": "openai",
        "default_model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api",
        "format": "openai",
        "default_model": "anthropic/claude-sonnet-4",
        "key_env": "OPENROUTER_API_KEY",
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "format": "openai",
        "default_model": "gemini-2.0-flash",
        "key_env": "GOOGLE_API_KEY",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "format": "openai",
        "default_model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    {
        "id": "mistral",
        "label": "Mistral",
        "base_url": "https://api.mistral.ai",
        "format": "openai",
        "default_model": "mistral-large-latest",
        "key_env": "MISTRAL_API_KEY",
    },
    {
        "id": "xai",
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai",
        "format": "openai",
        "default_model": "grok-3-beta",
        "key_env": "XAI_API_KEY",
    },
    {
        "id": "ollama",
        "label": "Ollama (local)",
        "base_url": "http://localhost:11434",
        "format": "openai",
        "default_model": "llama3.1",
        "key_env": "",
    },
]

BY_ID = {p["id"]: p for p in BUILT_INS}


def get_provider(provider_id):
    """Return a provider dict, or None."""
    return BY_ID.get(provider_id)


def all_providers():
    """Return the built-in provider list (for UI dropdowns)."""
    return list(BUILT_INS)
