"""Regression checks for API-key provider base URL overrides."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis import config as config_mod
from AgenticGis.backends.api_backend import ApiBackend
from AgenticGis.backends.openai_backend import OpenAIBackend


class _Config:
    def __init__(self, values):
        self.values = dict(config_mod.DEFAULTS)
        self.values.update(values)

    def get(self, name, default=None):
        if default is None:
            default = config_mod.DEFAULTS.get(name)
        return self.values.get(name, default)


def test_anthropic_provider_uses_api_base_url_override():
    cfg = _Config({
        "provider": "anthropic",
        "api_key": "key",
        "api_base_url": "https://anthropic-proxy.example.com/root/",
    })
    client = ApiBackend(cfg, None, None)._client()

    assert client.base_url == "https://anthropic-proxy.example.com/root"


def test_openai_provider_uses_api_base_url_override():
    cfg = _Config({
        "provider": "openai",
        "api_key": "key",
        "api_base_url": "https://openai-proxy.example.com/api/",
    })
    client = OpenAIBackend(cfg, None, None)._client()

    assert client.base_url == "https://openai-proxy.example.com/api"


def test_provider_default_used_when_api_base_url_empty():
    cfg = _Config({
        "provider": "openai",
        "api_key": "key",
        "api_base_url": "",
    })
    client = OpenAIBackend(cfg, None, None)._client()

    assert client.base_url == "https://api.openai.com"


def main():
    test_anthropic_provider_uses_api_base_url_override()
    test_openai_provider_uses_api_base_url_override()
    test_provider_default_used_when_api_base_url_empty()


if __name__ == "__main__":
    main()
