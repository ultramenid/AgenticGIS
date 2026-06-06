"""Per-provider hosted login / API-key pages.

The "Browser login" connection option opens one of these in the user's
default browser so they can either sign in (OAuth) or grab/create a key.
Add a provider's URL here whenever a new built-in is registered.
"""

LOGIN_URLS = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "groq": "https://console.groq.com/keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "gemini": "https://aistudio.google.com/apikey",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "mistral": "https://console.mistral.ai/api-keys",
    "xai": "https://console.x.ai/team/api-keys",
    "ollama": "",  # local; no hosted login
}


def login_url_for(provider_id):
    """Return the hosted login/key URL for a built-in provider, or ''."""
    return LOGIN_URLS.get(provider_id, "")
