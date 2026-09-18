# FarmBot Phase 1c: hardening and Mac deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five gaps the 2026-09-18 live smoke test exposed and make FarmBot survive a reboot on the Mac, so it can be left running instead of started by hand for each test.

**Architecture:** No new subsystems. Four small changes inside the existing `agent/` package (a delivery that carries no code change, receiver input bounds and error handling, a clone-seeding command, writable roots for the Claude runtime) plus one new module that writes launchd property lists so `serve` and the tunnel start at login.

**Tech Stack:** Python 3.11+ standard library only, `unittest`, launchd (`launchctl bootstrap`), `cloudflared`, git.

**Spec:** `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` (§3, §6, §8, §9, §15, §17 Phase 1). Live evidence this plan answers: `docs/superpowers/spikes/2026-09-18-mac-live-smoke.md`. Executors read all three.

## Scope

In: the six open items from the live smoke test that do not need the Windows host or a purchased domain.

Out, and why:

- **Windows deployment, the supervisor script port, and retiring the FarmQA receiver and BugAgent heartbeat.** These need the Windows machine, which was not available. They become their own plan when it is. The operational checklist at the end of this document records what is owed there.
- **The named Cloudflare tunnel.** The user deferred buying a throwaway domain on 2026-09-18 (see `farmbot-webhook-endpoint-decision` and the spike record). Task 5 therefore supports both a quick tunnel, which is what testing uses today, and a named tunnel selected by one config key, so buying the domain later needs no code change.
- **Unity slots, the identity probe and two-phase acquisition.** Those are Plan 1b.

## Global Constraints

- Python 3.11+, standard library only in `agent/`; no third-party imports (spec §13).
- Tests use `unittest`, real temporary SQLite files and real subprocesses; the whole suite must stay green **and warning-free** under `python3 -W error -m unittest discover -s tests -v` from the repo root. It stands at 118 tests before this plan.
- Linear comment and activity language is zh-CN and concise (spec §9).
- Receiver acknowledges HTTP within 5 s and emits the first activity within 10 s (spec §3, §4).
- Workers receive no Linear MCP; all Linear I/O goes through the ledger CLI with the app token (spec §8).
- Private runtime state lives under `.local/` and is git-ignored; nothing secret enters the ledger, reports, plists or git (spec §15).
- Never merge, deploy, change issue status or assignee (spec §1 non-goals).
- Commit after every task with a conventional-commit message and a `Co-Authored-By: <model> <noreply@anthropic.com>` trailer naming the model that authored the commit, exactly as that model's own attribution reminder states. The trailer lines inside this plan's commit steps are templates for that line, not literal text.

---

## File structure

| Path | Responsibility | Change |
|---|---|---|
| `agent/ledger.py` | `finish` accepts a delivery whose evidence explains that no code change was needed | modify |
| `agent/__main__.py` | the completing session response distinguishes a no-change delivery | modify |
| `agent/receiver.py` | bound `guidance` like prompt text; treat a database failure as an uncertain event rather than a crashed loop | modify |
| `agent/worktrees.py` | `ensure_clone` may seed refs from a local checkout before its first fetch from origin | modify |
| `agent/launcher.py` | a runtime may express writable roots as command-line flags instead of a config file | modify |
| `agent/deploy.py` | **new**: render launchd property lists for `serve` and the tunnel, and write them | create |
| `agent/service.py` | `seed-clones` and `install-launchd` subcommands | modify |
| `skills/fix/SKILL.md` | the third outcome: delivered with no code change | modify |
| `docs/operating-contract.md` | no-change delivery, and the Mac deployment statement | modify |
| `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` | §6 amendment: a delivery may carry no PR when its evidence says why | modify |
| `README.md` | first-run and deployment steps | modify |
| `tests/test_deploy.py` | **new**: plist rendering and installation | create |
| `tests/test_ledger.py`, `tests/test_cli.py`, `tests/test_receiver.py`, `tests/test_worktrees.py`, `tests/test_launcher.py`, `tests/test_service.py` | one new behaviour each | modify |

---

### Task 1: A fix may deliver with nothing to change

**Why:** on 2026-09-18 a real delegation (FARM-1127) found the bug already fixed on main and the referenced PR closed. The correct result was a report and no PR, but `finish --outcome delivered` demands a nonempty `prs` array, so the worker recorded a correct conclusion as `blocked`. `blocked` means "stopped, a human is needed", which misreads the outcome in Linear and in `status`.

**Decision this task encodes:** the state set does not change (spec §6). What changes is that `delivered` may carry an empty `prs` array when the evidence includes a nonempty `no_change` string explaining why nothing needed changing. The item still requires a confirmed `delivery` comment, so the humans reading the issue always get the reasoning.

**Files:**
- Modify: `agent/ledger.py` (`finish`, the `outcome == "delivered"` branch)
- Modify: `agent/__main__.py` (`session_response`)
- Modify: `skills/fix/SKILL.md` (the `## Outcomes` section)
- Modify: `docs/operating-contract.md`, `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` (§6)
- Test: `tests/test_ledger.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Ledger.finish(item_id, token, outcome, evidence)` and `session_response(skill, outcome, evidence)` as they exist today.
- Produces: no signature change. `evidence` gains one optional key, `no_change`, a nonempty string. When it is present on a `delivered` finish, `prs` must be absent or empty.

- [ ] **Step 1: Write the failing tests**

In `tests/test_ledger.py`, inside `OutboxTests` (the class that already prepares and confirms comments):

```python
    def test_a_fix_may_deliver_with_no_code_change(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "主干已修复，无需改动。")
        self.ledger.confirm_comment(action["action_id"], "remote-1")
        view = self.ledger.finish(item["id"], token, "delivered",
                                  {"summary": "已确认主干修复", "comment_action_id": action["action_id"],
                                   "verification": "对比 common 主干与客户端已提交配置",
                                   "no_change": "主干提交 6bfe03e2 已修正该文案", "prs": []})
        self.assertEqual(view["state"], "delivered")

    def test_a_no_change_delivery_may_not_also_claim_a_pr(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "已修复。")
        self.ledger.confirm_comment(action["action_id"], "remote-2")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "delivered",
                               {"summary": "两者都有", "comment_action_id": action["action_id"],
                                "verification": "dotnet test", "no_change": "无需改动",
                                "prs": ["https://github.com/o/r/pull/3"]})

    def test_a_delivery_without_prs_or_a_no_change_reason_is_still_refused(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        action = self.ledger.prepare_comment(item["id"], token, "delivery", "已修复。")
        self.ledger.confirm_comment(action["action_id"], "remote-3")
        with self.assertRaises(LedgerError):
            self.ledger.finish(item["id"], token, "delivered",
                               {"summary": "空交付", "comment_action_id": action["action_id"],
                                "verification": "dotnet test", "prs": []})
```

In `tests/test_cli.py`:

```python
    def test_a_no_change_delivery_completes_the_session_as_no_change(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        body = self.root / "d.md"
        body.write_text("主干已修复。", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "delivery",
                              "--body-file", str(body))
        self.run_cli("post-comment", "--item", item, "--token", token, "--action-id", action["action_id"])
        self.run_cli("finish", "--item", item, "--token", token, "--outcome", "delivered", "--input",
                     self.json_file("nc.json", {"summary": "已确认主干修复", "comment_action_id": action["action_id"],
                                                "verification": "对比源表与已提交配置",
                                                "no_change": "主干提交已修正", "prs": []}))
        final = self.calls()[-1]
        self.assertEqual(final["content"]["type"], "response")
        self.assertIn("无需改动", final["content"]["body"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k no_change`
Expected: FAIL. The ledger tests raise `LedgerError: delivered finish requires a nonempty prs array`, and the CLI test fails its `无需改动` assertion.

- [ ] **Step 3: Implement the ledger rule**

In `agent/ledger.py`, replace the `if outcome == "delivered":` block inside `finish` with:

```python
                if outcome == "delivered":
                    _text(evidence.get("verification"), "verification")
                    prs = evidence.get("prs") or []
                    if not isinstance(prs, list):
                        raise LedgerError("prs must be an array")
                    no_change = evidence.get("no_change")
                    if no_change is not None:
                        # The investigation is the deliverable: already fixed, not reproducible, a duplicate.
                        _text(no_change, "no_change")
                        if prs:
                            raise LedgerError("a no_change delivery carries no PR")
                    elif not prs:
                        raise LedgerError("delivered finish requires a nonempty prs array or a no_change reason")
                    for pr in prs:
                        _text(pr, "PR URL")
                        if not self.PR_URL.fullmatch(pr):
                            raise LedgerError("each PR URL must be a canonical HTTPS pull request URL")
```

- [ ] **Step 4: Implement the session response**

In `agent/__main__.py`, in `session_response`, replace the `if outcome == "delivered":` branch with:

```python
    if outcome == "delivered":
        if evidence.get("no_change"):
            body = f"✅ 无需改动：{summary}"
        else:
            body = f"✅ 已交付：{summary}" + ("".join(f"\n- {url}" for url in prs))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k no_change`
Expected: PASS, four tests.

- [ ] **Step 6: Document the outcome**

In `skills/fix/SKILL.md`, add this bullet to `## Outcomes`, directly after the Delivered bullet:

```markdown
- Delivered with no code change: when the bug is already fixed on the target branch, is a duplicate of
  work already merged, or does not reproduce, that conclusion is the deliverable. Write the delivery
  body from the template with your evidence, `prepare-comment --kind delivery`, `post-comment`, then
  `finish --outcome delivered` with `{"summary", "comment_action_id", "verification", "no_change", "prs": []}`,
  where `no_change` states in one sentence why nothing needed changing, naming the commit or PR that
  already covers it. Never open an empty PR to satisfy the ledger, and never report this as blocked.
```

In `docs/operating-contract.md`, replace the line reading `- A fix that finds nothing to change (already fixed, duplicate) finishes blocked with the evidence; there is no separate no-change outcome yet.` with:

```markdown
- A fix that finds nothing to change (already fixed, duplicate, does not reproduce) delivers with an
  empty PR list and a `no_change` reason, and FarmBot's session response says 无需改动. Blocked stays
  for work that a human must unblock.
```

In the spec, in §6, append this sentence to the paragraph describing the `delivered` state:

```markdown
A delivery normally carries at least one draft PR. The exception is an investigation whose conclusion
is that nothing needs changing, which delivers with an empty PR list and a `no_change` reason in its
evidence; it still requires a confirmed delivery comment, so the reasoning always reaches the issue.
```

- [ ] **Step 7: Run the whole suite**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 122 tests, no warnings.

- [ ] **Step 8: Commit**

```bash
git add agent/ledger.py agent/__main__.py skills/fix/SKILL.md docs/operating-contract.md docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md tests/test_ledger.py tests/test_cli.py
git commit -m "feat(ledger): let a fix deliver when nothing needs changing

A live delegation found the bug already fixed on main and had to record that
correct conclusion as blocked, because a delivery demanded a PR. A delivery may
now carry an empty prs array when its evidence includes a no_change reason, and
the session response says 无需改动 instead of 已交付.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 2: The receiver bounds its input and survives a database failure

**Why:** the final review of Plan 1a parked two findings that the live run made concrete. `_prepare` bounds prompt text at 32,000 characters but lets `guidance` through unbounded into the worker's launch message. And `process_one` catches `LedgerError`, `RuntimeError`, `ValueError`, `KeyError` and `OSError`, but not `sqlite3.Error`; a database failure therefore escapes to the service loop guard, which logs `loop_error` once a second forever while the event stays `processing` and the session hears nothing.

**Files:**
- Modify: `agent/receiver.py` (`_prepare`, `process_one`)
- Test: `tests/test_receiver.py`

**Interfaces:**
- Consumes: `Receiver._prepare(event)` raising `ValueError` for input the receiver rejects with HTTP 400, and `Receiver.process_one()` returning `True` when it handled an event.
- Produces: no signature change.

- [ ] **Step 1: Write the failing tests**

In `tests/test_receiver.py`, inside `ReceiverTests`:

```python
    def test_oversized_guidance_is_rejected_like_oversized_prompt_text(self):
        event = self.event()
        event["guidance"] = "指" * 32001
        status, message = self.receiver.receive(*self.signed(event))
        self.assertEqual((status, message), (400, "invalid prompt"))
        self.assertEqual(self.receiver.results(), [])

    def test_a_database_failure_marks_the_event_uncertain_and_tells_the_session(self):
        import sqlite3
        self.receiver.ledger_factory = Mock(side_effect=sqlite3.OperationalError("no such column: guidance"))
        self.receiver.receive(*self.signed(self.event()))
        self.assertTrue(self.receiver.process_one())
        result = self.receiver.results()[-1]
        self.assertEqual((result["status"], result["error"]), ("uncertain", "OperationalError"))
        self.assertEqual(self.activities()[-1]["type"], "error")
```

If `ReceiverBase` has no `signed(event)` helper yet, add one beside `event()`; it returns the tuple `receive` takes:

```python
    def signed(self, event=None):
        body = json.dumps(event if event is not None else self.event()).encode()
        return body, hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()
```

and use it in place of any inline body-and-signature construction you are duplicating.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k "guidance or database_failure"`
Expected: FAIL. The first returns `(200, 'accepted')` because guidance is unbounded; the second raises `sqlite3.OperationalError` out of `process_one` instead of recording it.

- [ ] **Step 3: Bound the guidance**

In `agent/receiver.py`, in `_prepare`, replace the final two statements with:

```python
        guidance = event.get("guidance")
        guidance = guidance if isinstance(guidance, str) else json.dumps(guidance, ensure_ascii=False) if guidance else ""
        if len(guidance) > 32000:
            raise ValueError("oversized guidance")
        return {"action": event["action"], "session_id": session["id"], "issue_id": issue_id, "text": text,
                "guidance": guidance}
```

- [ ] **Step 4: Catch database failures**

In `agent/receiver.py`, `sqlite3` is already imported at the top. In `process_one`, widen the except clause:

```python
        except (LedgerError, RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k "guidance or database_failure"`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 124 tests.

- [ ] **Step 7: Commit**

```bash
git add agent/receiver.py tests/test_receiver.py
git commit -m "fix(receiver): bound guidance and record database failures

Guidance reached the worker's launch message unbounded while prompt text was
capped, and a sqlite3 error escaped process_one into the service loop guard,
which then logged once a second while the event stayed processing and the
session heard nothing.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 3: `seed-clones` prepares the bare clones from local checkouts

**Why:** on the first live run the scheduler had to create four bare clones inside a tick. Fetching Farm-Client from GitHub that way is slow enough to trip the claim timeout, so the operator seeded them by hand from the local checkouts, which took two seconds each. That step is currently tribal knowledge in a spike document.

**Files:**
- Modify: `agent/worktrees.py` (`ensure_clone`)
- Modify: `agent/service.py` (new `seed_clones` function and CLI subcommand)
- Test: `tests/test_worktrees.py`, `tests/test_service.py`

**Interfaces:**
- Consumes: `Worktrees(repos_root, worktrees_root, remotes)` and its `clone_path(repo)`.
- Produces:
  - `Worktrees.ensure_clone(repo, seed_from=None)`. When the clone does not exist and `seed_from` is a path to a git repository, its `refs/remotes/origin/*` are fetched into the new clone before the first fetch from `origin`. The return value is unchanged: the clone's `Path`.
  - `agent.service.seed_clones(config, source_root=None) -> dict` mapping each repo name to `"present"`, `"created"` or `"seeded from <path>"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_worktrees.py`, inside `WorktreeTests`:

```python
    def test_a_clone_can_be_seeded_from_a_local_checkout_before_its_first_origin_fetch(self):
        root = Path(self.tmp.name)
        checkout = root / "checkout"
        git("clone", "-q", str(self.origin), str(checkout), cwd=root)
        (self.origin / "later.txt").write_text("later", encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "after the checkout was made", cwd=self.origin)
        clone = self.trees.ensure_clone("Farm-Client", seed_from=checkout)
        refs = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin", cwd=clone)
        self.assertIn("origin/main", refs)
        # the origin fetch still runs, so the commit made after the checkout is present too
        self.assertEqual(git("rev-parse", "origin/main", cwd=clone), git("rev-parse", "HEAD", cwd=self.origin))

    def test_seeding_from_a_path_that_is_not_a_repository_is_ignored(self):
        missing = Path(self.tmp.name) / "nowhere"
        clone = self.trees.ensure_clone("Farm-Client", seed_from=missing)
        self.assertEqual(git("rev-parse", "--is-bare-repository", cwd=clone), "true")
```

In `tests/test_service.py`, add a new class at the end of the file. `ServeTests.setUp` builds the whole
`Components` graph, which this test does not need, so it gets its own fixture. The module already imports
`json`, `os`, `tempfile`, `time` and `Path`; add `subprocess` if it is not there, and note that the existing
module-level `git(...)` helper is the same one used below.

```python
class SeedCloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir(parents=True)
        git("init", "-q", "-b", "main", ".", cwd=self.origin)
        (self.origin / "README.md").write_text("hi\n", encoding="utf-8")
        git("add", ".", cwd=self.origin); git("commit", "-qm", "init", cwd=self.origin)
        self.config = Config(client_id="c", client_secret="s", webhook_secret="w",
                             repos={"Farm-Client": str(self.origin)}, local_root=self.root / "local")

    def test_seed_clones_reports_what_it_created_and_then_reuses_it(self):
        self.assertEqual(seed_clones(self.config), {"Farm-Client": "created"})
        self.assertTrue((self.root / "local" / "repos" / "Farm-Client.git").is_dir())
        self.assertEqual(seed_clones(self.config), {"Farm-Client": "present"})

    def test_seed_clones_reports_the_checkout_it_seeded_from(self):
        checkout = self.root / "sources" / "Farm-Client"
        checkout.parent.mkdir(parents=True)
        git("clone", "-q", str(self.origin), str(checkout), cwd=self.root)
        report = seed_clones(self.config, source_root=self.root / "sources")
        self.assertEqual(report, {"Farm-Client": f"seeded from {checkout}"})
```

Add `seed_clones` to the module's existing `from agent.service import ...` line and `Config` to its
`from agent.config import ...` line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k "seed"`
Expected: FAIL. `ensure_clone()` rejects the `seed_from` keyword, and `agent.service` has no `seed_clones`.

- [ ] **Step 3: Implement seeding in the worktrees**

In `agent/worktrees.py`, replace `ensure_clone` with:

```python
    def ensure_clone(self, repo, seed_from=None):
        path = self.clone_path(repo)
        if not path.exists():
            self.repos_root.mkdir(parents=True, exist_ok=True)
            _git("init", "--quiet", "--bare", str(path), cwd=self.repos_root)
            _git("remote", "add", "origin", self.remotes[repo], cwd=path)
            _git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", cwd=path)
            # Seeding from a local checkout of the same remote turns the first origin fetch into a
            # small delta; without it a first launch pays a full clone inside a scheduler tick.
            if seed_from is not None and Path(seed_from).exists():
                _git("fetch", "--quiet", str(seed_from), "+refs/remotes/origin/*:refs/remotes/origin/*", cwd=path)
            _git("fetch", "--quiet", "--prune", "origin", cwd=path)
        return path
```

- [ ] **Step 4: Implement the service function and its subcommand**

In `agent/service.py`, add after `build`:

```python
def seed_clones(config, source_root=None):
    """Create FarmBot's bare clones ahead of the first launch, seeding from local checkouts when given."""
    paths = Paths(config)
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    report = {}
    for repo in config.repos:
        seed = None
        if source_root:
            for name in (repo, f"farm-{repo}"):  # the common repo is checked out as farm-common
                candidate = Path(source_root) / name
                if (candidate / ".git").exists() or (candidate / "HEAD").exists():
                    seed = candidate
                    break
        if trees.clone_path(repo).exists():
            report[repo] = "present"
            continue
        trees.ensure_clone(repo, seed_from=seed)
        report[repo] = f"seeded from {seed}" if seed else "created"
    return report
```

Add `from pathlib import Path` to the imports if it is not there. Then in `main`, extend the command choices and handle it:

```python
    parser.add_argument("command", choices=["configure", "serve", "status", "seed-clones"])
    parser.add_argument("--config")
    parser.add_argument("--from", dest="source_root", help="directory holding local checkouts to seed from")
```

```python
    if args.command == "seed-clones":
        print(json.dumps(seed_clones(load_config(args.config), args.source_root), ensure_ascii=False, indent=2))
        return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k "seed"`
Expected: PASS, four tests.

- [ ] **Step 6: Document it**

In `README.md`, in the paragraph of first-run steps, replace the clause `seed the bare clones under .local/repos/<repo>.git from local checkouts so the first launch does not fetch gigabytes inside a scheduler tick` with:

```markdown
run `python3 -m agent.service seed-clones --from ~/WorkSpaces/Farm` so the bare clones exist before the
first launch instead of being fetched inside a scheduler tick
```

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 128 tests.

```bash
git add agent/worktrees.py agent/service.py README.md tests/test_worktrees.py tests/test_service.py
git commit -m "feat(service): add seed-clones so a first launch does not clone inside a tick

Creating the four bare clones from GitHub during a scheduler tick is slow enough
to risk the claim timeout. Seeding them from the operator's existing checkouts
takes seconds, and this command replaces the hand-run script the live test used.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 4: The Claude runtime gets the same writable roots as Codex

**Why:** the launcher grants a worker its worktrees, bare clones, ledger directory and state directory. For Codex that happens through `[sandbox_workspace_write] writable_roots` in the isolated home. The Claude runtime still passes only `--add-dir {cwd}`, so a Claude worker cannot write its other worktrees or its token file, and would fail exactly as the first Codex run did. Claude is the declared fallback runtime in spec §8, so it must reach parity before anyone switches to it.

**Files:**
- Modify: `agent/launcher.py` (`RuntimeConfig`, `RUNTIMES`, `spawn`)
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `Launcher.spawn(item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=())` and `Launcher.state_dir(item_id)` as they exist today.
- Produces: `RuntimeConfig` gains a final field `writable_flag`, defaulting to `None`. When a runtime sets it, `spawn` appends that flag followed by each writable root, in the same order as the Codex `writable_roots` list, to the formatted command. `RUNTIMES["claude"].writable_flag == "--add-dir"`; `codex` and `fake` leave it `None`.

- [ ] **Step 1: Write the failing test**

In `tests/test_launcher.py`, inside `LauncherTests`:

```python
    def test_a_runtime_with_a_writable_flag_gets_one_flag_per_root(self):
        runtime = RUNTIMES["claude"]._replace(command=RUNTIMES["fake"].command)
        launcher = Launcher(self.runs, runtime, host="h")
        handle = launcher.spawn("item-4", self.message, {}, budget_seconds=60, cwd=self.tmp.name,
                                extra_env={"FAKE_CLI_MODE": "echo"},
                                writable=[Path("/w/item-4/Farm-Client"), Path("/repo/.local/agent")])
        deadline = time.time() + 10
        while time.time() < deadline and not launcher.poll():
            time.sleep(0.05)
        argv = handle.process.args
        self.assertEqual(argv[-6:], ["--add-dir", str(self.runs / "item-4"),
                                     "--add-dir", "/w/item-4/Farm-Client",
                                     "--add-dir", "/repo/.local/agent"])
        self.assertEqual(RUNTIMES["claude"].writable_flag, "--add-dir")
        self.assertIsNone(RUNTIMES["codex"].writable_flag)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -W error -m unittest discover -s tests -v -k writable_flag`
Expected: FAIL with `AttributeError: 'RuntimeConfig' object has no attribute 'writable_flag'`.

- [ ] **Step 3: Implement it**

In `agent/launcher.py`, give the namedtuple the new field with a default, and set it on the Claude runtime:

```python
RuntimeConfig = namedtuple("RuntimeConfig", "name command home_env mcp_format seed_files writable_flag",
                          defaults=(None,))
```

```python
    "claude": RuntimeConfig(
        name="claude",
        command=["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
                 "--mcp-config", "{mcp_config}", "--strict-mcp-config", "--add-dir", "{cwd}"],
        home_env="CLAUDE_CONFIG_DIR", mcp_format="json", seed_files={}, writable_flag="--add-dir"),
```

In `spawn`, directly after the line that formats `command`, append the roots:

```python
        if self.runtime.writable_flag:
            # Codex takes its roots from the isolated home's config; Claude takes them on the command line.
            for root in roots:
                command += [self.runtime.writable_flag, root]
```

`roots` is already computed several lines above, as `[str(self.state_dir(item_id)), *(str(path) for path in writable)]`, and the `command` assignment already follows it, so no reordering is needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -W error -m unittest discover -s tests -v -k writable_flag`
Expected: PASS.

- [ ] **Step 5: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 129 tests.

```bash
git add agent/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): give the Claude runtime the same writable roots as Codex

A Claude worker received only --add-dir for its working directory, so it could
not write its other worktrees, its bare clones or its token file. A runtime may
now declare that its writable roots go on the command line.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 5: FarmBot survives a reboot on the Mac

**Why:** the service and the tunnel currently live only as long as an operator's terminal session. After the live test both had to be stopped by hand, and FarmBot went dark. launchd should own them.

**Design:** a new module renders property lists and writes them into `~/Library/LaunchAgents`; it never runs `launchctl` itself, so installation stays a command the operator reads and runs. Two agents: `com.kuaiwa.farmbot.serve` runs `python3 -m agent.service serve`, and `com.kuaiwa.farmbot.tunnel` runs `cloudflared`. The tunnel agent reads the optional `tunnel` key of the host config: absent or `{"quick": true}` gives today's quick tunnel, and `{"name": "farmbot"}` runs the named tunnel that a purchased domain will enable later, with no code change.

**Files:**
- Create: `agent/deploy.py`
- Modify: `agent/config.py` (`Config` gains a `tunnel` field), `agent/service.py` (`install-launchd` subcommand), `README.md`, `docs/operating-contract.md`
- Test: `tests/test_deploy.py`

**Interfaces:**
- Consumes: `Config` and `Paths` from `agent.config`.
- Produces:
  - `agent.deploy.AGENTS` mapping `"serve"` and `"tunnel"` to their launchd labels, `com.kuaiwa.farmbot.serve` and `com.kuaiwa.farmbot.tunnel`.
  - `agent.deploy.plist(label, arguments, working_directory, log_dir, environment=None) -> str`, a complete XML property list with `RunAtLoad` and `KeepAlive` true, `StandardOutPath` and `StandardErrorPath` under `log_dir`.
  - `agent.deploy.tunnel_arguments(config, cloudflared="cloudflared") -> list[str]`.
  - `agent.deploy.install(config, target_dir, *, python=sys.executable, cloudflared="cloudflared", repo_root=ROOT) -> dict` mapping each label to the `Path` it wrote, creating `target_dir` and the log directory if needed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deploy.py`:

```python
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from agent.config import Config
from agent.deploy import AGENTS, install, plist, tunnel_arguments

ROOT = Path(__file__).resolve().parents[1]


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Config(client_id="c", client_secret="s", webhook_secret="w",
                             local_root=self.root / "local", port=8765)

    def test_a_rendered_plist_parses_and_keeps_the_job_alive(self):
        text = plist("com.example.job", ["/bin/echo", "hi"], self.root, self.root / "logs")
        parsed = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(parsed["Label"], "com.example.job")
        self.assertEqual(parsed["ProgramArguments"], ["/bin/echo", "hi"])
        self.assertTrue(parsed["RunAtLoad"] and parsed["KeepAlive"])
        self.assertEqual(parsed["WorkingDirectory"], str(self.root))
        self.assertTrue(parsed["StandardErrorPath"].endswith("com.example.job.err.log"))

    def test_a_quick_tunnel_is_the_default_and_a_named_tunnel_is_one_config_key(self):
        self.assertEqual(tunnel_arguments(self.config, cloudflared="/usr/bin/cloudflared")[:4],
                         ["/usr/bin/cloudflared", "tunnel", "--url", "http://127.0.0.1:8765"])
        named = Config(client_id="c", client_secret="s", webhook_secret="w",
                       local_root=self.root / "local", tunnel={"name": "farmbot"})
        self.assertEqual(tunnel_arguments(named, cloudflared="/usr/bin/cloudflared"),
                         ["/usr/bin/cloudflared", "tunnel", "--no-autoupdate", "run", "farmbot"])

    def test_install_writes_both_agents_and_never_runs_launchctl(self):
        target = self.root / "LaunchAgents"
        written = install(self.config, target, python="/usr/bin/python3", cloudflared="/usr/bin/cloudflared")
        self.assertEqual(sorted(written), sorted(AGENTS.values()))
        serve = plistlib.loads((target / f"{AGENTS['serve']}.plist").read_bytes())
        self.assertEqual(serve["ProgramArguments"], ["/usr/bin/python3", "-u", "-m", "agent.service", "serve"])
        self.assertEqual(serve["WorkingDirectory"], str(ROOT))
        self.assertTrue((self.config.local_root / "agent" / "logs").is_dir())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k Deploy`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.deploy'`.

- [ ] **Step 3: Add the config field**

In `agent/config.py`, add one field to `Config`, next to `slots`:

```python
    tunnel: dict = field(default_factory=dict)
```

- [ ] **Step 4: Write the module**

Create `agent/deploy.py`:

```python
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
        job["EnvironmentVariables"] = {str(k): str(v) for k, v in environment.items()}
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
```

- [ ] **Step 5: Add the subcommand**

In `agent/service.py`, import the module and extend `main`:

```python
from .deploy import AGENTS, install
```

```python
    parser.add_argument("command", choices=["configure", "serve", "status", "seed-clones", "install-launchd"])
```

```python
    if args.command == "install-launchd":
        config = load_config(args.config)
        target = Path.home() / "Library" / "LaunchAgents"
        written = install(config, target, cloudflared=shutil.which("cloudflared") or "cloudflared")
        print(json.dumps({label: str(path) for label, path in written.items()}, indent=2))
        print("\nLoad them with:")
        for label in AGENTS.values():
            print(f"  launchctl bootstrap gui/$(id -u) {target}/{label}.plist")
        print("\nStop and remove with `launchctl bootout gui/$(id -u)/<label>`.")
        return 0
```

Add `import shutil` and `from pathlib import Path` to the imports if they are missing.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k Deploy`
Expected: PASS, three tests.

- [ ] **Step 7: Document deployment**

In `README.md`, under the Run section, add:

```markdown
To keep FarmBot running across reboots on macOS:

```bash
python3 -m agent.service install-launchd
```

It writes two launchd agents and prints the `launchctl bootstrap` lines to load them. Logs land in
`.local/agent/logs/`. With no `tunnel` key in the host config it runs a quick tunnel, whose hostname
changes at every restart and must be pasted into the Linear app settings again; set `"tunnel": {"name":
"<tunnel>"}` once a named Cloudflare tunnel exists and the hostname stops moving.
```

In `docs/operating-contract.md`, replace the first bullet under `## Limits in this phase`, which begins `- One host at a time`, with:

```markdown
- One host at a time (the Mac since 2026-09-18; Windows follows in its own plan); no Unity slots. A fix
  that needs Editor verification records the gap and finishes blocked or delivers with the gap named.
  The receiver and the tunnel run as launchd agents and restart at login; while the host config names no
  named tunnel, the public hostname changes whenever the tunnel restarts and must be re-entered in Linear.
```

- [ ] **Step 8: Verify it works on this machine**

Run: `python3 -m agent.service install-launchd`
Expected: two paths printed under `~/Library/LaunchAgents`, then the bootstrap lines. Confirm the files parse:

```bash
plutil -lint ~/Library/LaunchAgents/com.kuaiwa.farmbot.*.plist
```

Expected: `OK` for both. Do not bootstrap them as part of this task; that is the operator's decision, and the Linear webhook URL must be set before FarmBot answers anything.

- [ ] **Step 9: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 132 tests.

```bash
git add agent/deploy.py agent/config.py agent/service.py README.md docs/operating-contract.md tests/test_deploy.py
git commit -m "feat(deploy): launchd agents for the service and the tunnel

FarmBot and cloudflared lived only as long as an operator's terminal session.
install-launchd writes both agents and prints the launchctl lines rather than
loading them, and one config key switches the tunnel from quick to named when a
domain exists.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** This plan implements the parts of spec §17 Phase 1 that do not need the Windows host: the deployment story for the machine FarmBot actually runs on, and the corrections that live evidence forced on §6 and §8. §17's Windows deployment, FarmQA receiver retirement and BugAgent heartbeat pause are explicitly out of scope above and carried in the checklist below. Phase 1's done-criteria 1, 2, 4 and 5 were demonstrated live on 2026-09-18; criterion 3 needs Unity slots and belongs to Plan 1b.

**Placeholders.** None. Every step names its files, shows its code and gives the command to run with the output to expect.

**Type consistency.** `ensure_clone(repo, seed_from=None)` in Task 3 is the only signature change and its one existing caller, `Worktrees.add`, passes a single positional argument, so it is unaffected. `RuntimeConfig` gains a defaulted trailing field in Task 4, so the three existing constructions stay valid. `seed_clones` and `install` return dictionaries and are consumed only by `main`. The `no_change` key in Task 1 is read in exactly two places, `Ledger.finish` and `session_response`.

**Test counts.** The suite stands at 118 before Task 1 and 132 after Task 5 (Task 3 adds four, not three; its step says so). Each task states the expected total so a drift is caught immediately.

## Operational checklist, owed but not code

These need the Windows host or a human decision and are tracked here so they are not lost:

1. Re-run the runtime spike on Windows: `codex exec` flags, auth seeding, whether the sandbox honours `writable_roots` there, and the descendant kill path.
2. Port `FarmTestAgent/tools/farmqa-windows-supervisor.ps1` to FarmBot, or replace it with a Windows service, matching what Task 5 gives macOS.
3. Retire the FarmQA receiver and tunnel scheduled tasks on Windows, and pause the BugAgent Codex heartbeat `farm-linear-bug-agent-windows`.
4. Buy a throwaway domain, put it on Cloudflare, create the named tunnel, and set `"tunnel": {"name": "..."}` in the host config. Rejected alternatives and their reasons are in the live smoke record.
5. Confirm the tunnel still connects with the host's local proxy client disabled. Every hostname resolved from the Mac during the live test landed in the `198.18.x.x` fake-IP range, so an unattended daemon may currently depend on that proxy running.
6. Decide who closes FARM-1127, which FarmBot delivered as already fixed and which still names FarmBot as its delegate.
