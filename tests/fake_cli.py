"""Stand-in for codex exec / claude -p in tests. Reads the prompt from stdin like the real CLIs."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_CLI_MODE", "echo")
if mode == "exit-immediately":
    sys.exit(0)
# The launcher writes the prompt in UTF-8 and logs the worker's output to UTF-8 files, whatever the host's code
# page (Windows reads a pipe in the ANSI code page unless PYTHONUTF8 is set).
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
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
    fingerprint = None
    last = None
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

    def git(repo, *args):
        """Git in the worktree the launch message names for `repo`, as a worker runs it: FarmBot's commit identity
        and never a prompt. The worktree's own `origin` decides where a push goes."""
        out = subprocess.run(["git", "-c", "user.name=FarmBot", "-c", "user.email=farmbot@localhost", *args],
                             cwd=payload["worktrees"][repo], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if out.returncode:
            write_last(f"cli-error:git {args[0]} in {repo}: {out.stderr.strip()}")
            sys.exit(4)
        return out.stdout.strip()

    def expand(text):
        """A step's placeholders: {token}, {item}, {action_id}, {token_file} and {fingerprint} from earlier steps, and
        {rev:REPO:REF}, the commit REF names in that repository's worktree when the step runs."""
        text = (text.replace("{token}", token or "").replace("{item}", item).replace("{action_id}", action_id or "")
                .replace("{token_file}", token_file).replace("{fingerprint}", fingerprint or ""))
        return re.sub(r"\{rev:([^:{}]+):([^{}]+)\}", lambda m: git(m[1], "rev-parse", "--verify", m[2]), text)

    for step in steps:
        verb, args = step[0], [expand(a) for a in step[1:]]
        if verb == "git":  # ["git", REPO, ARG, ...]: git ARG... in that repository's worktree
            git(*args)
            continue
        if verb == "file":  # ["file", PATH, TEXT]: TEXT with its placeholders filled, written to PATH
            Path(args[0]).parent.mkdir(parents=True, exist_ok=True)
            Path(args[0]).write_text(args[1], encoding="utf-8")
            continue
        if verb == "wait":  # ["wait", PATH]: busy until the test creates PATH, as a worker in the middle of work
            deadline = time.time() + 120
            while not os.path.exists(args[0]):
                if time.time() > deadline:
                    write_last(f"cli-error:wait for {args[0]} timed out")
                    sys.exit(4)
                time.sleep(0.1)
            continue
        if verb == "expect":  # ["expect", KEY, JSON]: the last command's result has KEY equal to JSON, or the run ends
            actual = last.get(args[0]) if isinstance(last, dict) else None
            if actual != json.loads(args[1]):
                write_last(f"cli-error:expected {args[0]}={args[1]}, got {json.dumps(actual, ensure_ascii=False)}")
                sys.exit(4)
            continue
        if verb == "refused":  # ["refused", PATH, COMMAND, ARG, ...]: the command must be refused; its stderr to PATH
            out = subprocess.run([sys.executable, "-m", "agent", "--db", db, *args[1:]], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", cwd=os.environ["FAKE_CLI_REPO"],
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            if out.returncode == 0:
                write_last(f"cli-error:{args[1]} was not refused: {out.stdout.strip()}")
                sys.exit(4)
            Path(args[0]).write_text(out.stderr.strip(), encoding="utf-8")
            continue
        args = [verb, *args]
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
        if isinstance(result, dict) and result.get("fingerprint"):
            fingerprint = result["fingerprint"]
        last = result
    write_last(f"cli-done:{item}")
