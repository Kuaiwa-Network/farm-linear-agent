"""Speculative queue journeys with real SQLite claims and scripted GitHub state."""
import json
from types import SimpleNamespace

from agent.review_controller import Controller
from agent.review_policy import ReviewError, digest
from test_review import APP, BASE, HEAD, ReviewFixture, policy


class GitHub:
    def __init__(self):
        self.heads, self.merged, self.states, self.labels = {}, set(), {}, {}
        self.bases, self.calls, self.checked, self.blocked = {'common': BASE}, [], [], set()

    @property
    def base(self):
        return self.bases['common']

    @base.setter
    def base(self, value):
        self.bases['common'] = value

    def pr(self, pr):
        return {'html_url': pr['url'], 'head': {'sha': self.heads.get(pr['url'], pr['source_head'])},
                'base': {'sha': self.target(pr)}, 'merged': pr['url'] in self.merged,
                'draft': False, 'mergeable': True, 'labels': self.labels.get(pr['url'], [])}

    def authority(self, repo, owner):
        pass

    def validate_pr(self, pr, value):
        if value['head']['sha'] != pr['source_head'] or value['merged']:
            raise ReviewError('head changed')

    def blockers(self, pr):
        return pr['url'] in self.blocked

    def bot_feedback(self, pr):
        return []

    def rerun(self, repo, sha, ci):
        self.calls.append(('rerun', sha))

    def files(self, pr):
        return [{'filename': 'feature.py', 'additions': 5, 'deletions': 1}]

    def checks(self, repo, sha, required):
        self.checked.append(sha)
        return {'sha': sha, 'state': self.states.get(sha, 'waiting'), 'checks': [], 'failures': []}

    def target(self, pr):
        return self.bases.get(pr['repository'], BASE)

    def commits(self, pr):
        return {HEAD}

    def default_head(self, repo):
        return self.target({'repository': repo})

    def protected(self, pr):
        return False

    def dispatch(self, snapshot, policy):
        self.calls.append(('ci', snapshot['commit']))

    def ready(self, pr, value):
        self.calls.append(('ready', pr['url']))

    def notify(self, pr, marker, body, **kwargs):
        self.calls.append(('notify', marker))


class Git:
    def __init__(self, github):
        self.github, self.parents, self.snapshots, self.calls = github, {}, {}, []
        self.worktrees = self
        self.counter, self.conflicts, self.lose_response = 0, set(), False

    def fetch(self, repo):
        pass

    def prepare(self, pr, metadata, predecessor=None):
        self.counter += 1
        sha = f'{self.counter:040x}'
        base = self.github.target(pr)
        prefix = predecessor['commit'] if predecessor else base
        self.parents[sha] = [prefix, pr['source_head']]
        self.snapshots[sha] = pr['url']
        return {'repository': pr['repository'], 'url': pr['url'], 'target': pr['target'],
                'head': pr['source_head'], 'base': base, 'prefix': prefix,
                'commit': sha, 'tree': 'd' * 40, 'conflicts': pr['url'] in self.conflicts}

    def files(self, snapshot):
        return [{'filename': 'feature.py', 'additions': 1, 'deletions': 1}]

    def ancestor(self, repo, earlier, later):
        return earlier == later or any(self.ancestor(repo, earlier, p) for p in self.parents.get(later, []))

    def publish_candidate(self, snapshot):
        self.calls.append(('publish', snapshot['id']))

    def repaired(self, snapshot, attempt, head):
        self.parents[head] = [snapshot['commit']]
        self.snapshots[head] = snapshot['url']
        return {**snapshot, 'commit': head, 'tree': 'e' * 40, 'conflicts': False, 'repair_parent': snapshot['id']}

    def promote(self, snapshot, branch):
        self.calls.append(('promote', snapshot['commit']))
        self.github.heads[snapshot['url']] = snapshot['commit']

    def merge_exact(self, snapshot, actual):
        if actual != self.github.target(snapshot):
            raise ReviewError('non-FF')
        self.github.bases[snapshot['repository']] = snapshot['commit']
        self.github.merged.add(snapshot['url'])
        self.calls.append(('merge', snapshot['url']))
        if self.lose_response:
            raise OSError('lost response after accepted push')

    def text(self, repo, head, path):
        return 'COMMON_COMMIT=' + HEAD


class Workers:
    def __init__(self, store):
        self.store, self.launched, self.tokens, self.contexts = store, [], {}, []
        self.capacity = 3

    def reap(self):
        db = self.store.db
        db.execute("UPDATE review_attempts SET state='processed' WHERE state='stopping'")
        db.execute("UPDATE review_attempts SET state='settled' WHERE state='submitted'")
        return [dict(r) for r in db.execute("SELECT * FROM review_attempts WHERE state='settled'")]

    def launch(self, snapshot, role, context):
        active = self.store.db.execute("SELECT * FROM review_attempts WHERE state IN ('queued','running','submitted','stopping')").fetchall()
        if len(active) >= self.capacity or any(r['candidate_id'] == snapshot['id'] for r in active):
            return False
        attempt = self.store.attempt(snapshot['id'], role)
        self.tokens[attempt['id']] = self.store.claim(attempt['id'], 2700)
        self.launched.append(attempt)
        self.contexts.append(context)
        return True

    def finish(self, attempt, result):
        self.store.submit(attempt['id'], self.tokens[attempt['id']], result)

    def blockers(self):
        return []


class ControllerTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.github = GitHub()
        self.git = Git(self.github)
        self.workers = Workers(self.store)
        self.config = SimpleNamespace(auto_merge=policy(mode='merge'), repos={'common': 'unused'}, instance_id='test',
                                      max_concurrent=8, local_root=self.path.parent)
        self.api = SimpleNamespace(app_user_id=APP, fetch_issue=self.ledger.issue,
                                   awaiting_review=lambda *args, **kwargs: True)
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)

    def delivery(self, number=1, **changes):
        run, pr = super().delivery(number, **changes)
        self.store.db.execute('UPDATE review_runs SET policy=? WHERE id=?', (self.controller.run_policy(run['id']), run['id']))
        return self.store.row('review_runs', run['id']), pr

    def good(self, attempt=None):
        attempt = attempt or self.workers.launched[-1]
        snap = json.loads(self.store.row('review_candidates', attempt['candidate_id'])['snapshot'])
        self.workers.finish(attempt, self.verdict(snap))
        return snap

    def launched_prs(self):
        return [self.store.row('review_candidates', a['candidate_id'])['pr_id'] for a in self.workers.launched]

    def test_legacy_pr_and_issue_status_labels_do_not_block_new_review(self):
        self.config.auto_merge['hold_labels'] = ['needs-person', '待人工审查', 'hold-auto-merge']
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        run, pr = self.delivery(labels=['needs-person', '待人工审查'])
        self.github.labels[pr['url']] = [{'name': 'needs-person'}, {'name': '待人工审查'}]
        self.controller.tick()
        self.assertEqual(len(self.workers.launched), 1)
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')

    def test_explicit_manual_hold_still_blocks_review(self):
        run, pr = self.delivery()
        self.github.labels[pr['url']] = [{'name': 'hold-auto-merge'}]
        self.controller.tick()
        self.assertEqual(self.workers.launched, [])
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')

    def test_handoff_label_failure_schedules_retry_without_hiding_handoff_notice(self):
        from unittest.mock import Mock
        run, _ = self.delivery()
        self.store.handoff(run['id'], '需要人工审查。')
        self.api.awaiting_review = Mock(side_effect=[OSError('lost label response'), True])
        self.controller.linear_notice = Mock()
        self.controller.notifications()
        self.assertEqual(self.store.row('review_runs', run['id'])['notified'], 1)
        issue_id = self.ledger.item(run['item_id'])['issue_id']
        self.assertTrue(self.ledger.status_check(issue_id)['requested'])
        self.controller.notifications()
        self.assertEqual(self.store.row('review_runs', run['id'])['notified'], 1)
        self.assertEqual(self.api.awaiting_review.call_args.args,
                         (self.ledger.item(run['item_id'])['issue_id'], True))
        self.controller.linear_notice.assert_called_once()
        self.api.awaiting_review.assert_called_once()

    def test_shared_ui_path_gets_reviewed_and_can_merge_after_routine_verdict(self):
        self.controller.policy['repositories']['common']['sensitive'] = ['Core/UI/*']
        files = [{'filename': 'Core/UI/MessageBox.cs', 'additions': 5, 'deletions': 1}]
        self.github.files = lambda pr: files
        self.git.files = lambda snapshot: files
        run, pr = self.delivery()
        self.controller.tick()
        self.assertEqual(self.store.row('review_prs', pr['id'])['state'], 'reviewing')
        focus = self.workers.contexts[-1]['risk_focus']
        self.assertEqual(focus['source'][0]['rule'], 'Core/UI/*')
        self.assertEqual(focus['candidate'][0]['file'], files[0]['filename'])
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')

    def test_sensitive_path_added_by_repair_is_independently_reviewed(self):
        self.controller.policy['repositories']['common']['sensitive'] = ['Core/UI/*']
        run, pr = self.delivery()
        self.git.conflicts.add(pr['url'])
        self.git.files = lambda snapshot: [{'filename': 'Core/UI/MessageBox.cs' if snapshot.get('repair_parent') else 'feature.py'}]
        self.controller.tick()
        attempt = self.workers.launched[-1]
        self.assertEqual(attempt['role'], 'repair')
        self.workers.finish(attempt, {'snapshot': attempt['candidate_id'], 'head': 'e' * 40,
                                     'summary': '已解决冲突并验证弹窗调用兼容性', 'needs_person': False})
        self.controller.tick()
        self.assertEqual(self.workers.launched[-1]['role'], 'review')
        focus = self.workers.contexts[-1]['risk_focus']
        self.assertEqual(focus['source'], [])
        self.assertEqual(focus['candidate'][0]['rule'], 'Core/UI/*')
        self.assertIsNone(self.store.row('review_runs', run['id'])['disposition'])

    def test_focus_read_failure_uses_bounded_recovery_before_launch(self):
        from unittest.mock import Mock
        run, pr = self.delivery()
        self.controller.risk_focus = Mock(side_effect=OSError('metadata unavailable'))
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.controller.risk_focus.call_count, 1)
        self.assertEqual(self.workers.launched, [])
        for _ in range(2):
            self.now += 300
            self.controller.tick()
        terminal = self.store.row('review_runs', run['id'])
        self.assertEqual(terminal['disposition'], 'human_handoff')
        self.assertIn('review_context', terminal['reason'])
        self.assertIn('连续恢复失败 3 次', terminal['reason'])

    def test_hard_path_handoff_explains_gate_and_no_ai_review(self):
        self.github.files = lambda pr: [{'filename': '.github/workflows/test.yml'}]
        run, pr = self.delivery()
        self.controller.tick()
        run = self.store.row('review_runs', run['id'])
        self.assertEqual(run['disposition'], 'human_handoff')
        self.assertEqual(self.workers.launched, [])
        body = self.controller.notification_body(run)
        for text in ('硬性路径门禁', '.github/*', '.github/workflows/test.yml', pr['url'],
                     '尚未进入独立 AI 审查', '已接收审查报告：0', 'AI 修复轮数：0'):
            self.assertIn(text, body)

    def test_actual_high_risk_reason_and_review_progress_survive_handoff(self):
        run, pr = self.delivery()
        self.controller.tick()
        attempt = self.workers.launched[-1]
        snapshot = self.controller.candidate(self.store.row('review_prs', pr['id']))
        reason = '改动：store.py:12 删除旧存档；风险：回滚也无法恢复玩家数据；依据：delete_many 无备份；需人工：确认迁移与恢复方案。'
        self.workers.finish(attempt, self.verdict(snapshot, risk='high', reasons=[reason], summary='存在不可逆的数据删除风险。'))
        self.controller.tick()
        body = self.controller.notification_body(self.store.row('review_runs', run['id']))
        for text in ('AI 独立审查判定高风险', reason, '已接收审查报告：1', pr['url']):
            self.assertIn(text, body)
        self.assertNotIn('尚未进入独立 AI 审查', body)
        self.assertFalse(any(call[0] == 'merge' for call in self.git.calls))

    def test_attempt_without_accepted_report_does_not_claim_review_completed(self):
        run, _ = self.delivery()
        self.controller.tick()
        self.store.handoff(run['id'], '基础设施恢复失败，需要人工排查。')
        body = self.controller.notification_body(self.store.row('review_runs', run['id']))
        self.assertIn('独立 AI 审查尝试：1 次；已接收审查报告：0 份', body)
        self.assertIn('尚未收到可用的独立审查结论', body)

    def test_legacy_path_reason_is_displayed_in_chinese_without_reviving_run(self):
        run, pr = self.delivery()
        self.store.handoff(run['id'], 'sensitive path: Core/UI/MessageBox.cs')
        run = self.store.row('review_runs', run['id'])
        body = self.controller.notification_body(run)
        self.assertIn('历史路径门禁', body)
        self.assertIn('不能据此认定实际改动存在缺陷', body)
        self.assertNotIn('sensitive path:', body)
        self.controller.tick()
        self.assertEqual(self.store.row('review_prs', pr['id'])['state'], 'delivered')
        self.assertEqual(self.workers.launched, [])

    def test_review_priority_orders_all_levels_then_fifo_without_reordering_merges(self):
        self.config.auto_merge['depth'] = 6
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        deliveries = [self.delivery(n, priority=p) for n, p in enumerate((0, 4, 3, 2, 1, 2), 1)]
        prs = [p for _, p in deliveries]
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [prs[i]['id'] for i in (4, 3, 5)])
        for attempt in list(self.workers.launched):
            snapshot = self.good(attempt)
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [prs[i]['id'] for i in (4, 3, 5, 2, 1, 0)])
        for attempt in self.workers.launched[3:]:
            snapshot = self.good(attempt)
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual([c[1] for c in self.git.calls if c[0] == 'merge'], [p['url'] for p in prs])
        self.assertTrue(all(self.store.row('review_runs', r['id'])['disposition'] == 'auto_merged'
                            for r, _ in deliveries))

    def test_live_priority_change_reorders_waiting_reviews_without_rebuilding_candidates(self):
        deliveries = [self.delivery(n, priority=p) for n, p in enumerate((2, 3, 4), 1)]
        self.workers.capacity = 0
        self.controller.tick()
        candidates = [p['candidate_id'] for p in self.controller.prs()]
        run, urgent = deliveries[2]
        issue_id = self.ledger.item(run['item_id'])['issue_id']
        updated = {**self.ledger.issue(issue_id), 'priority': 1, 'updated_at': '2026-10-27T10:00:00Z'}
        self.api.fetch_issue = lambda key: updated if key == issue_id else self.ledger.issue(key)
        self.workers.capacity = 3
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [urgent['id'], deliveries[0][1]['id'], deliveries[1][1]['id']])
        self.assertEqual([p['candidate_id'] for p in self.controller.prs()], candidates)
        self.assertEqual(self.ledger.item(run['item_id'])['priority'], 4)  # Author's historical priority.
        self.assertTrue(all(self.store.current(r) for r, _ in deliveries))

    def test_stale_linear_read_cannot_undo_newer_review_priority(self):
        _, high = self.delivery(1, priority=2)
        run, urgent = self.delivery(2, priority=4, updated_at='2026-10-27T09:00:00Z')
        issue_id = self.ledger.item(run['item_id'])['issue_id']
        stale = self.ledger.issue(issue_id)
        self.ledger.apply_issue_status({**stale, 'priority': 1, 'updated_at': '2026-10-27T10:00:00Z'})
        self.api.fetch_issue = lambda key: stale if key == issue_id else self.ledger.issue(key)
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [urgent['id'], high['id']])
        self.assertEqual(self.ledger.issue(issue_id)['priority'], 1)

    def test_repairs_use_priority_and_always_precede_urgent_reviews(self):
        repo_policy = self.config.auto_merge['repositories']['common']
        for repo in ('one', 'two'):
            self.config.auto_merge['repositories'][repo] = dict(repo_policy)
            self.config.repos[repo] = 'unused'
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        _, review = self.delivery(1, priority=1)
        _, low_repair = self.delivery(2, priority=0)
        _, high_repair = self.delivery(3, priority=2)
        for repo, pr in (('one', low_repair), ('two', high_repair)):
            self.store.db.execute('UPDATE review_prs SET repository=? WHERE id=?', (repo, pr['id']))
            self.store.db.execute('UPDATE review_runs SET policy=? WHERE id=?',
                                  (self.controller.run_policy(pr['run_id']), pr['run_id']))
            self.git.conflicts.add(pr['url'])
        self.workers.capacity = 1
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [high_repair['id']])
        self.assertEqual(self.workers.launched[-1]['role'], 'repair')
        for pr, head in ((high_repair, 'e' * 40), (low_repair, 'f' * 40)):
            attempt = self.workers.launched[-1]
            self.assertEqual(self.launched_prs()[-1], pr['id'])
            self.assertEqual(attempt['role'], 'repair')
            self.workers.finish(attempt, {'snapshot': attempt['candidate_id'], 'head': head,
                                         'summary': 'fixed conflict', 'needs_person': False})
            self.controller.tick()
        self.assertEqual(self.launched_prs(), [high_repair['id'], low_repair['id'], review['id']])
        self.assertEqual(self.workers.launched[-1]['role'], 'review')

    def test_priority_never_preempts_running_review(self):
        _, low = self.delivery(1, priority=4)
        self.workers.capacity = 1
        self.controller.tick()
        running = self.workers.launched[0]
        _, urgent = self.delivery(2, priority=1)
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [low['id']])
        self.assertEqual(self.store.row('review_attempts', running['id'])['state'], 'running')
        self.good(running)
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [low['id'], urgent['id']])

    def test_retry_delayed_urgent_review_does_not_block_eligible_lower_priority(self):
        _, low = self.delivery(1, priority=4)
        _, urgent = self.delivery(2, priority=1)
        self.workers.capacity = 0
        self.controller.tick()
        self.store.db.execute('UPDATE review_prs SET retry_at=? WHERE id=?', (self.now + 60, urgent['id']))
        self.workers.capacity = 1
        self.controller.tick()
        self.assertEqual(self.launched_prs(), [low['id']])

    def test_three_speculative_reviews_release_capacity_while_ci_waits_then_merge_in_order(self):
        deliveries = [self.delivery(n) for n in (1, 2, 3)]
        self.controller.tick()
        self.assertEqual(len(self.workers.launched), 3)
        snapshots = [self.good(a) for a in self.workers.launched]
        self.assertEqual(snapshots[1]['prefix'], snapshots[0]['commit'])
        self.assertEqual(snapshots[2]['prefix'], snapshots[1]['commit'])
        self.controller.tick()
        self.assertTrue(all(p['state'] == 'waiting_ci' for p in self.controller.prs()))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM review_attempts WHERE state='running'").fetchone()[0], 0)
        for snapshot in reversed(snapshots):
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual([c[1] for c in self.git.calls if c[0] == 'merge'], [p['url'] for _, p in deliveries])
        self.assertTrue(all(self.store.row('review_runs', r['id'])['disposition'] == 'auto_merged' for r, _ in deliveries))

    def test_original_head_ci_cannot_clear_integrated_candidate(self):
        self.delivery()
        self.github.states[HEAD] = 'passed'
        self.controller.tick()
        self.good()
        self.controller.tick()
        self.assertEqual(self.controller.prs()[0]['state'], 'waiting_ci')
        self.assertNotIn(HEAD, self.github.checked)

    def test_slow_ci_does_not_consume_repair_budget_or_handoff(self):
        run, _ = self.delivery()
        self.controller.tick()
        self.good()
        for _ in range(12):
            self.now += 3600
            self.controller.tick()
        self.assertEqual(self.controller.prs()[0]['repairs'], 0)
        self.assertIsNone(self.store.row('review_runs', run['id'])['disposition'])
        self.assertEqual(len(self.workers.launched), 1)

    def test_unknown_mergeability_is_bounded_metadata_recovery_not_a_code_repair(self):
        run, _ = self.delivery()
        original = self.github.pr
        self.github.pr = lambda pr: {**original(pr), 'mergeable': None}
        self.controller.tick()
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'passed'
        for _ in range(3):
            self.now += 300
            self.controller.tick()
        self.assertEqual(self.controller.prs()[0]['repairs'], 0)
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertFalse(any(c[0] == 'merge' for c in self.git.calls))

    def test_failure_of_a_invalidates_b_c_and_prioritizes_repair(self):
        for n in (1, 2, 3):
            self.delivery(n)
        self.controller.tick()
        snapshots = [self.good(a) for a in self.workers.launched]
        self.github.states[snapshots[0]['commit']] = 'failed'
        self.controller.tick()
        self.assertEqual(self.workers.launched[-1]['role'], 'repair')
        for snapshot in snapshots[1:]:
            self.assertEqual(self.store.row('review_candidates', snapshot['id'])['state'], 'stale')
        for pr in self.controller.prs()[1:]:
            self.assertEqual((pr['state'], pr['candidate_id']), ('queued', None))

    def test_high_risk_handoff_is_terminal_even_after_human_approval(self):
        run, pr = self.delivery()
        self.controller.tick()
        attempt = self.workers.launched[-1]
        snapshot = json.loads(self.store.row('review_candidates', attempt['candidate_id'])['snapshot'])
        self.workers.finish(attempt, self.verdict(snapshot, risk='high', reasons=[
            '改动：auth.py:12 放宽权限；风险：普通用户可读管理数据；依据：检查被移除；需人工：确认新的授权边界。']))
        self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.github.blocked.clear()
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertFalse(any(c[0] == 'merge' for c in self.git.calls))

    def test_last_repair_can_pass_and_merge_after_source_publication_and_fresh_review(self):
        run, _ = self.delivery()
        self.controller.tick()
        snap = self.good()
        pr = self.controller.prs()[0]
        self.store.db.execute('UPDATE review_prs SET repairs=2 WHERE id=?', (pr['id'],))
        self.github.states[snap['commit']] = 'failed'
        self.controller.tick()
        repair = self.workers.launched[-1]
        self.workers.finish(repair, {'snapshot': snap['id'], 'head': 'f' * 40, 'summary': 'fixed defect', 'needs_person': False})
        self.controller.tick()
        self.assertEqual(self.workers.launched[-1]['role'], 'review')
        repaired = self.good()
        self.github.states[repaired['commit']] = 'passed'
        self.controller.tick()  # atomically publish the exact reviewed repair and merge
        self.assertEqual(self.controller.prs()[0]['repairs'], 3)
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')

    def test_lost_merge_response_is_reconciled_without_duplicate_merge(self):
        run, _ = self.delivery()
        self.controller.tick()
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'passed'
        self.git.lose_response = True
        self.controller.tick()
        self.now += 60
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')
        self.assertEqual(sum(c[0] == 'merge' for c in self.git.calls), 1)

    def test_shadow_never_publishes_marks_ready_repairs_or_merges(self):
        self.config.auto_merge = policy(mode='shadow')
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        self.delivery()
        self.controller.tick()
        snap = self.good()
        self.github.states[snap['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.git.calls, [])
        self.assertEqual(self.github.calls, [])

    def test_external_source_change_fences_entire_run(self):
        run, pr = self.delivery()
        self.controller.tick()
        self.github.heads[pr['url']] = 'e' * 40
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')

    def test_external_target_change_rebuilds_not_reuses_evidence(self):
        self.delivery()
        self.controller.tick()
        old = self.good()
        self.github.base = 'e' * 40
        self.github.states[old['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_candidates', old['id'])['state'], 'stale')
        self.assertNotEqual(self.controller.prs()[0]['candidate_id'], old['id'])
        self.assertFalse(any(c[0] == 'merge' for c in self.git.calls))

    def multi_delivery(self):
        repo_policy = self.config.auto_merge['repositories']['common']
        self.config.auto_merge = policy(mode='merge', repositories={
            'common': repo_policy, 'server': {**repo_policy, 'dependencies': ['common'],
                'pin': {'repository': 'common', 'path': 'pin.env', 'key': 'COMMON_COMMIT'}}})
        self.config.repos['server'] = 'unused'
        self.controller = Controller(self.ledger, self.config, self.api, self.github, self.git, self.workers)
        run, common = self.delivery()
        server = {**common, 'id': digest(['server', run['id']]), 'repository': 'server',
                  'url': 'https://github.com/example/server/pull/2'}
        self.store.db.execute('INSERT INTO review_prs(id,run_id,repository,url,target,source_head,source_branch) VALUES(?,?,?,?,?,?,?)',
            tuple(server[k] for k in ('id', 'run_id', 'repository', 'url', 'target', 'source_head', 'source_branch')))
        self.store.db.execute('UPDATE review_runs SET policy=? WHERE id=?', (self.controller.run_policy(run['id']), run['id']))
        return run

    def test_dependency_pin_membership_is_checked_before_any_merge_and_ancestry_after_common(self):
        run = self.multi_delivery()
        self.controller.tick()
        for a in list(self.workers.launched):
            snapshot = self.good(a)
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual([call[1] for call in self.git.calls if call[0] == 'merge'],
            ['https://github.com/example/common/pull/1', 'https://github.com/example/server/pull/2'])
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')

    def test_invalid_pin_hands_off_before_common_can_merge(self):
        run = self.multi_delivery()
        self.github.commits = lambda pr: {'e' * 40}
        self.controller.tick()
        for a in list(self.workers.launched):
            snapshot = self.good(a)
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertFalse(any(c[0] == 'merge' for c in self.git.calls))

    def test_related_repair_fences_inflight_review_and_requires_new_cross_repository_review(self):
        run = self.multi_delivery()
        self.controller.tick()
        first, second = self.workers.launched
        server_snapshot = json.loads(self.store.row('review_candidates', second['candidate_id'])['snapshot'])
        common = self.controller.prs()[0]
        self.controller.need_repair(common, 'CI failed')
        with self.assertRaises(ReviewError):
            self.workers.finish(second, self.verdict(server_snapshot))
        self.assertFalse(self.controller.all_reviewed(run))

    def test_related_repair_invalidates_stored_verdict_without_reusing_it_for_new_inputs(self):
        run = self.multi_delivery()
        self.controller.tick()
        snapshots = [self.good(a) for a in self.workers.launched]
        self.controller.tick()
        common, server = self.controller.prs()
        self.assertIsNotNone(self.store.row('review_candidates', server['candidate_id'])['verdict'])
        self.controller.need_repair(common, 'new CI failure')
        self.controller.tick()
        retained = self.store.row('review_candidates', server['candidate_id'])
        self.assertFalse(self.store.inputs_current(run['id'], retained['review_inputs']))
        self.assertFalse(self.controller.all_reviewed(run))
        repair = self.workers.launched[-1]
        self.assertEqual(repair['role'], 'repair')
        self.workers.finish(repair, {'snapshot': common['candidate_id'], 'head': 'f' * 40,
                                   'summary': 'fixed', 'needs_person': False})
        self.controller.tick()
        # Server's own commit is unchanged, but its reviewer must inspect the new common candidate.
        self.assertEqual(self.controller.prs()[1]['candidate_id'], server['candidate_id'])
        fresh = self.workers.launched[-2:]
        self.assertTrue(all(a['role'] == 'review' for a in fresh))
        self.assertNotEqual(fresh[1]['review_inputs'], self.workers.launched[1]['review_inputs'])
        for attempt in fresh:
            snapshot = self.good(attempt)
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'auto_merged')

    def test_unrecognized_merge_is_bounded_without_repeating_an_accepted_push(self):
        run, pr = self.delivery()
        original = self.git.merge_exact
        def merge(snapshot, base):
            original(snapshot, base)
            self.github.merged.clear()  # Target landed; PR recognition is unavailable.
        self.git.merge_exact = merge
        self.controller.tick()
        snapshot = self.good()
        self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        for _ in range(3):
            self.now += 300
            self.controller.tick()
        terminal = self.store.row('review_runs', run['id'])
        self.assertEqual(terminal['disposition'], 'human_handoff')
        self.assertIn('合并结果尚未确认', terminal['reason'])
        self.assertEqual(sum(c[0] == 'merge' for c in self.git.calls), 1)
        self.assertEqual(self.store.db.execute('SELECT state FROM review_merges').fetchone()[0], 'pending')

    def test_terminal_handoff_notification_reconciles_a_lost_linear_response(self):
        run, _ = self.delivery()
        self.store.handoff(run['id'], 'High risk')
        issue = self.ledger.issue(self.ledger.item(run['item_id'])['issue_id'])
        posted = []
        self.api.fetch_issue = lambda key: issue
        def post(key, body):
            posted.append(body)
            issue['comments'].append({'id': 'remote-review-comment', 'body': body, 'author_kind': 'bot'})
            raise OSError('response lost after comment was created')
        self.api.create_comment = post
        self.controller.notifications()
        self.assertEqual(self.store.row('review_runs', run['id'])['notified'], 0)
        self.controller.notifications()
        self.assertEqual(self.store.row('review_runs', run['id'])['notified'], 1)
        self.assertEqual(len(posted), 1)
        self.assertIn('后续由人工审查', posted[0])
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')

    def test_unsupported_backend_prevents_partial_delivery_merge(self):
        run = self.multi_delivery()
        self.controller.policy['repositories']['server']['backend'] = 'github_queue'
        self.store.db.execute('UPDATE review_runs SET policy=? WHERE id=?', (self.controller.run_policy(run['id']), run['id']))
        self.controller.tick()
        for a in list(self.workers.launched):
            snapshot = self.good(a)
            self.github.states[snapshot['commit']] = 'passed'
        self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertIn('合并后端尚未实现', self.store.row('review_runs', run['id'])['reason'])
        self.assertFalse(any(c[0] == 'merge' for c in self.git.calls))

    def test_partial_merge_failure_retains_confirmed_inventory_and_never_reverts(self):
        run = self.multi_delivery()
        original = self.git.merge_exact
        def merge(snapshot, base):
            if snapshot['repository'] == 'server':
                raise OSError('transport unavailable')
            return original(snapshot, base)
        self.git.merge_exact = merge
        self.controller.tick()
        for a in list(self.workers.launched):
            snapshot = self.good(a)
            self.github.states[snapshot['commit']] = 'passed'
        for _ in range(3):
            self.now += 300
            self.controller.tick()
        self.assertEqual(self.store.row('review_runs', run['id'])['disposition'], 'human_handoff')
        self.assertEqual([p['state'] for p in self.controller.prs()], ['merged', 'delivered'])
        self.assertEqual([call[1] for call in self.git.calls if call[0] == 'merge'], ['https://github.com/example/common/pull/1'])
