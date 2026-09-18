"""Render launchd agents so FarmBot and its tunnel start at login (spec §17 Phase 1).

Writing the property lists is all this module does. Loading them stays an explicit `launchctl`
command the operator reads and runs, because starting a webhook receiver is not a side effect.
"""
from pathlib import Path
import os
import plistlib
import shutil
import sys

from .config import Paths, ROOT

AGENTS = {"serve": "com.kuaiwa.farmbot.serve", "tunnel": "com.kuaiwa.farmbot.tunnel"}


def missing_tools(config, which=shutil.which):
    """The binaries the installed jobs will invoke and this host cannot resolve."""
    return [name for name in (config.runtime, "cloudflared") if which(name) is None]


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


def install(config, target_dir, *, python=sys.executable, cloudflared="cloudflared", repo_root=ROOT,
            path=None, config_path=None):
    # A launchd job inherits only /usr/bin:/bin:/usr/sbin:/sbin, which holds no codex, claude, gh or
    # cloudflared, so a worker spawned under it dies at Popen. Carry the installing shell's PATH instead.
    environment = {"PATH": path or os.environ.get("PATH", os.defpath), "HOME": str(Path.home())}
    log_dir = Paths(config).config_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    # An installed job cannot inherit the operator's --config, so name it explicitly or it reads another file.
    serve = [python, "-u", "-m", "agent.service", "serve"]
    if config_path:
        serve += ["--config", str(Path(config_path).expanduser().resolve())]
    jobs = {AGENTS["serve"]: serve, AGENTS["tunnel"]: tunnel_arguments(config, cloudflared)}
    written = {}
    for label, arguments in jobs.items():
        destination = target_dir / f"{label}.plist"
        destination.write_text(plist(label, arguments, repo_root, log_dir, environment), encoding="utf-8")
        written[label] = destination
    return written
