"""kw_ops, the test game environment's GM backend, as a tool server FarmBot grants to workers.

A skill's manifest grants it (`mcp`: `kw_ops` for every tool, `kw_ops:read` for the query tools). The host
profile says where it is and which environment variable holds its token. The token itself never leaves the
controller's environment: the worker's CLI reads it by name.
"""
from collections import namedtuple
import re

SERVER = "kw_ops"
GRANTS = {"kw_ops": "full", "kw_ops:read": "read"}
# The query tools a read grant exposes. A tool kw_ops adds later stays unavailable to read grants until listed.
READ_TOOLS = ("gm_list_targets", "gm_query_players", "gm_player_detail", "gm_guild_query", "gm_time_get",
              "gm_reward_types", "gm_reward_catalog")
_URL = re.compile(r"https?://[^\s/]+(/\S*)?")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")

# tools: the launch payload's tools.kw_ops entry, or None when the skill has no kw_ops grant.
# server: the MCP server entry to inject, or None. keep_env: the token variable the worker keeps, or None.
Resolution = namedtuple("Resolution", "tools server keep_env")


def validate_config(block):
    """The host profile's optional block: {} or exactly {"url": http(s) URL, "token_env": variable name}."""
    if block == {}:
        return
    if not isinstance(block, dict) or set(block) != {"url", "token_env"}:
        raise ValueError("kw_ops accepts exactly url and token_env")
    if not isinstance(block["url"], str) or not _URL.fullmatch(block["url"]):
        raise ValueError("kw_ops url must be an http or https URL")
    if not isinstance(block["token_env"], str) or not _ENV_NAME.fullmatch(block["token_env"]):
        raise ValueError("kw_ops token_env must be an environment variable name")


def access(grants):
    """The kw_ops access a skill manifest grants: "full", "read" or None."""
    levels = {GRANTS[grant] for grant in grants if grant in GRANTS}
    return "full" if "full" in levels else ("read" if levels else None)


def _unavailable(reason):
    return Resolution({"status": "unavailable", "reason": reason}, None, None)


def resolve(grants, config, runtime, environ):
    """One launch's kw_ops: what the worker is told, what is injected, and which variable it keeps."""
    level = access(grants)
    if level is None:
        return Resolution(None, None, None)
    if not config:
        return _unavailable("kw_ops is not configured on this host")
    if not environ.get(config["token_env"]):
        return _unavailable(f"{config['token_env']} is not set in the controller's environment")
    if runtime != "codex":
        # Since #39 fix runs only on Codex, and Claude cannot hold a read grant to the query tools.
        return _unavailable(f"kw_ops is not supported on the {runtime} runtime")
    server = {"url": config["url"], "bearer_token_env_var": config["token_env"]}
    if level == "read":
        server["enabled_tools"] = list(READ_TOOLS)
    return Resolution({"access": level}, server, config["token_env"])
