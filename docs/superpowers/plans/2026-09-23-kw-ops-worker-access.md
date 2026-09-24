# kw_ops Worker Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grant Codex fix workers every kw_ops tool and Codex chat workers its query tools. The token
never enters a file, a prompt or a worker's shell.

**Architecture:**
- **Grant model:** a new focused module, `agent/kw_ops.py`, holds the model: known grants, the read
  allowlist, host-config validation and per-launch resolution.
- **Where grants and settings come from:** skill manifests declare grants (`mcp`). The private host
  profile supplies the URL and the *name* of the token variable.
- **Wiring:**
  - `Scheduler.launch` injects the server and tells the worker what it got.
  - `Launcher.spawn` withholds the variable from workers without the server, and hides it from the shell
    of Codex workers with it.
  - `dispatch.py` states the authority.
  - `doctor` reports the configuration.

**Tech Stack:** Python 3.13 standard library, `unittest`, codex-cli 0.156.1 (measured behaviour below).

**Spec:** `docs/superpowers/specs/2026-09-23-kw-ops-worker-access-design.md`

> **Status (2026-09-24):** implemented. The shipped code differs from this plan where reviews ruled:
> URL validation uses `urllib.parse.urlsplit` (credentials, query and fragment rejected), `resolve`
> checks the runtime first, a blank token counts as unset, and the AUTHORITY and documentation
> wording changed. The code and the operating contract are authoritative.

## Global Constraints

**Scope and dependencies**
- Standard library and `unittest` only. Follow the existing module and test patterns.
- Codex runtime only. Every other runtime gets
  `{"status": "unavailable", "reason": "kw_ops is not supported on the <runtime> runtime"}`.

**The token and public text**
- FarmBot never writes the token value to any file, prompt, payload or log. Tests use dummy values
  such as `dummy-token-value` and assert that they are absent.
- The repository is public. Never commit a kw_ops host, URL, IP or real token. Tests and docs use
  `http://gm.test/mcp` or `http://<gm-host>/mcp`.

**Measured behaviour this plan relies on** (codex-cli 0.156.1, offline, 2026-09-23)
- `bearer_token_env_var` sends `Authorization: Bearer <value of that variable>`.
- `enabled_tools = [...]` limits the MCP tools that GPT-6 code mode exposes as
  `tools.mcp__kw_ops__<tool>`.
- `shell_environment_policy.exclude` hides a variable from shell commands only together with
  `features.shell_snapshot = false`. The shell snapshot re-exports it otherwise.

**How to work**
- Run commands from the repository root. The focused form is
  `python3 -m unittest discover -s tests -p '<file>' -v`; the full suite is
  `python3 -m unittest discover -s tests -v`.
- Work offline. Never touch production, the Windows host, live Linear, GitHub, or a real kw_ops.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: kw_ops grant model and host configuration

**Files:**
- Create: `agent/kw_ops.py`
- Modify: `agent/config.py`: import, the `Config` field, and `Config.__post_init__`
- Test: `tests/test_kw_ops.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `kw_ops.SERVER == "kw_ops"`
  - `kw_ops.GRANTS == {"kw_ops": "full", "kw_ops:read": "read"}`
  - `kw_ops.READ_TOOLS`, a tuple of 7 tool names
  - `kw_ops.validate_config(block) -> None`, which raises `ValueError`
  - `kw_ops.access(grants: iterable of str) -> "full" | "read" | None`
  - `kw_ops.Resolution(tools, server, keep_env)`, a namedtuple
  - `kw_ops.resolve(grants, config: dict, runtime: str, environ: mapping) -> Resolution`
  - `Config.kw_ops: dict`, which defaults to `{}`

- [ ] **Step 1: Write the failing tests** in `tests/test_kw_ops.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from agent import kw_ops
from agent.config import Config, load_config

URL = "http://gm.test/mcp"
CONFIG = {"url": URL, "token_env": "KW_OPS_TOKEN"}
ENV = {"KW_OPS_TOKEN": "dummy-token-value"}


class KwOpsConfigTests(unittest.TestCase):
    def test_no_block_means_not_configured(self):
        self.assertEqual(Config("c", "s", "w").kw_ops, {})

    def test_a_valid_block_is_accepted_and_loaded_from_the_private_profile(self):
        self.assertEqual(Config("c", "s", "w", kw_ops=dict(CONFIG)).kw_ops, CONFIG)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "kw_ops": CONFIG}), encoding="utf-8")
            self.assertEqual(load_config(path).kw_ops, CONFIG)

    def test_invalid_blocks_are_rejected(self):
        for block in ([], {"url": URL}, {**CONFIG, "token": "inline-secret"}, {**CONFIG, "url": "ftp://gm.test/mcp"},
                      {**CONFIG, "url": "gm.test/mcp"}, {**CONFIG, "token_env": "kw_ops_token"},
                      {**CONFIG, "token_env": "KW OPS"}, {**CONFIG, "token_env": ""}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                Config("c", "s", "w", kw_ops=block)


class KwOpsResolutionTests(unittest.TestCase):
    def test_manifest_grants_map_to_access_levels(self):
        self.assertIsNone(kw_ops.access(()))
        self.assertEqual(kw_ops.access(("kw_ops",)), "full")
        self.assertEqual(kw_ops.access(("kw_ops:read",)), "read")
        self.assertEqual(kw_ops.access(("kw_ops:read", "kw_ops")), "full")

    def test_a_full_codex_grant_names_the_token_variable_and_never_its_value(self):
        resolution = kw_ops.resolve(("kw_ops",), CONFIG, "codex", ENV)
        self.assertEqual(resolution.tools, {"access": "full"})
        self.assertEqual(resolution.server, {"url": URL, "bearer_token_env_var": "KW_OPS_TOKEN"})
        self.assertEqual(resolution.keep_env, "KW_OPS_TOKEN")
        self.assertNotIn("dummy-token-value", repr(resolution))

    def test_a_read_grant_enables_only_the_query_tools(self):
        self.assertEqual(kw_ops.READ_TOOLS, ("gm_list_targets", "gm_query_players", "gm_player_detail",
                                             "gm_guild_query", "gm_time_get", "gm_reward_types",
                                             "gm_reward_catalog"))
        resolution = kw_ops.resolve(("kw_ops:read",), CONFIG, "codex", ENV)
        self.assertEqual(resolution.tools, {"access": "read"})
        self.assertEqual(resolution.server["enabled_tools"], list(kw_ops.READ_TOOLS))

    def test_a_skill_without_a_grant_gets_nothing(self):
        self.assertEqual(kw_ops.resolve((), CONFIG, "codex", ENV), kw_ops.Resolution(None, None, None))

    def test_an_unavailable_grant_says_what_is_missing_and_injects_nothing(self):
        cases = {"not configured": ({}, "codex", ENV), "KW_OPS_TOKEN is not set": (CONFIG, "codex", {}),
                 "empty variable": (CONFIG, "codex", {"KW_OPS_TOKEN": ""}), "claude": (CONFIG, "claude", ENV)}
        for label, (config, runtime, environ) in cases.items():
            with self.subTest(label):
                resolution = kw_ops.resolve(("kw_ops",), config, runtime, environ)
                self.assertEqual(resolution.tools["status"], "unavailable")
                self.assertIsNone(resolution.server)
                self.assertIsNone(resolution.keep_env)
        self.assertIn("not configured", kw_ops.resolve(("kw_ops",), {}, "codex", ENV).tools["reason"])
        self.assertIn("KW_OPS_TOKEN", kw_ops.resolve(("kw_ops",), CONFIG, "codex", {}).tools["reason"])
        self.assertIn("claude", kw_ops.resolve(("kw_ops",), CONFIG, "claude", ENV).tools["reason"])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_kw_ops.py' -v`
Expected: `ImportError`, because `agent.kw_ops` does not exist yet.

- [ ] **Step 3: Implement `agent/kw_ops.py`**

```python
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
```

- [ ] **Step 4: Add the `Config` field and its validation** in `agent/config.py`.

Add the import after `from .linear_api import LinearAPI`:

```python
from .kw_ops import validate_config as validate_kw_ops_config
```

Add the field directly after `codex_workers: dict = field(default_factory=dict)`:

```python
    # {"url": ..., "token_env": NAME}; the token itself lives in the controller's environment, never here.
    kw_ops: dict = field(default_factory=dict)
```

Append as the last statement of `__post_init__`, after the `codex_workers` loop:

```python
        validate_kw_ops_config(self.kw_ops)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python3 -m unittest discover -s tests -p 'test_kw_ops.py' -v`
Expected: all pass.

Run: `python3 -m unittest discover -s tests -p 'test_worker_models.py' -v`
Expected: all pass. This checks that existing `Config` construction is unaffected.

- [ ] **Step 6: Commit**

```bash
git add agent/kw_ops.py agent/config.py tests/test_kw_ops.py
git commit -m "Add the kw_ops grant model and its host configuration" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Manifest grants for fix and chat

**Files:**
- Modify: `skills/fix/skill.json` (`"mcp"`), `skills/chat/skill.json` (`"mcp"`)
- Modify: `agent/skills.py`: an import, and `_load_one`'s validation
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `kw_ops.GRANTS` (Task 1).
- Produces:
  - `load_skills(...)["fix"].mcp == ("kw_ops",)`
  - `load_skills(...)["chat"].mcp == ("kw_ops:read",)`
  - an unknown grant raises `SkillError`.

- [ ] **Step 1: Write the failing tests** in `tests/test_skills.py`.

In `test_repository_skills_load_with_expected_authority`, append:

```python
        self.assertEqual(skills["fix"].mcp, ("kw_ops",))
        self.assertEqual(skills["chat"].mcp, ("kw_ops:read",))
```

Add a new test after `test_invalid_manifest_is_rejected`:

```python
    def test_an_unknown_tool_grant_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad"
            bad.mkdir()
            (bad / "SKILL.md").write_text("# bad", encoding="utf-8")
            (bad / "skill.json").write_text(json.dumps({"name": "bad", "trigger": ["mention"], "intents": [], "writes": [],
                                                        "resources": [], "gates": [], "mcp": ["kw_ops:write"],
                                                        "budget": {"lease_seconds": 1, "max_hours": 1, "renew_minutes": 1}}), encoding="utf-8")
            with self.assertRaisesRegex(SkillError, "unknown mcp grant"):
                load_skills(Path(tmp))
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_skills.py' -v`
Expected: two failures.
- `test_repository_skills_load_with_expected_authority`: `() != ('kw_ops',)`.
- `test_an_unknown_tool_grant_is_rejected`: `SkillError not raised`.

- [ ] **Step 3: Implement the change**

In `agent/skills.py`, add after `from pathlib import Path`:

```python
from .kw_ops import GRANTS
```

In `_load_one`, directly after the loop that checks the list-of-strings keys, add:

```python
    unknown = [grant for grant in manifest["mcp"] if grant not in GRANTS]
    if unknown:
        raise SkillError(f"{manifest_path}: unknown mcp grant {unknown[0]!r}")
```

In `skills/fix/skill.json`, replace `"mcp": []` with `"mcp": ["kw_ops"]`. In `skills/chat/skill.json`,
replace `"mcp": []` with `"mcp": ["kw_ops:read"]`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python3 -m unittest discover -s tests -p 'test_skills.py' -v`, then
`python3 -m unittest discover -s tests -p 'test_scheduler.py'`.
Expected: all pass. The scheduler does not read `mcp` until Task 4.

- [ ] **Step 5: Commit**

```bash
git add skills/fix/skill.json skills/chat/skill.json agent/skills.py tests/test_skills.py
git commit -m "Grant kw_ops to fix and its query tools to chat in the skill manifests" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Launcher withholds the token, and hides it from the Codex shell

**Files:**
- Modify: `agent/launcher.py`, in `Launcher.spawn`: the signature, the codex settings block, and the
  line that builds `env`
- Test: `tests/test_launcher.py`, in `LauncherTests`

**Interfaces:**
- Consumes: nothing new. Server entries come from callers.
- Produces:
  - `Launcher.spawn(..., model_settings=None, withheld_env=())`. `withheld_env` names variables
    removed from the worker's inherited environment.
  - A Codex home config whose injected servers name a `bearer_token_env_var` also carries
    `[shell_environment_policy] exclude = [those names]` and `features.shell_snapshot = false`.

- [ ] **Step 1: Write the failing tests** in `tests/test_launcher.py`.

Add the helper method to `LauncherTests`, after `wait_finished`:

```python
    def finished_by(self, launcher, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            finished = launcher.poll()
            if finished:
                return finished
            time.sleep(0.02)
        self.fail("worker did not finish")
```

Add the tests, after `test_home_config_strings_survive_any_path_character`:

```python
    def test_a_withheld_variable_never_reaches_the_worker(self):
        # Braces are doubled: spawn formats every command part.
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{k: os.environ.get(k) for k in "
                  "['KW_OPS_TOKEN', 'KEPT_MARKER']}}))")
        runtime = RUNTIMES["codex"]._replace(command=[sys.executable, "-c", script, "{last_message}"], seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value", "KEPT_MARKER": "kept"}):
            launcher.spawn("item-withheld", self.message, {}, 30, self.tmp.name, withheld_env=["KW_OPS_TOKEN"])
        self.addCleanup(launcher.stop, "item-withheld")
        finished = self.finished_by(launcher)
        self.assertEqual(json.loads(finished[0].last_message), {"KW_OPS_TOKEN": None, "KEPT_MARKER": "kept"})

    def test_a_codex_worker_with_a_token_server_hides_the_token_from_its_shell(self):
        import tomllib
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        server = {"url": "http://gm.test/mcp", "bearer_token_env_var": "KW_OPS_TOKEN"}
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            handle = launcher.spawn("item-secret", self.message, {"kw_ops": server}, 30, self.tmp.name,
                                    extra_env={"FAKE_CLI_MODE": "echo"})
        self.addCleanup(launcher.stop, "item-secret")
        self.finished_by(launcher)
        text = (handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8")
        config = tomllib.loads(text)
        self.assertEqual(config["mcp_servers"]["kw_ops"], server)
        self.assertEqual(config["shell_environment_policy"], {"exclude": ["KW_OPS_TOKEN"]})
        # codex-cli 0.156.1 re-exports an excluded variable from its shell snapshot unless the snapshot is off.
        self.assertIs(config["features"]["shell_snapshot"], False)
        self.assertNotIn("dummy-token-value", text)

    def test_a_codex_worker_without_a_token_server_keeps_its_shell_settings(self):
        import tomllib
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(self.runs, runtime, host="h")
        handle = launcher.spawn("item-plain", self.message, {"unity": {"url": "http://127.0.0.1:8080/mcp"}}, 30,
                                self.tmp.name, extra_env={"FAKE_CLI_MODE": "echo"})
        self.addCleanup(launcher.stop, "item-plain")
        self.finished_by(launcher)
        config = tomllib.loads((handle.run_dir / "home" / "config.toml").read_text(encoding="utf-8"))
        self.assertNotIn("shell_environment_policy", config)
        self.assertEqual(config["features"], {"memories": False})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_launcher.py' -k withheld -k token_server -v`
Expected:
- `test_a_withheld_variable_never_reaches_the_worker` errors with
  `TypeError: ... unexpected keyword argument 'withheld_env'`.
- The token-server test fails with `KeyError: 'shell_environment_policy'`.
- The third test passes already. It guards the unchanged path.

- [ ] **Step 3: Implement the change** in `agent/launcher.py`.

Change the `spawn` signature:

```python
    def spawn(self, item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=(), cancelled=None,
              model_settings=None, withheld_env=()):
```

Inside `if self.runtime.name == "codex":`, after the `for path in dict.fromkeys(...)` loop that writes
the `projects` trust entries, add:

```python
            # A server the CLI authenticates from an environment variable names it, never its value, and the
            # worker's shell must not see it either. codex-cli 0.156.1 re-exports variables from its shell
            # snapshot, so `exclude` holds only with the snapshot off (measured).
            secrets = sorted({server["bearer_token_env_var"] for server in mcp_servers.values()
                              if "bearer_token_env_var" in server})
            if secrets:
                settings["features"]["shell_snapshot"] = False
                settings["shell_environment_policy"] = {"exclude": secrets}
```

Replace the line that builds `env`:

```python
        env = {k: v for k, v in os.environ.items() if k not in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", *withheld_env)}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python3 -m unittest discover -s tests -p 'test_launcher.py' -v`
Expected: all pass, including the existing trust and encoder tests.

- [ ] **Step 5: Commit**

```bash
git add agent/launcher.py tests/test_launcher.py
git commit -m "Withhold worker secrets and hide token variables from the Codex shell" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Scheduler injection, launch payload and authority

**Files:**
- Modify: `agent/scheduler.py`: an import, `Scheduler.__init__`, and `Scheduler.launch`
- Modify: `agent/dispatch.py`: `AUTHORITY` and `dispatch_message`
- Modify: `agent/service.py`: the `Scheduler(...)` construction in `build`
- Test: `tests/test_scheduler.py`, `tests/test_dispatch.py`, `tests/test_service.py`

**Interfaces:**
- Consumes:
  - `kw_ops.resolve`, `kw_ops.SERVER` and `kw_ops.READ_TOOLS` (Task 1)
  - `Skill.mcp` (Task 2)
  - `Launcher.spawn(..., withheld_env=...)` (Task 3)
- Produces:
  - `Scheduler(..., kw_ops=None)`, stored as `Scheduler.kw_ops_config: dict`
  - `dispatch_message(..., tools=None)`, which puts `payload["tools"]` (always a dict) in the launch
    message
  - an AUTHORITY clause about `tools.kw_ops`

- [ ] **Step 1: Write the failing dispatch tests** in `tests/test_dispatch.py`, inside `DispatchTests`:

```python
    def dispatched(self, **extra):
        return dispatch_message(item={'id': 'i', 'skill': 'chat'}, issue={'identifier': 'FARM-1', 'url': 'u'},
                                skill_path=ROOT / 'skills/chat/SKILL.md', worktrees={}, db_path='/db',
                                runtime='codex', guidance='', budget={'lease_seconds': 1, 'renew_minutes': 1},
                                **extra)

    def test_tool_grants_reach_the_payload_and_the_authority_explains_kw_ops(self):
        message = self.dispatched(tools={'kw_ops': {'access': 'read'}})
        self.assertEqual(payload_of(message)['tools'], {'kw_ops': {'access': 'read'}})
        authority = message.split("\n\n", 1)[0]
        for phrase in ("tools.kw_ops.access", "test game environment", "gm_list_targets",
                       "never read, print or store", "State changes", "verification gap"):
            self.assertIn(phrase, authority)

    def test_a_launch_without_tool_grants_carries_an_empty_tools_map(self):
        self.assertEqual(payload_of(self.dispatched())['tools'], {})
```

- [ ] **Step 2: Write the failing scheduler tests** in `tests/test_scheduler.py`.

Change the import `from unittest.mock import Mock` to `from unittest.mock import Mock, patch`, and add
`from agent import kw_ops` after the other `agent` imports. Inside `SchedulerTests`, after
`test_a_codex_worker_home_distrusts_the_cwd_the_scheduler_chose`, add:

```python
    KW_OPS = {"url": "http://gm.test/mcp", "token_env": "KW_OPS_TOKEN"}

    def launched(self, launcher, item):
        """Launch through a real Launcher and return (home config, launch payload, handle)."""
        import tomllib
        handle = self.scheduler.launch(self.ledger.item(item["id"]))
        self.addCleanup(launcher.stop, item["id"])
        handle.process.wait(timeout=10)
        launcher.poll()
        home = handle.run_dir / "home"
        config = (tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
                  if (home / "config.toml").exists() else None)
        payload = json.loads((handle.run_dir / "prompt.md").read_text(encoding="utf-8").split("\n\n", 1)[1])
        return config, payload, handle

    def codex_with_kw_ops(self):
        runtime = RUNTIMES["codex"]._replace(command=RUNTIMES["fake"].command, seed_files={})
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "codex"
        self.scheduler.kw_ops_config = dict(self.KW_OPS)
        return launcher

    def test_a_codex_fix_worker_gets_every_kw_ops_tool_and_the_token_only_by_name(self):
        launcher = self.codex_with_kw_ops()
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            config, payload, handle = self.launched(launcher, self.item())
        self.assertEqual(config["mcp_servers"]["kw_ops"],
                         {"url": "http://gm.test/mcp", "bearer_token_env_var": "KW_OPS_TOKEN"})
        self.assertEqual(payload["tools"], {"kw_ops": {"access": "full"}})
        self.assertEqual(config["shell_environment_policy"], {"exclude": ["KW_OPS_TOKEN"]})
        for path in handle.run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn("dummy-token-value", path.read_text(encoding="utf-8", errors="replace"), path)

    def test_a_codex_chat_worker_gets_only_the_kw_ops_query_tools(self):
        launcher = self.codex_with_kw_ops()
        chat = self.item(issue_id=OTHER, session="chat-session", skill="chat")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            config, payload, _ = self.launched(launcher, chat)
        self.assertEqual(config["mcp_servers"]["kw_ops"]["enabled_tools"], list(kw_ops.READ_TOOLS))
        self.assertEqual(payload["tools"], {"kw_ops": {"access": "read"}})

    def test_without_the_token_variable_no_server_is_injected_and_the_worker_is_told_why(self):
        launcher = self.codex_with_kw_ops()
        with patch.dict(os.environ, {"KW_OPS_TOKEN": ""}):
            config, payload, _ = self.launched(launcher, self.item())
        self.assertNotIn("kw_ops", config.get("mcp_servers", {}))
        self.assertEqual(payload["tools"]["kw_ops"]["status"], "unavailable")
        self.assertIn("KW_OPS_TOKEN", payload["tools"]["kw_ops"]["reason"])

    def test_a_worker_denied_kw_ops_does_not_inherit_its_token(self):
        # Braces are doubled: spawn formats every command part.
        script = ("import json,os,pathlib,sys;sys.stdin.read();"
                  "pathlib.Path(sys.argv[1]).write_text(json.dumps({{'token': os.environ.get('KW_OPS_TOKEN')}}))")
        runtime = RUNTIMES["claude"]._replace(command=[sys.executable, "-c", script, "{last_message}"])
        launcher = Launcher(Path(self.tmp.name) / "real-runs", runtime, "h")
        self.scheduler.launcher = launcher
        self.scheduler.runtime_name = "claude"
        self.scheduler.kw_ops_config = dict(self.KW_OPS)
        chat = self.item(skill="chat")
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            _, payload, handle = self.launched(launcher, chat)
        self.assertEqual(json.loads((handle.run_dir / "last_message.txt").read_text(encoding="utf-8")),
                         {"token": None})
        self.assertIn("claude", payload["tools"]["kw_ops"]["reason"])
        servers = json.loads((handle.run_dir / "home" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertNotIn("kw_ops", servers)

    def test_an_unconfigured_host_injects_nothing_and_says_so(self):
        launcher = self.codex_with_kw_ops()
        self.scheduler.kw_ops_config = {}
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            config, payload, _ = self.launched(launcher, self.item())
        self.assertNotIn("kw_ops", config.get("mcp_servers", {}))
        self.assertIn("not configured", payload["tools"]["kw_ops"]["reason"])
```

- [ ] **Step 3: Write the failing service test** in `tests/test_service.py`, in the class holding
`test_build_speaks_as_the_configured_bot_and_production_keeps_farmbot`, directly after that test:

```python
    def test_build_hands_the_scheduler_the_kw_ops_block(self):
        block = {"url": "http://gm.test/mcp", "token_env": "KW_OPS_TOKEN"}
        config = Config(client_id="client", client_secret="s", webhook_secret="signing-secret", host="test",
                        runtime="fake", repos=self.c.config.repos, port=0,
                        local_root=Path(self.tmp.name) / "kw-ops", kw_ops=block)
        service = build(config)
        self.close_later(service)
        self.assertEqual(service.scheduler.kw_ops_config, block)
```

- [ ] **Step 4: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_dispatch.py' -v`
Expected: the two new tests error with `TypeError: ... unexpected keyword argument 'tools'` or
`KeyError: 'tools'`.

Run: `python3 -m unittest discover -s tests -p 'test_scheduler.py' -k kw_ops -k denied -k unconfigured -v`
Expected: `KeyError: 'kw_ops'` or `KeyError: 'tools'`.

Run: `python3 -m unittest discover -s tests -p 'test_service.py' -k kw_ops -v`
Expected: `AttributeError: ... 'kw_ops_config'`.

- [ ] **Step 5: Implement `dispatch.py`.**

In `AUTHORITY`, insert these literals immediately before the final
`"Use references/worker-cli.md for command arguments and the exact handoff JSON shape."`:

```python
    "When tools.kw_ops.access is present, the kw_ops MCP server is the GM backend of the test game "
    "environment, and every server gm_list_targets returns is a test server. With access \"full\" you may use "
    "any kw_ops tool on any listed server when this issue's reproduction or verification needs it; with "
    "\"read\" only its query tools exist. Record every state-changing kw_ops call, with server_id, tool, target "
    "and reason, as a handoff fact and under State changes in the run report. The kw_ops credential belongs "
    "to the host: never read, print or store it. kw_ops grants no other authority. When tools.kw_ops.status "
    "is \"unavailable\", report the missing tool as a verification gap. "
```

Change the `dispatch_message` signature to end with
`bot_name="FarmBot", write_repositories=(), prior_context=None, tools=None):`. In `payload`, add after
the `"resource": resource,` line:

```python
        # Standing tool grants beside the reservation: tools.kw_ops is {"access": ...} or {"status": "unavailable",
        # "reason": ...}. A token is never here; the worker's CLI reads it from the environment by name.
        "tools": tools or {},
```

- [ ] **Step 6: Implement `scheduler.py`.**

Add after `from .ledger import LedgerError`:

```python
from .kw_ops import SERVER as KW_OPS_SERVER, resolve as resolve_kw_ops
```

Add `kw_ops=None` as the last keyword parameter of `Scheduler.__init__`:
`..., issue_prefix='FARM', bot_name='FarmBot', kw_ops=None):`. In the body add
`self.kw_ops_config = dict(kw_ops or {})`.

In `launch`, directly after the `if reservation is not None and reservation["kind"] in skill.resources:`
block, and before `try: memory = publish_snapshot(...)`, add:

```python
        # A standing grant, unlike the Unity MCP: the manifest's `mcp` names it (spec §5) and the host profile
        # says where it is. The token stays in the controller's environment, and a worker without the server
        # keeps none of it.
        kw_ops_grant = resolve_kw_ops(skill.mcp, self.kw_ops_config, self.runtime_name, os.environ)
        if kw_ops_grant.server is not None:
            servers[KW_OPS_SERVER] = kw_ops_grant.server
        tools = {KW_OPS_SERVER: kw_ops_grant.tools} if kw_ops_grant.tools is not None else {}
```

Pass `tools=tools` to `dispatch_message(...)` as its last argument. After the
`if self.runtime_name == "codex": options["model_settings"] = ...` block, add:

```python
        if self.kw_ops_config and kw_ops_grant.keep_env is None:
            options["withheld_env"] = [self.kw_ops_config["token_env"]]
```

- [ ] **Step 7: Implement `service.py`.**

In `build`, add `kw_ops=config.kw_ops,` to the `Scheduler(...)` call, after `bot_name=config.expected_bot_name,`.

- [ ] **Step 8: Run the tests and confirm they pass**

Run each of:
- `python3 -m unittest discover -s tests -p 'test_dispatch.py' -v`
- `python3 -m unittest discover -s tests -p 'test_scheduler.py' -v`
- `python3 -m unittest discover -s tests -p 'test_service.py' -v`
- `python3 -m unittest discover -s tests -p 'test_worker_models.py' -v`

Expected: all pass. `FakeLauncher.spawn` never receives `withheld_env`, because the fixture configures no
kw_ops.

- [ ] **Step 9: Commit**

```bash
git add agent/scheduler.py agent/dispatch.py agent/service.py tests/test_scheduler.py tests/test_dispatch.py tests/test_service.py
git commit -m "Inject kw_ops into Codex fix and chat workers and state its authority" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Doctor reports the kw_ops configuration

**Files:**
- Modify: `agent/doctor.py`, in `diagnose`
- Test: `tests/test_doctor.py`, in `DoctorTests`

**Interfaces:**
- Consumes: `Config.kw_ops` (Task 1).
- Produces: `report["tools"]["kw_ops"]`. It is either
  `{"configured": False}` or
  `{"configured": True, "token_env": NAME, "token_set_in_doctor_environment": bool}`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_kw_ops_configuration_is_reported_without_its_token(self):
        self.config.kw_ops = {"url": "http://gm.test/mcp", "token_env": "KW_OPS_TOKEN"}
        with patch.dict(os.environ, {"KW_OPS_TOKEN": "dummy-token-value"}):
            report = self.report()
        self.assertEqual(report["tools"]["kw_ops"], {"configured": True, "token_env": "KW_OPS_TOKEN",
                                                     "token_set_in_doctor_environment": True})
        self.assertNotIn("dummy-token-value", json.dumps(report))
        with patch.dict(os.environ, {"KW_OPS_TOKEN": ""}):
            self.assertFalse(self.report()["tools"]["kw_ops"]["token_set_in_doctor_environment"])

    def test_an_unconfigured_kw_ops_is_reported_as_such(self):
        self.assertEqual(self.report()["tools"], {"kw_ops": {"configured": False}})
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_doctor.py' -k kw_ops -v`
Expected: `KeyError: 'tools'`.

- [ ] **Step 3: Implement the change.** In `diagnose`, directly after the
`report.update(host=config.host, ...)` statement, add:

```python
    # Doctor sees its own environment, not the running controller's, and never reports the token itself.
    kw_ops = config.kw_ops
    report["tools"] = {"kw_ops": ({"configured": True, "token_env": kw_ops["token_env"],
                                   "token_set_in_doctor_environment": bool(os.environ.get(kw_ops["token_env"]))}
                                  if kw_ops else {"configured": False})}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python3 -m unittest discover -s tests -p 'test_doctor.py' -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent/doctor.py tests/test_doctor.py
git commit -m "Report the kw_ops configuration in doctor without its token" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/operating-contract.md`: the Authority table, plus a paragraph after the Codex trust
  paragraph
- Modify: `README.md`: after the `codex_workers` paragraph that ends "number of configured Unity slots."
- Modify: `docs/development-workflow.md`: step 4, "Worker runtime"
- Add: this plan and the spec, `docs/superpowers/specs/2026-09-23-kw-ops-worker-access-design.md`

- [ ] **Step 1: Update the operating contract.**

In the Authority table:
- Set the chat row's Resources cell to `kw_ops query tools (Codex, when configured)`.
- Append `; kw_ops, every tool (Codex, when configured)` to the fix row's Resources cell.

After the paragraph that ends "Claude workers pin only their MCP servers (`--strict-mcp-config`).", add:

```markdown
kw_ops, the GM backend of the test game environment, is a standing tool grant declared in each
skill manifest's `mcp`. `kw_ops` gives fix workers every tool, and `kw_ops:read` gives chat workers
the query tools listed in `agent/kw_ops.py`. The private host profile's `kw_ops` block names the
URL and `token_env`, the environment variable that holds the token. The token stays in the
controller's environment, and a Codex worker's CLI reads it by name (`bearer_token_env_var`).
Such a worker lists the variable under `shell_environment_policy.exclude` and runs with
`features.shell_snapshot = false`, because codex-cli 0.156.1 re-exports excluded variables from
its shell snapshot. Every other worker has the variable removed from its environment. Claude
workers get no kw_ops.

When kw_ops is not configured, its variable is unset, or the runtime is unsupported,
`tools.kw_ops` in the launch payload says why and nothing is injected. An unreachable kw_ops
leaves the CLI running without it. Every server kw_ops lists belongs to the test environment,
so FarmBot does not scope servers. The dispatch AUTHORITY limits use to the issue's reproduction
and verification, and requires every state-changing call to be recorded. Read-only access rests
on FarmBot's allowlist, not on kw_ops.
```

- [ ] **Step 2: Update the README.** After the `codex_workers` paragraph, add:

````markdown
To give workers kw_ops, the test environment's GM backend, add its location to the private config
and put the token only in the controller's environment:

```json
"kw_ops": {"url": "http://<gm-host>/mcp", "token_env": "KW_OPS_TOKEN"}
```

`install-launchd` writes only `PATH` and `HOME` into its plists, so start a launchd-installed
controller through a wrapper that exports the variable. Codex fix workers then get every kw_ops
tool and chat workers its query tools; Claude workers get none. `doctor` shows whether kw_ops is
configured and whether the variable is set in doctor's own environment.
````

- [ ] **Step 3: Update the development workflow.** In step 4 ("Worker runtime"), append this bullet:

```markdown
   - kw_ops reaches Codex workers only. Export the profile's `token_env` variable in the wrapper
     that starts `serve`, never in the profile itself.
```

- [ ] **Step 4: Check the docs**

Run: `git diff --check`, then
`git diff origin/main | grep -n -E '^\+.*(([0-9]{1,3}\.){3}[0-9]{1,3}|Bearer [A-Za-z0-9._-]{12,})'`.
Expected: no whitespace errors, and no added line with an IP or a bearer token other than loopback
`127.0.0.1`.

- [ ] **Step 5: Commit**

```bash
git add docs/operating-contract.md README.md docs/development-workflow.md docs/superpowers/specs/2026-09-23-kw-ops-worker-access-design.md docs/superpowers/plans/2026-09-23-kw-ops-worker-access.md
git commit -m "Document kw_ops worker access" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Verification

**Files:** none committed. The throwaway harness lives outside the repository.

- [ ] **Step 1: Full offline suite**

Run: `rm -rf agent/__pycache__ tests/__pycache__ && python3 -m unittest discover -s tests -v`
Expected: 0 failures. Record the total and the skips; the skips are expected to be Windows-only.

- [ ] **Step 2: Offline real-CLI checks**

Build a scratch harness outside the repository with these parts:

- **Network block:** a Seatbelt profile that allows only loopback:

  ```
  (version 1)
  (allow default)
  (deny network-outbound (remote ip "*:*"))
  (deny network-outbound (remote unix-socket (path-literal "/private/var/run/mDNSResponder")))
  (allow network-outbound (remote ip "localhost:*"))
  ```

- **kw_ops stub:** a loopback streamable-HTTP MCP endpoint `/mcp`. It answers `initialize`,
  `tools/list` (with `gm_list_targets` and `gm_grant_reward`) and `tools/call`, and records every
  request's headers.
- **Model stub:** a loopback Responses endpoint `/v1/responses`. Its first response is a
  `custom_tool_call` named `exec` with this JavaScript:

  ```js
  const names = Object.keys(tools).filter(n => n.includes("kw_ops"));
  let called = "none";
  try { called = JSON.stringify(await tools.mcp__kw_ops__gm_list_targets({})); } catch (e) { called = "error:" + e; }
  text("KWTOOLS=" + JSON.stringify(names) + " CALLED=" + called);
  ```

  Its follow-up response is a final message. It detects the follow-up by an input item of type
  `custom_tool_call_output` or `function_call_output`, never by substring.
- **CLI wrapper:** a `codex` wrapper on `PATH` that execs the real CLI under the profile. It adds only
  `-c model_provider="mock"` and
  `-c 'model_providers.mock={ name = "mock", base_url = "http://127.0.0.1:<port>/v1", wire_api = "responses", request_max_retries = 0, stream_max_retries = 0, supports_websockets = false }'`.
- **Launch:** call `Scheduler.launch` or `Launcher.spawn` with the server from
  `kw_ops.resolve(("kw_ops",), {"url": "http://127.0.0.1:<port>/mcp", "token_env": "KW_OPS_TOKEN"}, "codex", env)`.
  Set `KW_OPS_TOKEN` to a random dummy value. Seed no `auth.json`. Use a fake `HOME`.

Then check:
1. The stub received `Authorization: Bearer <dummy>`.
2. A fix grant lists both tools after `KWTOOLS=`. A read grant lists only
   `mcp__kw_ops__gm_list_targets`. If the first model request carries a top-level `tools` array
   instead of an `additional_tools` item (fallback model metadata), check the kw_ops tool names in
   that array instead.
3. The dummy value is absent from every file under the attempt's run directory and from every
   captured model request.
4. The shell sees neither the token nor a snapshot copy of it. Copy the attempt's generated
   `home/config.toml` into a scratch `CODEX_HOME` and add the mock provider. Script the model stub's
   first response as an `exec_command` function call with `{"cmd": "env"}`. Run
   `codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --cd <cwd> -` under
   the profile. Bypass mode is used only so the command can run inside the outer Seatbelt profile;
   Codex's own Seatbelt cannot nest in it. Check that `env` output reached the follow-up request, that
   a control variable appears, and that the dummy token does not.
5. Record `codex --version`.

- [ ] **Step 3: Report**

Report:
- the suite result with its skips;
- each of the five checks, with the CLI version;
- that Windows is unverified;
- that the harness was not committed.
