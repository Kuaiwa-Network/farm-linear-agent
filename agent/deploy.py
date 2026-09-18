"""Render launchd agents so FarmBot and its tunnel start at login (spec §17 Phase 1).

Writing the property lists is all this module does. Loading them stays an explicit `launchctl`
command the operator reads and runs, because starting a webhook receiver is not a side effect.
"""
from pathlib import Path
import plistlib
import sys

from .config import Paths, ROOT

AGENTS = {"serve": "com.kuaiwa.farmbot.serve", "tunnel": "com.kuaiwa.farmbot.tunnel"}


def plist(label, arguments, working_directory, log_dir, environment=None):
    job = {
        "Label": label,
        "ProgramArguments": [str(part) for part in arguments],
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(Path(log_dir) / f"{label}.out.log"),
        "StandardErrorPath": str(Path(log_dir) / f"{label}.err.log"),
    }
    if environment:
        job["EnvironmentVariables"] = {str(key): str(value) for key, value in environment.items()}
    return plistlib.dumps(job).decode("utf-8")


def tunnel_arguments(config, cloudflared="cloudflared"):
    """A quick tunnel by default; a named tunnel once `tunnel.name` is set in the host config."""
    name = (config.tunnel or {}).get("name")
    if name:
        return [cloudflared, "tunnel", "--no-autoupdate", "run", name]
    return [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{config.port}",
            "--no-autoupdate", "--protocol", "http2"]


def install(config, target_dir, *, python=sys.executable, cloudflared="cloudflared", repo_root=ROOT):
    log_dir = Paths(config).config_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    jobs = {
        AGENTS["serve"]: [python, "-u", "-m", "agent.service", "serve"],
        AGENTS["tunnel"]: tunnel_arguments(config, cloudflared),
    }
    written = {}
    for label, arguments in jobs.items():
        path = target_dir / f"{label}.plist"
        path.write_text(plist(label, arguments, repo_root, log_dir), encoding="utf-8")
        written[label] = path
    return written
