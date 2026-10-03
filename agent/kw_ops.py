"""kw_ops, the test game environment's GM backend, as a tool server FarmBot grants to workers.

A skill's manifest grants it (`mcp`: `kw_ops` for every tool, `kw_ops:read` for the query tools). The host
profile says where it is and which environment variable holds its token. The token itself never leaves the
controller's or an authorized Codex CLI's process environment: the CLI reads it by name.
"""
from collections import namedtuple
import ipaddress
import os
import re
from urllib.parse import urlsplit

SERVER = "kw_ops"
GRANTS = {"kw_ops": "full", "kw_ops:read": "read"}
# The query tools a read grant exposes. A tool kw_ops adds later stays unavailable to read grants until listed.
READ_TOOLS = ("gm_list_targets", "gm_query_players", "gm_player_detail", "gm_guild_query", "gm_time_get",
              "gm_reward_types", "gm_reward_catalog")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
# Launcher and Scheduler set these for each worker. None can also carry the kw_ops token.
_WORKER_ENV = frozenset({"CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                         "FARMBOT_ITEM_ID", "FARMBOT_DB", "FARMBOT_CONFIG", "PYTHONPATH"})

# tools: the launch payload's tools.kw_ops entry, or None when the skill has no kw_ops grant.
# server: the MCP server entry to inject, or None. keep_env: the token variable the worker keeps, or None.
Resolution = namedtuple("Resolution", "tools server keep_env")


def _is_http_url(url):
    """An HTTPS URL, or HTTP on loopback, without credentials or stray characters."""
    # Checked on the raw string: urlsplit silently drops tabs and newlines and strips leading spaces.
    if not isinstance(url, str) or not url.isprintable() or any(char.isspace() for char in url):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # an invalid port raises ValueError
    except ValueError:
        # Refused rather than chained: Python's own messages can repeat part of the value.
        return False
    if not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return False
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    if parts.hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        return False


def validate_config(block):
    """The host profile's optional block: {} or exactly {"url": secure URL, "token_env": name}."""
    if block == {}:
        return
    if not isinstance(block, dict) or set(block) != {"url", "token_env"}:
        raise ValueError("kw_ops accepts exactly url and token_env")
    if not _is_http_url(block["url"]):
        raise ValueError("kw_ops url must use https, or http on loopback")
    if not isinstance(block["token_env"], str) or not _ENV_NAME.fullmatch(block["token_env"]):
        raise ValueError("kw_ops token_env must be an environment variable name")
    if block["token_env"] in _WORKER_ENV:
        raise ValueError("kw_ops token_env conflicts with a worker environment variable")


# lark-cli reads credentials from these variables before any --profile (P13; lark-cli 1.0.82's environment provider
# comes first), so inherited credentials are always withheld. Launcher alone may then add explicitly configured
# bot credentials to a feature worker. Case-insensitive, as Windows environment names are.
LARK_CLI_CREDENTIALS = re.compile(r"LARKSUITE_CLI_(?:APP_ID|APP_SECRET|PROXY_KEY|\w*ACCESS_TOKEN)", re.IGNORECASE)


def child_environment(token_env, environ=None, *, secret_env=None):
    """The host environment for a child the controller starts outside a worker's sandbox (a Unity run, an Editor
    launch or diagnostic), without the configured kw_ops token, lark-cli credentials or its config-directory
    override (which would defeat the profile/home FarmBot selects), and the configured lark secret source.
    None, which inherits the
    environment as it is, when there is nothing to withhold."""
    source = os.environ if environ is None else environ
    # Windows environment names are case-insensitive, including when an explicit env dict is supplied.
    fold = str.upper if os.name == "nt" else str
    denied = {fold(name) for name in (token_env, secret_env) if name}
    kept = {name: value for name, value in source.items()
            if fold(name) not in denied and not LARK_CLI_CREDENTIALS.fullmatch(name)
            and name.upper() != "LARKSUITE_CLI_CONFIG_DIR"}
    return environ if len(kept) == len(source) else kept


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
    if runtime != "codex":
        # Since #39, fix runs only on Codex, and Claude cannot hold a read grant to the query tools.
        return _unavailable(f"kw_ops is not supported on the {runtime} runtime")
    if not config:
        return _unavailable("kw_ops is not configured on this host")
    if not environ.get(config["token_env"], "").strip():
        return _unavailable(f"{config['token_env']} is not set in the controller's environment")
    server = {"url": config["url"], "bearer_token_env_var": config["token_env"]}
    if level == "read":
        server["enabled_tools"] = list(READ_TOOLS)
    return Resolution({"access": level}, server, config["token_env"])
