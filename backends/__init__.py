"""Pluggable agent backends and the factory that builds the active one."""

from .base import AgentBackend, AgentEvent, EventType


def build_backend(config, toolkit, executor, server_provider=None):
    """Construct the backend selected in ``config``.

    ``server_provider`` is a zero-arg callable returning a running MCP server's
    base URL; only the CLI-tool backend needs it.
    """
    from .. import config as config_mod
    from . import providers

    mode = config.get("connection_mode")
    if mode in (config_mod.MODE_CLI_TOOL, config_mod.MODE_SUBSCRIPTION):
        from .cli_backend import CliToolBackend

        return CliToolBackend(config, server_provider)

    # API-key and custom endpoint modes are format-aware:
    # pick the Anthropic or OpenAI-compatible in-process loop.
    pid = config.get("provider")
    p = providers.get_provider(pid)
    if p:
        wire = p["format"]
    elif pid == "custom":
        wire = config.get("custom_format")
    else:
        # historical fallback: default to Anthropic
        from .api_backend import ApiBackend
        return ApiBackend(config, toolkit, executor)

    if wire == "anthropic":
        from .api_backend import ApiBackend
        return ApiBackend(config, toolkit, executor)
    # openai or anything else
    from .openai_backend import OpenAIBackend

    return OpenAIBackend(config, toolkit, executor)


__all__ = ["AgentBackend", "AgentEvent", "EventType", "build_backend"]
