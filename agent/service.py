"""Run FarmBot: receiver HTTP server plus scheduler loop in one process (spec §3)."""
from collections import namedtuple
import argparse
import json
import threading

from .config import Paths, configure, linear_api, load_config, ROOT
from .launcher import RUNTIMES, Launcher
from .ledger import Ledger
from .receiver import Receiver, make_server
from .scheduler import Scheduler
from .skills import load_skills
from .worktrees import Worktrees

Components = namedtuple("Components", "config paths api ledger skills worktrees launcher scheduler receiver server")


def build(config, runtime_override=None):
    paths = Paths(config)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    api = linear_api(config)
    identity = api.identity()
    skills = load_skills(ROOT / "skills")
    worktrees = Worktrees(paths.repos, paths.worktrees, config.repos)
    runtime = RUNTIMES[runtime_override or config.runtime]
    launcher = Launcher(paths.runs, runtime, config.host)
    ledger = Ledger(paths.ledger, check_same_thread=False)
    scheduler = Scheduler(ledger, launcher, skills, worktrees, skill_root=ROOT / "skills", db_path=paths.ledger,
                          runtime_name=runtime.name, host=config.host, max_concurrent=config.max_concurrent,
                          guidance_for=lambda item: (ledger.session(item["session_id"]) or {}).get("guidance") or "",
                          api=api)
    receiver = Receiver(paths.ledger, config.webhook_secret,
                        {"oauthClientId": config.client_id, "appUserId": identity["viewer"]["id"],
                         "organizationId": identity["organization"]["id"]},
                        api, lambda: Ledger(paths.ledger, check_same_thread=False), set(skills), scheduler)
    server = make_server(receiver, config.port)
    return Components(config, paths, api, ledger, skills, worktrees, launcher, scheduler, receiver, server)


def serve(config_path=None, components=None):
    components = build(load_config(config_path)) if components is None else components
    stop = threading.Event()

    def guarded(name, body):
        """One loop iteration never kills its thread: log the kind of failure and back off."""
        def loop():
            while not stop.is_set():
                try:
                    body()
                except Exception as exc:
                    print(json.dumps({"event": "loop_error", "loop": name, "error": type(exc).__name__}), flush=True)
                    stop.wait(1.0)
        return loop

    def receive_once():
        if not components.receiver.process_one():
            stop.wait(0.1)

    def schedule_once():
        components.scheduler.tick()
        stop.wait(1.0)

    threads = [threading.Thread(target=guarded("receive", receive_once), daemon=True),
               threading.Thread(target=guarded("schedule", schedule_once), daemon=True)]
    for thread in threads:
        thread.start()
    print(json.dumps({"event": "ready", "listen": f"http://127.0.0.1:{components.server.server_address[1]}",
                      "runtime": components.launcher.runtime.name, "host": components.config.host,
                      "skills": sorted(components.skills)}), flush=True)
    try:
        components.server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        components.server.server_close()
        stop.set()
        for thread in threads:
            thread.join(timeout=20)
        components.receiver.close()
        components.ledger.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m agent.service")
    parser.add_argument("command", choices=["configure", "serve", "status"])
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    if args.command == "configure":
        return configure(args.config)
    if args.command == "status":
        config = load_config(args.config)
        ledger = Ledger(Paths(config).ledger)
        try:
            print(json.dumps(ledger.status(), ensure_ascii=False, indent=2))
        finally:
            ledger.close()
        return 0
    serve(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
