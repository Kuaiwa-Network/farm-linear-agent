"""Feature-only lark-cli credentials. Values travel only in a worker's process environment."""
import os

SKILLS = ("feature",)
APP_ID = "LARKSUITE_CLI_APP_ID"
APP_SECRET = "LARKSUITE_CLI_APP_SECRET"
STRICT_MODE = "LARKSUITE_CLI_STRICT_MODE"


def secret_value(block, environ):
    """Resolve the configured source, using Windows name semantics even for injected dictionaries.

    Ambiguous case variants fail closed. Neither errors nor tools metadata include the value or app ID.
    """
    name = block.get("secret_env")
    if not name:
        return None
    fold = str.upper if os.name == "nt" else str
    values = [value for key, value in environ.items() if fold(key) == fold(name)]
    return values[0] if len(values) == 1 and values[0].strip() else None


def tools(block, runtime, environ):
    """The public launch payload: profile metadata, an environment grant, or a sanitized gap."""
    if not block:
        return {"status": "unavailable", "reason": "lark_cli is not configured on this host"}
    if "secret_env" not in block:
        return dict(block)
    if runtime != "codex":
        return {"status": "unavailable", "reason": "lark_cli environment credentials require the codex runtime"}
    if secret_value(block, environ) is None:
        return {"status": "unavailable", "reason": "lark_cli secret is not set in the controller environment"}
    return {"authentication": "environment"}


def worker_credentials(block, runtime, environ):
    """Only Launcher, after withholding inherited credentials, may add these three variables."""
    if "secret_env" not in block or runtime != "codex":
        raise ValueError("lark_cli environment credentials require explicit configuration and the codex runtime")
    secret = secret_value(block, environ)
    if secret is None:
        raise ValueError("lark_cli secret is not set in the controller environment")
    return {APP_ID: block["app_id"], APP_SECRET: secret, STRICT_MODE: "bot"}
