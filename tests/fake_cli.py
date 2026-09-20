"""Stand-in for codex exec / claude -p in tests. Reads the prompt from stdin like the real CLIs."""
import json
import os
import subprocess
import sys
import time

mode = os.environ.get("FAKE_CLI_MODE", "echo")
if mode == "exit-immediately":
    sys.exit(0)
prompt = sys.stdin.read()
payload = json.loads(prompt.split("\n\n", 1)[1]) if "\n\n" in prompt else {}
last_message = sys.argv[1] if len(sys.argv) > 1 else None


def write_last(text):
    if last_message:
        with open(last_message, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if mode == "echo":
    write_last(f"echo:{payload.get('item_id')}:home={os.environ.get('FAKE_HOME_MARKER', '')}"
               f":codex_home={os.environ.get('CODEX_HOME', '')}:claude_home={os.environ.get('CLAUDE_CONFIG_DIR', '')}")
elif mode == "sleep":
    time.sleep(60)
elif mode == "crash":
    sys.exit(3)
elif mode == "detached-sleep":
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    child.wait()
elif mode == "cli":
    db = payload["database"]
    item = payload["item_id"]
    token = None
    action_id = None
    steps = json.loads(os.environ["FAKE_CLI_STEPS"])
    if isinstance(steps, dict):
        # One script per item, plus a second script for the worker the pool resumes once it has been
        # granted the slot. The launch message is what says which of the two this run is — a resumed
        # worker is the one that carries a `resource` block — exactly as it is for a real worker, which
        # reads the same field to know whether it holds a reservation at all.
        steps = steps[(item + ":resumed") if payload.get("resource") else item]
    # The reservation token lives in a file the pool wrote; the launch message carries its path and never
    # the secret, so this is the only spelling a worker can use to give the slot back.
    token_file = (payload.get("resource") or {}).get("token_file") or ""
    for step in steps:
        args = [a.replace("{token}", token or "").replace("{item}", item).replace("{action_id}", action_id or "")
                .replace("{token_file}", token_file)
                for a in step]
        attempts = 60 if args[0] == "finish" else 1
        for attempt in range(attempts):
            out = subprocess.run([sys.executable, "-m", "agent", "--db", db, *args], capture_output=True, text=True,
                                 cwd=os.environ["FAKE_CLI_REPO"])
            if out.returncode == 0 or attempt == attempts - 1:
                break
            time.sleep(1)
        if out.returncode:
            write_last(f"cli-error:{out.stderr.strip()}")
            sys.exit(4)
        result = json.loads(out.stdout)
        if "token" in result:
            token = result["token"]
        if isinstance(result, dict) and result.get("action_id"):
            action_id = result["action_id"]
    write_last(f"cli-done:{item}")
