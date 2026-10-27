"""Durable speculative review queue. GitHub owns CI; no AI process waits for it.

Only the designated instance mutates a repository. Unsupported promotion backends
remain shadow-only: an ordinary PR merge API cannot fence the target branch.
"""
import json

from .maintenance import Maintenance
from .review_policy import ReviewError, commit, digest, encoded, evidence_policy, review_focus, risk, settings
from .review_store import ReviewStore


class Controller:
    def __init__(self, ledger, config, api, github, git, workers):
        self.ledger, self.db = ledger, ledger.connection
        self.config, self.api, self.github, self.git, self.workers = config, api, github, git, workers
        self.store = ReviewStore(ledger)
        self.policy = settings(config.auto_merge, config.repos)
        self.authorized, self.authority_checks = set(), {}

    def run_policy(self, run_id):
        return evidence_policy(self.policy, [p['repository'] for p in self.prs(run_id)])

    @property
    def writes(self):
        return self.policy['mode'] == 'merge' and self.config.instance_id == self.policy['authority']

    def prs(self, run_id=None, *, active_only=False):
        return [dict(r) for r in self.db.execute('SELECT * FROM review_prs' +
            (' WHERE run_id=?' if run_id else
             " WHERE state NOT IN ('merged','delivered') AND run_id IN "
             "(SELECT id FROM review_runs WHERE state NOT IN ('delivered','superseded'))" if active_only else '')
            + ' ORDER BY sequence', (run_id,) if run_id else ())]

    def worker_queue(self, state):
        # Delivered work items keep their old priority. Read the latest observed
        # issue instead, without changing the speculative chain's sequence.
        return [dict(r) for r in self.db.execute(
            'SELECT p.* FROM review_prs p JOIN review_runs r ON r.id=p.run_id '
            'JOIN work_items w ON w.id=r.item_id JOIN issues i ON i.id=w.issue_id '
            "WHERE p.state=? AND r.state NOT IN ('delivered','superseded') "
            'ORDER BY COALESCE(NULLIF(json_extract(i.metadata,\'$.priority\'),0),5),p.sequence',
            (state,))]

    def candidate(self, pr):
        return json.loads(self.store.row('review_candidates', pr['candidate_id'])['snapshot']) if pr['candidate_id'] else None

    def active(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM review_runs WHERE state NOT IN ('delivered','superseded') ORDER BY created_at,id")]

    def tick(self):
        # Reaping also fences stale workers when disabled or in maintenance.
        completed = self.workers.reap()
        if self.policy['mode'] == 'off':
            return
        if self.writes and not Maintenance(self.ledger).requested() and not self.db.execute('SELECT paused FROM review_control').fetchone()[0]:
            owner = {'version': 1, 'instance': self.config.instance_id, 'app': self.api.app_user_id,
                     'controller': self.store.identity()}
            for repo in self.policy['repositories']:
                if self.authority_checks.get(repo, 0) > self.ledger.clock():
                    continue
                try:
                    self.github.authority(repo, owner)
                    self.authorized.add(repo)
                    self.authority_checks[repo] = self.ledger.clock() + 300
                except Exception as exc:
                    self.authorized.discard(repo)
                    self.authority_checks[repo] = self.ledger.clock() + 60
                    self.db.execute("UPDATE review_prs SET error=? WHERE repository=? AND state NOT IN ('merged','delivered')",
                                    ('Review authority unavailable: ' + type(exc).__name__, repo))
                    # Still reap, reconcile and report; never publish without authority.
        for delivery in self.db.execute('SELECT d.* FROM review_deliveries d LEFT JOIN review_runs r '
                'ON r.item_id=d.item_id AND r.generation=d.generation WHERE r.id IS NULL ORDER BY d.created_at').fetchall():
            self.store.enroll(dict(delivery), evidence_policy(self.policy,
                              [p['repo'] for p in json.loads(delivery['inputs'])]))
        for attempt in completed:
            try:
                self.completed(attempt)
            except Exception as exc:
                candidate = self.store.row('review_candidates', attempt['candidate_id'])
                self.retry(self.store.row('review_prs', candidate['pr_id']), type(exc).__name__, phase='worker')
            finally:
                self.db.execute("UPDATE review_attempts SET state='processed' WHERE id=? AND state='settled'", (attempt['id'],))
        fresh_runs = set()
        for run in self.active():
            if any(p['retry_at'] > self.ledger.clock() for p in self.prs(run['id'])
                   if p['state'] not in ('merged', 'delivered')):
                continue
            try:
                self.refresh(run)
                for pr in self.prs(run['id']):
                    self.recovered(pr, 'refresh')
                fresh_runs.add(run['id'])
            except Exception as exc:
                # Unavailable metadata never becomes permission to merge.
                for pr in self.prs(run['id']):
                    if pr['state'] not in ('merged', 'delivered'):
                        self.retry(pr, type(exc).__name__, phase='refresh')
        self.observe_incidents()
        if Maintenance(self.ledger).requested():
            if self.writes:
                self.notifications()
            return
        paused = self.db.execute('SELECT paused FROM review_control').fetchone()[0]
        if paused:
            if self.writes:
                self.notifications()
            return
        for pr in self.prs(active_only=True):
            pr = self.store.row('review_prs', pr['id'])  # An earlier PR may have invalidated this row in this tick.
            run = self.store.row('review_runs', pr['run_id'])
            if run['id'] not in fresh_runs or not self.store.current(run) or pr['retry_at'] > self.ledger.clock():
                continue
            if self.writes and pr['repository'] not in self.authorized:
                continue
            if pr['state'] in ('queued', 'reviewing', 'waiting_ci', 'ready', 'repair'):
                try:
                    self.advance(pr)
                    self.recovered(pr, 'advance')
                except Exception as exc:
                    self.retry(self.store.row('review_prs', pr['id']), type(exc).__name__)
        # Repairs win admission; Linear priority orders each role, then FIFO.
        # Running reviews are never preempted for capacity.
        for state, role in (('repair', 'repair'), ('reviewing', 'review')):
            if role == 'repair' and not self.writes:
                continue
            for pr in self.worker_queue(state):
                pr = self.store.row('review_prs', pr['id'])
                if pr['state'] != state or not pr['candidate_id'] or pr['retry_at'] > self.ledger.clock():
                    continue
                run = self.store.row('review_runs', pr['run_id'])
                if run['id'] not in fresh_runs or not self.store.current(run):
                    continue
                if self.writes and pr['repository'] not in self.authorized:
                    continue
                snapshot = self.candidate(pr)
                record = self.store.row('review_candidates', snapshot['id'])
                if role == 'review' and (snapshot['conflicts'] or self.policy['mode'] == 'observe'
                        or (record['verdict'] and self.store.inputs_current(run['id'], record['review_inputs']))):
                    continue
                if role == 'review' and self.store.review_inputs(run['id']) is None:
                    continue
                try:
                    focus = self.risk_focus(pr, snapshot) if role == 'review' else {}
                    self.recovered(pr, 'review_context')
                except Exception as exc:
                    self.retry(pr, type(exc).__name__, phase='review_context')
                    continue
                try:
                    self.workers.launch(snapshot, role, {'repairs': pr['repairs'], 'reason': pr['error'],
                        'risk_focus': focus,
                        'ci': json.loads(record['ci']) if record['ci'] else None,
                        'previous': self.history(pr), 'delivery': self.ledger.item(run['item_id']).get('evidence'),
                        'bot_feedback': {p['repository']: json.loads(p['bot_feedback']) for p in self.prs(run['id'])},
                        'author_checkpoint': self.ledger.item(run['item_id']).get('checkpoint')})
                except Exception as exc:
                    # The persisted attempt is reaped and charged once after safe
                    # teardown. Charging here as well would spend two retries.
                    self.db.execute('UPDATE review_prs SET error=? WHERE id=?', (type(exc).__name__, pr['id']))
        if self.writes:
            for pr in self.prs(active_only=True):
                pr = self.store.row('review_prs', pr['id'])
                if pr['run_id'] in fresh_runs and pr['state'] == 'ready' and pr['retry_at'] <= self.ledger.clock():
                    try:
                        self.merge(pr)
                    except Exception as exc:
                        self.retry(self.store.row('review_prs', pr['id']), type(exc).__name__, phase='merge')
            self.notifications()
        if getattr(self, 'retention', None):
            self.retention.tick()

    def history(self, pr):
        return [dict(r) for r in self.db.execute('SELECT c.id,c.verdict,a.role,a.result FROM review_candidates c '
            'LEFT JOIN review_attempts a ON a.candidate_id=c.id AND a.result IS NOT NULL WHERE c.pr_id=? '
            'ORDER BY c.created_at,a.created_at', (pr['id'],))][-12:]

    def risk_focus(self, pr, snapshot):
        policy = self.policy['repositories'][pr['repository']]
        return {'source': review_focus(self.github.files(pr), policy),
                'candidate': review_focus(self.git.files(snapshot), policy)}

    def high_risk(self, pr, verdict):
        self.store.handoff(pr['run_id'], f"AI 独立审查判定高风险：{pr['repository']}（{pr['url']}）。\n\n"
                           + '\n\n'.join(verdict['reasons']))

    def recovered(self, pr, phase):
        if self.db.execute('DELETE FROM review_recovery WHERE pr_id=? AND phase=?', (pr['id'], phase)).rowcount:
            self.retry_totals(pr)

    def retry_totals(self, pr):
        row = self.db.execute('SELECT COALESCE(max(failures),0),COALESCE(max(retry_at),0) '
                              'FROM review_recovery WHERE pr_id=?', (pr['id'],)).fetchone()
        self.db.execute('UPDATE review_prs SET retries=?,retry_at=? WHERE id=?', (*row, pr['id']))

    def retry(self, pr, reason, *, phase='advance'):
        pr = self.store.row('review_prs', pr['id'])
        if pr['state'] in ('merged', 'delivered'):
            return
        previous = self.db.execute('SELECT * FROM review_recovery WHERE pr_id=? AND phase=?', (pr['id'], phase)).fetchone()
        if previous and previous['retry_at'] > self.ledger.clock():
            return
        count = previous['failures'] + 1 if previous else 1
        self.db.execute('INSERT INTO review_recovery VALUES(?,?,?,?) ON CONFLICT(pr_id,phase) '
                        'DO UPDATE SET failures=excluded.failures,retry_at=excluded.retry_at',
                        (pr['id'], phase, count, self.ledger.clock() + min(900, 30 * 2 ** min(count, 6))))
        self.retry_totals(pr)
        self.db.execute('UPDATE review_prs SET error=? WHERE id=?', (reason, pr['id']))
        # A journaled merge is reconciled on every retry. Exhaustion hands its
        # UNCERTAIN outcome to humans; it never asserts that a timed-out push failed.
        pending = self.db.execute("SELECT 1 FROM review_merges WHERE candidate_id=? AND state='pending'", (pr['candidate_id'],)).fetchone()
        if count >= self.policy['retry_limit']:
            self.store.handoff(pr['run_id'], f"{pr['repository']}（{pr['url']}）："
                               + ('合并结果尚未确认；请核对保留的合并记录及远端分支，不能假定合并失败。' if pending else '')
                               + f"基础设施连续恢复失败 {count} 次，已达到上限；需人工排查 {phase} 阶段。错误详情：{reason}")

    def live_issue(self, run):
        item = self.ledger.item(run['item_id'])
        issue = self.api.fetch_issue(item['issue_id'])
        if not issue or not issue.get('detail_complete') or not issue.get('comments_complete'):
            raise ReviewError('complete current issue inputs are required')
        self.ledger.observe_issue(issue)
        issue = self.ledger.issue(item['issue_id'])  # Preserve newer lifecycle observations.
        if (issue.get('delegate_id') != self.api.app_user_id or issue.get('archived')
                or issue.get('status_type') in ('completed', 'canceled', 'duplicate')):
            self.store.handoff(run['id'], '任务已关闭、归档或撤销对 FarmBot 的委派，自动化授权已失效；剩余 PR 由人工处置。', superseded=True)
            return None
        if not self.store.current(self.store.row('review_runs', run['id'])):
            self.store.handoff(run['id'], '任务输入或执行代次已变化，现有审查证据不能覆盖最新请求；需人工确认剩余 PR 如何处理。', superseded=True)
            return None
        holds = set(issue.get('labels', [])) & set(self.policy['hold_labels'])
        if holds:
            self.store.handoff(run['id'], '任务存在禁止自动合并的人工交接标签：' + '、'.join(sorted(holds)) + '；请人工审查并决定是否合并。')
            return None
        return issue

    def refresh(self, run):
        if run['policy'] != self.run_policy(run['id']):
            self.store.handoff(run['id'], '审查或合并策略已变化，旧证据不能沿用；如需重新自动审查，必须通过新请求重新交付。')
            return
        # Reconcile submitted merges BEFORE comparing source/base metadata, including after a restart.
        for pr in self.prs(run['id']):
            if pr['candidate_id']:
                pending = self.db.execute("SELECT * FROM review_merges WHERE candidate_id=? AND state='pending'",
                                          (pr['candidate_id'],)).fetchone()
                if pending:
                    self.reconcile_merge(pr)
        run = self.store.row('review_runs', run['id'])
        if run['state'] in ('delivered', 'superseded'):
            return
        if not self.live_issue(run):
            return
        for pr in self.prs(run['id']):
            if pr['state'] in ('merged', 'merging'):
                continue
            if pr['repository'] not in self.policy['repositories']:
                self.store.handoff(run['id'], f"仓库 {pr['repository']} 未配置审查策略，无法确认自动合并条件；本次交付由人工审查与合并。")
                return
            if self.writes and self.policy['repositories'][pr['repository']]['backend'] != 'git_ff':
                self.store.handoff(run['id'], f"仓库 {pr['repository']} 配置的合并后端尚未实现安全自动合并；本次交付由人工合并，避免跨仓库部分提交。")
                return
            value = self.github.pr(pr)
            if value.get('merged') is True:
                self.db.execute("UPDATE review_prs SET state='merged',error='Merged outside the review queue' WHERE id=?", (pr['id'],))
                self.store.handoff(run['id'], f"PR 已在本队列之外合并：{pr['url']}；现有整合证据失效，剩余 PR 由人工确认依赖并合并。")
                return
            try:
                self.github.validate_pr(pr, value)
            except ReviewError:
                self.store.handoff(run['id'], f"PR 已变化、关闭或不再符合已验证的交付身份：{pr['url']}；需人工核对最新提交与目标分支。")
                return
            names = {label['name'] for label in value.get('labels', [])}
            holds = names & (set(self.policy['hold_labels']) | {'aot-no-hotfix', 'lead-review', 'economy'})
            if holds:
                self.store.handoff(run['id'], f"PR {pr['url']} 存在禁止自动合并的人工交接标签："
                                   + '、'.join(sorted(holds)) + '；请人工审查并决定是否合并。')
                return
            if self.github.blockers(pr):
                self.store.handoff(run['id'], f"PR {pr['url']} 存在人工要求修改、未解决的人工讨论或无法确认讨论者身份的阻塞；需人工在 GitHub 处理后审查与合并。")
                return
            feedback = self.github.bot_feedback(pr)
            self.db.execute('UPDATE review_prs SET bot_feedback=? WHERE id=?', (encoded(feedback), pr['id']))
            reasons = risk(self.github.files(pr), self.policy['repositories'][pr['repository']])
            if reasons:
                self.store.handoff(run['id'], f"仓库 {pr['repository']}（{pr['url']}）：\n" + '\n'.join(reasons))
                return
            snapshot = self.candidate(pr)
            if snapshot:
                actual = commit(self.github.target(pr))
                allowed = {snapshot['base']}
                # Only confirmed predecessor results preserve already-tested speculative evidence.
                for row in self.db.execute('WITH RECURSIVE predecessors(id) AS '
                        '(SELECT predecessor FROM review_edges WHERE successor=? UNION '
                        'SELECT e.predecessor FROM review_edges e JOIN predecessors p ON e.successor=p.id) '
                        "SELECT snapshot FROM review_candidates WHERE id IN (SELECT id FROM predecessors) AND state='merged'",
                        (snapshot['id'],)):
                    earlier = json.loads(row['snapshot'])
                    if earlier['repository'] == pr['repository']:
                        allowed.add(earlier['commit'])
                if actual not in allowed:
                    with self.ledger._transaction():
                        self.store.invalidate(snapshot['id'], 'Target branch changed outside this candidate prefix')

    def frontier(self, pr):
        rows = [p for p in self.prs(active_only=True) if p['repository'] == pr['repository'] and p['target'] == pr['target']
                and p['state'] not in ('merged', 'delivered')
                and self.store.current(self.store.row('review_runs', p['run_id']))]
        return rows, next((i for i, p in enumerate(rows) if p['id'] == pr['id']), -1)

    def advance(self, pr):
        pr = self.store.row('review_prs', pr['id'])
        if not self.store.current(self.store.row('review_runs', pr['run_id'])):
            return
        policy = self.policy['repositories'][pr['repository']]
        if pr['state'] == 'queued':
            if pr['rebuilds'] > self.policy['retry_limit'] * self.policy['depth']:
                self.store.handoff(pr['run_id'], f"PR {pr['url']} 的整合候选已重建 {pr['rebuilds']} 次，超过恢复上限；需人工协调并行 PR 的基线与合并顺序。")
                return
            queue, position = self.frontier(pr)
            if position < 0 or position >= self.policy['depth']:
                return
            predecessor = self.candidate(queue[position - 1]) if position else None
            if position and (not predecessor or predecessor['conflicts'] or queue[position - 1]['state'] == 'repair'):
                return
            metadata = self.github.pr(pr)
            self.github.validate_pr(pr, metadata)
            snapshot = self.git.prepare(pr, metadata, predecessor)
            snapshot['policy'] = self.run_policy(pr['run_id'])
            snapshot['checks'] = policy['checks']
            # Record the complete delivery identity even when its other candidates are not ready yet.
            snapshot['delivery'] = [{k: p[k] for k in ('repository', 'url', 'source_head')} for p in self.prs(pr['run_id'])]
            snapshot = self.store.candidate(pr['id'], snapshot, [predecessor['id']] if predecessor else [])
            pr = self.store.row('review_prs', pr['id'])
            if snapshot['conflicts']:
                self.need_repair(pr, '整合候选存在合并冲突')
                return
        snapshot = self.candidate(pr)
        if not snapshot or snapshot['conflicts'] or pr['state'] == 'repair':
            return
        reasons = risk(self.git.files(snapshot), policy)
        if reasons:
            self.store.handoff(pr['run_id'], f"仓库 {pr['repository']}（{pr['url']}）的整合候选：\n" + '\n'.join(reasons))
            return
        record = self.store.row('review_candidates', snapshot['id'])
        if record['verdict'] and not self.store.inputs_current(pr['run_id'], record['review_inputs']):
            self.db.execute("UPDATE review_prs SET state='reviewing' WHERE id=?", (pr['id'],))
            # Retain the old report and its findings for the new reviewer's explicit
            # responses, while refusing to use it as a current verdict.
            record['verdict'] = None
        if record['verdict'] and json.loads(record['verdict'])['risk'] == 'high':
            self.high_risk(pr, json.loads(record['verdict']))
            return
        if self.writes and record['state'] == 'prepared':
            # Each ref is immutable and unique. Crash retries publish the identical commit.
            self.git.publish_candidate(snapshot)
            self.github.dispatch(snapshot, policy)
            self.db.execute("UPDATE review_candidates SET state='ci' WHERE id=?", (snapshot['id'],))
        ci = self.checks(pr, snapshot, snapshot['commit'], policy['checks'])
        self.db.execute('UPDATE review_candidates SET ci=? WHERE id=?', (encoded(ci), snapshot['id']))
        if self.store.row('review_runs', pr['run_id'])['state'] in ('delivered', 'superseded'):
            return
        if ci['state'] == 'failed':
            self.need_repair(pr, '整合候选的必需 CI 检查失败')
            return
        if not record['verdict']:
            return
        verdict = json.loads(record['verdict'])
        if verdict['risk'] == 'high':
            self.high_risk(pr, verdict)
        elif any(f['severity'] == 'important' for f in verdict['findings']):
            self.need_repair(pr, '独立验证确认的重要缺陷尚未解决')
        else:
            source_ci = self.checks(pr, snapshot, pr['source_head'], policy['source_checks'])
            if self.store.row('review_runs', pr['run_id'])['state'] in ('delivered', 'superseded'):
                return
            if source_ci['state'] == 'failed':
                self.need_repair(pr, '源 PR 的必需 CI 检查失败')
                return
            self.db.execute('UPDATE review_prs SET state=?,error=? WHERE id=?',
                ('ready' if ci['state'] == source_ci['state'] == 'passed' else 'waiting_ci',
                 'Backend remains shadow-only' if policy['backend'] != 'git_ff' else '', pr['id']))

    def checks(self, pr, snapshot, sha, required):
        if not required:
            return {'state': 'passed', 'sha': sha, 'checks': [], 'exception': 'configured evidence_only'}
        ci = self.github.checks(pr['repository'], sha, required)
        now = self.ledger.clock()
        self.db.execute('INSERT OR IGNORE INTO review_ci_recovery(candidate_id,sha,started_at) VALUES(?,?,?)',
                        (snapshot['id'], sha, now))
        recovery = self.db.execute('SELECT * FROM review_ci_recovery WHERE candidate_id=? AND sha=?',
                                   (snapshot['id'], sha)).fetchone()
        if ci['state'] in ('waiting', 'unavailable') and now - recovery['started_at'] >= self.policy['ci_timeout_seconds']:
            self.store.handoff(pr['run_id'], f"PR {pr['url']} 的必需 CI 等待已超过 {self.policy['ci_timeout_seconds']} 秒。"
                               f"提交：`{sha}`；需人工检查工作流触发、检查名称与 App 配置。未完成不能视为通过。")
        elif ci['state'] == 'unavailable' and self.writes and now >= recovery['retry_at']:
            if recovery['attempts'] >= self.policy['retry_limit']:
                self.store.handoff(pr['run_id'], f"PR {pr['url']} 的必需 CI 在 {recovery['attempts']} 次工作流重试后仍不可用；"
                                   f"提交：`{sha}`。需人工排查 CI 服务或工作流配置。")
            else:
                # Journal before the remote call. Lost responses retain the cooldown and budget.
                self.db.execute('UPDATE review_ci_recovery SET attempts=attempts+1,retry_at=? WHERE candidate_id=? AND sha=?',
                                (now + max(300, self.policy['poll_seconds']), snapshot['id'], sha))
                try:
                    self.github.rerun(pr['repository'], sha, ci)
                except ReviewError as exc:
                    self.store.handoff(pr['run_id'], f"PR {pr['url']} 的 CI 无法由控制器安全重试；需人工检查工作流与检查来源。技术详情：{exc}")
                except Exception as exc:
                    self.db.execute('UPDATE review_ci_recovery SET error=? WHERE candidate_id=? AND sha=?',
                                    (type(exc).__name__, snapshot['id'], sha))
        return ci

    def need_repair(self, pr, reason):
        if pr['state'] == 'repair':
            return
        if pr['repairs'] >= self.policy['repair_limit']:
            self.store.handoff(pr['run_id'], f"PR {pr['url']}：{reason}；已完成 {pr['repairs']} 轮 AI 修复，达到配置上限 "
                               f"{self.policy['repair_limit']} 轮。需人工继续诊断、修改并验证。")
            return
        if not self.writes:
            self.db.execute("UPDATE review_prs SET state='shadow_repair',error=? WHERE id=?", (reason, pr['id']))
            return
        with self.ledger._transaction():
            # The current candidate survives as the repair input, but nobody may use its old verdict.
            for row in self.db.execute('SELECT successor FROM review_edges WHERE predecessor=?', (pr['candidate_id'],)).fetchall():
                self.store.invalidate(row['successor'], 'Predecessor requires repair')
            self.db.execute("UPDATE review_attempts SET state='stopping',token_hash=NULL WHERE candidate_id=? "
                            "AND state IN ('queued','running','submitted')", (pr['candidate_id'],))
            self.db.execute("UPDATE review_prs SET state='repair',error=? WHERE id=?", (reason, pr['id']))

    def completed(self, attempt):
        record = self.store.row('review_candidates', attempt['candidate_id'])
        pr = self.store.row('review_prs', record['pr_id'])
        if pr['candidate_id'] != record['id'] or not self.store.current(self.store.row('review_runs', pr['run_id'])):
            return
        if not attempt['result']:
            self.retry(pr, 'Worker exited without a valid current report', phase='worker')
            return
        result, snapshot = json.loads(attempt['result']), json.loads(record['snapshot'])
        if attempt['role'] == 'review':
            if pr['state'] != 'repair' and self.store.inputs_current(pr['run_id'], attempt['review_inputs']):
                self.db.execute('UPDATE review_candidates SET verdict=?,review_inputs=? WHERE id=?',
                                (encoded(result), attempt['review_inputs'], record['id']))
                self.recovered(pr, 'worker')
                if result['risk'] == 'high':
                    self.high_risk(pr, result)
            return
        if result['needs_person']:
            self.store.handoff(pr['run_id'], f"AI 修复需要人工协助：{pr['repository']}（{pr['url']}）。\n\n" + result['summary'])
            return
        replacement = self.git.repaired(snapshot, attempt['id'], result['head'])
        if record['verdict']:
            replacement['open_findings'] = [f for f in json.loads(record['verdict'])['findings'] if f['severity'] == 'important']
        with self.ledger._transaction():
            self.store.invalidate(snapshot['id'], 'AI repair changed integrated candidate')
            self.db.execute('UPDATE review_prs SET repairs=repairs+1 WHERE id=?', (pr['id'],))
        for key in ('id', 'revision', 'predecessors', 'pr_id'):
            replacement.pop(key, None)
        self.store.candidate(pr['id'], replacement, snapshot['predecessors'])
        self.recovered(pr, 'worker')

    def all_reviewed(self, run):
        for pr in self.prs(run['id']):
            if self.writes and self.policy['repositories'][pr['repository']]['backend'] != 'git_ff':
                return False
            if pr['state'] == 'merged':
                continue
            if pr['state'] != 'ready' or not pr['candidate_id']:
                return False
            record = self.store.row('review_candidates', pr['candidate_id'])
            if not record['verdict'] or not record['ci'] or not self.store.inputs_current(run['id'], record['review_inputs']):
                return False
            verdict = json.loads(record['verdict'])
            if verdict['risk'] != 'routine' or any(f['severity'] == 'important' for f in verdict['findings']):
                return False
            if json.loads(record['ci'])['state'] != 'passed':
                return False
        return True

    def pins(self, run, *, merging=None):
        prs = {p['repository']: p for p in self.prs(run['id'])}
        for repo, pr in prs.items():
            pin = self.policy['repositories'][repo]['pin']
            snapshot = self.candidate(pr)
            if not pin or not snapshot:
                continue
            content = self.git.text(repo, snapshot['commit'], pin['path'])
            values = dict(line.split('=', 1) for line in content.splitlines() if '=' in line and not line.lstrip().startswith('#'))
            sha = commit(values.get(pin['key'], '').strip().strip('"\''))
            dependency = prs.get(pin['repository'])
            if dependency:
                commits = self.github.commits(dependency)
                if sha not in commits:
                    raise ReviewError(f"{repo} 的依赖锁定文件 {pin['path']} 未指向本次交付中 {pin['repository']} PR 的提交；需人工核对版本配套关系。")
            if merging == repo or not dependency:
                base = self.github.default_head(pin['repository'])
                self.git.worktrees.fetch(pin['repository'])
                if not self.git.ancestor(pin['repository'], sha, base):
                    raise ReviewError(f"{repo} 锁定的依赖提交 {sha} 尚未合入 {pin['repository']} 默认分支；需人工确认依赖版本与合并顺序。")

    def merge(self, pr):
        pr = self.store.row('review_prs', pr['id'])
        if pr['state'] != 'ready' or not pr['candidate_id']:
            return
        policy = self.policy['repositories'][pr['repository']]
        if policy['backend'] != 'git_ff' or not self.writes or pr['repository'] not in self.authorized:
            return  # Never approximate a native queue with the unfenced ordinary merge API.
        run = self.store.row('review_runs', pr['run_id'])
        if not self.all_reviewed(run) or not self.live_issue(run):
            return
        queue, position = self.frontier(pr)
        if position != 0:
            return
        if any(p['state'] != 'merged' for p in self.prs(run['id']) if p['repository'] in policy['dependencies']):
            return
        # Fresh authority is required for irreversible promotion, even with cached polling.
        self.github.authority(pr['repository'], {'version': 1, 'instance': self.config.instance_id,
                              'app': self.api.app_user_id, 'controller': self.store.identity()})
        try:
            self.pins(run, merging=pr['repository'])
        except ReviewError as exc:
            self.store.handoff(run['id'], '跨仓库依赖版本验证未通过；需人工核对锁定文件与配套提交。详情：' + str(exc))
            return
        snapshot = self.candidate(pr)
        for predecessor in snapshot['predecessors']:
            if self.store.row('review_candidates', predecessor)['state'] != 'merged':
                return
        metadata = self.github.pr(pr)
        self.github.validate_pr(pr, metadata)
        if self.github.protected(pr):
            self.store.handoff(run['id'], f"PR {pr['url']} 的目标分支受到保护，当前精确提交推送后端不能绕过保护规则；需人工按仓库规则合并。")
            return
        if self.github.blockers(pr) or {v['name'] for v in metadata.get('labels', [])} & (
                set(self.policy['hold_labels']) | {'aot-no-hotfix', 'lead-review', 'economy'}):
            self.store.handoff(run['id'], f"PR {pr['url']} 在合并前出现人工审查阻塞或禁止自动合并的标签；需人工处理并决定是否合并。")
            return
        actual = self.github.target(pr)
        if actual != snapshot['prefix']:
            with self.ledger._transaction():
                self.store.invalidate(snapshot['id'], 'Merge frontier changed')
            return
        # The source PR may report conflicts that the independently reviewed
        # candidate has resolved. Atomic promotion publishes that exact resolution.
        if metadata.get('draft'):
            self.github.ready(pr, metadata)
            metadata = self.github.pr(pr)
        if metadata.get('draft') or (metadata.get('mergeable') is not True and not snapshot.get('repair_parent')):
            raise ReviewError('GitHub has not confirmed ready and mergeable source PR metadata')
        ci = self.github.checks(pr['repository'], snapshot['commit'], policy['checks']) if policy['checks'] else {'state': 'passed'}
        if ci['state'] != 'passed':
            return
        if policy['source_checks'] and self.github.checks(pr['repository'], pr['source_head'], policy['source_checks'])['state'] != 'passed':
            return
        # Re-read every delivery PR and its issue after potentially slow calls, then fence locally.
        self.refresh(run)
        pr = self.store.row('review_prs', pr['id'])
        if pr['state'] != 'ready' or pr['candidate_id'] != snapshot['id'] or not self.all_reviewed(self.store.row('review_runs', run['id'])):
            return
        with self.ledger._transaction():
            if Maintenance(self.ledger).requested() or self.db.execute('SELECT paused FROM review_control').fetchone()[0]:
                return
            if not self.store.current(self.store.row('review_runs', run['id'])):
                return
            now = self.ledger.clock()
            self.db.execute("INSERT OR IGNORE INTO review_merges(candidate_id,state,request,created_at,updated_at) VALUES(?,'pending',?,?,?)",
                (snapshot['id'], encoded({'base': actual, 'head': snapshot['head'], 'commit': snapshot['commit'], 'method': 'merge'}), now, now))
            self.db.execute("UPDATE review_prs SET state='merging' WHERE id=?", (pr['id'],))
        self.git.merge_exact(snapshot, actual)
        self.reconcile_merge(pr)

    def reconcile_merge(self, pr):
        snapshot = self.candidate(pr)
        actual = self.github.target(pr)
        self.git.worktrees.fetch(pr['repository'])
        if not self.git.ancestor(pr['repository'], snapshot['commit'], actual):
            if actual != snapshot['prefix']:
                with self.ledger._transaction():
                    self.db.execute("UPDATE review_merges SET state='rejected',updated_at=? WHERE candidate_id=?",
                                    (self.ledger.clock(), snapshot['id']))
                    self.store.invalidate(snapshot['id'], 'Concurrent target update rejected exact merge')
            else:
                self.db.execute("UPDATE review_prs SET state='ready' WHERE id=?", (pr['id'],))
            return
        value = self.github.pr(pr)
        if not value.get('merged'):
            # Target landed; GitHub can take time to recognize the PR. Never push
            # again, but an unresolved outcome must eventually reach an operator.
            intent = self.db.execute('SELECT created_at FROM review_merges WHERE candidate_id=?', (snapshot['id'],)).fetchone()
            if (intent and self.ledger.clock() >= pr['retry_at']
                    and self.ledger.clock() - intent['created_at'] >= min(300, self.policy['poll_seconds'] * 2)):
                self.retry(pr, 'Target contains candidate but GitHub has not confirmed the PR merge', phase='merge')
            return
        with self.ledger._transaction():
            self.db.execute("UPDATE review_merges SET state='confirmed',merge_sha=?,response=?,updated_at=? WHERE candidate_id=?",
                (snapshot['commit'], encoded({'merged': True, 'target': actual}), self.ledger.clock(), snapshot['id']))
            self.db.execute("UPDATE review_candidates SET state='merged' WHERE id=?", (snapshot['id'],))
            self.db.execute("UPDATE review_prs SET state='merged',error='' WHERE id=?", (pr['id'],))
            self.recovered(pr, 'merge')
            self.ledger._audit(self.store.row('review_runs', pr['run_id'])['item_id'], 'review_merge_confirmed',
                               details={'url': pr['url'], 'candidate': snapshot['id'], 'commit': snapshot['commit']})
            if all(p['state'] == 'merged' for p in self.prs(pr['run_id'])):
                self.db.execute("UPDATE review_runs SET state='delivered',disposition='auto_merged',updated_at=? WHERE id=?",
                                (self.ledger.clock(), pr['run_id']))

    def notification_body(self, run):
        prs = self.prs(run['id'])
        handoff = run['disposition'] != 'auto_merged'
        body = 'FarmBot 已交付：' + ('交由人工审查与合并。' if handoff else '已自动合并。')
        reason = run['reason']
        if reason.startswith('sensitive path: '):
            reason = ('历史路径门禁触发人工交接，文件：`' + reason.removeprefix('sensitive path: ')
                      + '`。这是路径规则判断，不能据此认定实际改动存在缺陷。')
        if reason:
            body += '\n\n交接原因：\n' + reason
        if handoff:
            if len(prs) > 1:
                body += '\n\n以上原因使本次关联交付停止自动合并；所有未合并 PR 一并交由人工处理，避免仅交付部分配套改动。'
            body += '\n\n后续由人工审查、修改并合并。本轮自动化已结束；通过审查或 CI 转绿不会自动恢复。'
        for pr in prs:
            state = '已合并' if pr['state'] == 'merged' else '已交付人工处理'
            body += f"\n\n{pr['repository']}：{pr['url']}（{state}）"
            attempts = self.db.execute("SELECT count(*) FROM review_attempts a JOIN review_candidates c ON c.id=a.candidate_id "
                                       "WHERE c.pr_id=? AND a.role='review'", (pr['id'],)).fetchone()[0]
            reports = self.db.execute('SELECT count(*) FROM review_candidates WHERE pr_id=? AND verdict IS NOT NULL',
                                      (pr['id'],)).fetchone()[0]
            body += f"\n独立 AI 审查尝试：{attempts} 次；已接收审查报告：{reports} 份；AI 修复轮数：{pr['repairs']}。"
            if not attempts:
                body += '\n尚未进入独立 AI 审查；本仓库不能视为已通过审查。'
            elif not reports:
                body += '\n尚未收到可用的独立审查结论；不能视为已通过审查。'
            seen = set()
            for history in self.history(pr):
                if history['verdict'] and history['id'] not in seen:
                    seen.add(history['id'])
                    verdict = json.loads(history['verdict'])
                    body += '\n\n保留的审查摘要：' + verdict['summary']
                    for finding in verdict['findings']:
                        if finding['severity'] == 'important':
                            body += f"\n- {finding['path']}:{finding['line']}：{finding['claim']}（依据：{finding['evidence']}）"
            candidate = self.candidate(pr)
            if candidate:
                body += '\n\n保留候选：`' + candidate['commit'] + '`；基线：`' + candidate['base'] + '`。'
                ci = self.store.row('review_candidates', candidate['id'])['ci']
                if ci:
                    body += '\nCI 记录：`' + ci + '`'
        return body[:48000]

    def notifications(self):
        for row in self.db.execute("SELECT * FROM review_runs WHERE state IN ('delivered','superseded') AND notified=0").fetchall():
            run = dict(row)
            try:
                marker = '<!-- farmbot-review:' + run['id'] + ' -->'
                body = self.notification_body(run)
                for pr in self.prs(run['id']):
                    self.github.notify(pr, marker, body, handoff=run['disposition'] != 'auto_merged' and pr['state'] != 'merged')
                self.linear_notice(run, body)
                self.db.execute('UPDATE review_runs SET notified=1 WHERE id=?', (run['id'],))
            except Exception:
                continue  # Durable idempotency markers reconcile an uncertain notification response.

    def linear_notice(self, run, body):
        """Controller notice uses the existing outbox exclusion from issue fingerprints."""
        item = self.ledger.item(run['item_id'])
        request_id = 'review-' + run['id'][:48]
        marker = '[farmbot:' + digest(['review-notice', run['id']]) + ']'
        with self.ledger._transaction():
            self.db.execute('INSERT OR IGNORE INTO notices(item_id,request_id,issue_id,kind,marker,body,created_at) VALUES(?,?,?,?,?,?,?)',
                (run['item_id'], request_id, item['issue_id'], 'review', marker, body + '\n\n' + marker, self.ledger.clock()))
        notice = self.ledger.notice(run['item_id'], request_id)
        if notice['remote_id']:
            return
        issue = self.api.fetch_issue(item['issue_id'])
        if not issue.get('comments_complete'):
            raise ReviewError('Cannot reconcile review notification')
        existing = next((c for c in issue.get('comments', []) if marker in c.get('body', '') and c.get('author_kind') == 'bot'), None)
        remote_id = existing['id'] if existing else self.api.create_comment(item['issue_id'], notice['body'])
        self.ledger.confirm_notice(run['item_id'], request_id, remote_id)

    def close(self):
        if getattr(self, 'retention', None):
            self.retention.close()
        self.workers.close()
        self.ledger.close()

    def observe_incidents(self):
        """Read-only signals; ambiguous transports never count as a regression."""
        cutoff = self.ledger.clock() - self.policy['circuit_days'] * 86400
        for raw in self.db.execute('SELECT DISTINCT r.* FROM review_runs r JOIN review_prs p ON p.run_id=r.id '
                'JOIN review_candidates c ON c.pr_id=p.id JOIN review_merges m ON m.candidate_id=c.id '
                'LEFT JOIN review_observations o ON o.run_id=r.id '
                "WHERE m.state='confirmed' AND m.updated_at>=? AND COALESCE(o.checked_at,0)<=? "
                'ORDER BY COALESCE(o.checked_at,0),r.id LIMIT 10', (cutoff, self.ledger.clock() - 300)).fetchall():
            run = dict(raw)
            old = self.db.execute('SELECT * FROM review_observations WHERE run_id=?', (run['id'],)).fetchone()
            if old and old['checked_at'] > self.ledger.clock() - 300:
                continue
            try:
                issue = self.api.fetch_issue(self.ledger.item(run['item_id'])['issue_id'])
                completed = issue['status_type'] == 'completed'
                if old and old['completed_seen'] and issue['status_type'] in ('started', 'unstarted', 'backlog'):
                    self.store.incident('reopen:' + run['id'], run['id'], 'reopen', self.policy)
                self.db.execute('INSERT INTO review_observations VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
                    'completed_seen=max(completed_seen,excluded.completed_seen),checked_at=excluded.checked_at',
                    (run['id'], int(completed), self.ledger.clock()))
                for pr in self.prs(run['id']):
                    snapshot = self.candidate(pr)
                    if snapshot and self.db.execute("SELECT 1 FROM review_merges WHERE candidate_id=? AND state='confirmed'",
                                                     (snapshot['id'],)).fetchone():
                        for event in self.github.reverts(pr['repository'], snapshot['commit'], cutoff):
                            self.store.incident(event, run['id'], 'revert', self.policy)
            except Exception:
                continue
