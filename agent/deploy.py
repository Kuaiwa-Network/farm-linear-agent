"""Render launchd agents so FarmBot and its tunnel start at login (spec §17 Phase 1).

Writing the property lists is all this module does. Loading them stays an explicit `launchctl`
command the operator reads and runs, because starting a webhook receiver is not a side effect.
"""
from pathlib import Path
import os
import plistlib
import shutil
import sys

from .config import Paths, ROOT, monitor_settings

AGENTS = {"serve": "com.kuaiwa.farmbot.serve", "tunnel": "com.kuaiwa.farmbot.tunnel"}
# Written only when the host config has a monitor block, so it is not part of AGENTS, which every install writes.
MONITOR_AGENT = "com.kuaiwa.farmbot.monitor"
# launchd starts a KeepAlive job again at most once per ThrottleInterval, 10 s by default. A monitor that refuses its
# block or port exits at once, and each start appends a monitor_failed line to a log that is never rotated, so it is
# retried once a minute. serve and the tunnel keep launchd's default.
MONITOR_THROTTLE_INTERVAL = 60


def labels(config):
    """Keep legacy installations stable and separate explicitly named profiles."""
    if config.environment == "legacy":
        return {**AGENTS, "monitor": MONITOR_AGENT}
    prefix = f"com.kuaiwa.farmbot.{config.environment}.{config.instance_id}"
    return {kind: f"{prefix}.{kind}" for kind in (*AGENTS, "monitor")}


def missing_tools(config, which=shutil.which):
    """The binaries the installed jobs will invoke and this host cannot resolve."""
    return [name for name in (config.runtime, "cloudflared") if which(name) is None]


def plist(label, arguments, working_directory, log_dir, environment=None, throttle_interval=None):
    job = {
        "Label": label,
        "ProgramArguments": [str(part) for part in arguments],
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        # Standard, not Background. A launchd job's scheduling band is inherited by everything it spawns,
        # and Task 0 Step 6 measured the same EditMode suite at 105 s under Background against 14 s
        # foreground with warm caches — 7.5x, against a warm-cache control, so it is the band and not the
        # cache. A slot switch, an import and a test run each pay it.
        "ProcessType": "Standard",
        "StandardOutPath": str(Path(log_dir) / f"{label}.out.log"),
        "StandardErrorPath": str(Path(log_dir) / f"{label}.err.log"),
    }
    if environment:
        job["EnvironmentVariables"] = {str(key): str(value) for key, value in environment.items()}
    if throttle_interval is not None:
        job["ThrottleInterval"] = throttle_interval
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
    if config.monitor:
        # The monitor would refuse this block at every start; refuse it here, before anything is created.
        monitor_settings(config)
    # A launchd job inherits only /usr/bin:/bin:/usr/sbin:/sbin, which holds no codex, claude, gh or
    # cloudflared, so a worker spawned under it dies at Popen. Carry the installing shell's PATH instead.
    environment = {"PATH": path or os.environ.get("PATH", os.defpath), "HOME": str(Path.home())}
    log_dir = Paths(config).config_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    # An installed job cannot inherit the operator's --config, so name it explicitly or it reads another file.
    config_path = config_path or config.source_path
    selected = ["--config", str(Path(config_path).expanduser().resolve())] if config_path else []
    names = labels(config)
    # Each job's arguments, and its ThrottleInterval or None for launchd's default.
    jobs = {names["serve"]: ([python, "-u", "-m", "agent.service", "serve", *selected], None),
            names["tunnel"]: (tunnel_arguments(config, cloudflared), None)}
    if config.monitor:  # already accepted by monitor_settings at the top
        jobs[names["monitor"]] = ([python, "-u", "-m", "agent.service", "monitor", *selected],
                                  MONITOR_THROTTLE_INTERVAL)
    written = {}
    for label, (arguments, throttle_interval) in jobs.items():
        destination = target_dir / f"{label}.plist"
        destination.write_text(plist(label, arguments, repo_root, log_dir, environment, throttle_interval),
                               encoding="utf-8")
        written[label] = destination
    return written
