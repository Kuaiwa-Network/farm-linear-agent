"""Review progress joins real ledger fixtures without opening a writable reader."""
import json

from test_monitor_view import ViewBase, NOW, beat


class ReviewProgressTests(ViewBase):
    def review(self, states=('reviewing',), *, role='review', attempt='running'):
        item = self.job(skill='work', description='PRIVATE issue body')
        self.sql("UPDATE work_items SET state='delivered' WHERE id=?", item['id'])
        run = 'run-' + str(self.count)
        fp = self.ledger.connection.execute('SELECT fingerprint FROM issues WHERE id=?', (item['issue_id'],)).fetchone()[0]
        self.sql('INSERT INTO review_runs(id,item_id,generation,fingerprint,policy,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                 run, item['id'], item['generation'], fp, 'policy', NOW-120, NOW-20)
        for index, state in enumerate(states):
            pr, candidate = f'{run}-pr-{index}', f'{run}-candidate-{index}'
            self.sql('INSERT INTO review_prs(id,run_id,repository,url,target,source_head,source_branch,state) VALUES(?,?,?,?,?,?,?,?)',
                     pr, run, 'Farm-Client', f'https://github.com/o/client/pull/{self.count * 100 + index}',
                     'main', 'a'*40, 'branch', state)
            self.sql('INSERT INTO review_candidates(id,pr_id,snapshot,ci,created_at) VALUES(?,?,?,?,?)',
                     candidate, pr, json.dumps({'commit': 'b'*40, 'secret': 'PRIVATE snapshot'}),
                     json.dumps({'state': 'waiting', 'diagnostic': 'PRIVATE CI output'}), NOW-100)
            self.sql('UPDATE review_prs SET candidate_id=? WHERE id=?', candidate, pr)
            if attempt:
                self.sql('INSERT INTO review_attempts(id,candidate_id,role,state,token_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                         candidate+'-attempt', candidate, role, attempt, 'PRIVATE claim', NOW-90, NOW-10)
        return run, item

    def test_multi_repository_review_is_one_task_in_combined_counts_and_contains_issue_identity(self):
        self.review(('reviewing', 'waiting_ci', 'merged'))
        work = self.job()
        self.claim(work)
        document = self.status()
        self.assertEqual(document['counts']['running'], 2)
        reviews = [job for job in document['active'] if job.get('kind') == 'review']
        self.assertEqual(len(reviews), 1)
        self.assertEqual((reviews[0]['identifier'], reviews[0]['title'], reviews[0]['url']),
                         ('FARM-1', '标题 1', 'https://linear.app/k/issue/FARM-1'))
        progress = reviews[0]['review']
        self.assertEqual((progress['phase'], progress['total'], progress['merged']), ('review_running', 3, 1))
        self.assertNotIn('PRIVATE', json.dumps(document))

    def test_ci_wait_is_visible_without_counting_a_worker_or_running_task(self):
        self.review(('waiting_ci', 'ready'), attempt='processed')
        document = self.status()
        self.assertEqual(document['counts']['waiting_ci'], 1)
        self.assertEqual(document['counts']['running'], 0)
        self.assertEqual(self.active(document, 'FARM-1')['review']['phase'], 'waiting_ci')
        self.assertIsNone(self.active(document, 'FARM-1')['worker'])

    def test_repair_role_startup_report_and_retry_are_distinguished(self):
        run, _ = self.review(('repair',), role='repair', attempt='queued')
        for attempt, phase, count in (('queued', 'repair_starting', 'queued'),
                                       ('running', 'repair_running', 'running'),
                                       ('submitted', 'repair_finishing', 'running'),
                                       ('settled', 'report_pending', 'running'),
                                       ('processed', 'repair_queued', 'queued')):
            self.sql('UPDATE review_attempts SET state=?', attempt)
            document = self.status()
            self.assertEqual(self.active(document, 'FARM-1')['review']['phase'], phase)
            self.assertEqual(document['counts'][count], 1)
        self.sql('UPDATE review_prs SET retry_at=?,repairs=2', NOW+300)
        self.sql('UPDATE review_attempts SET role=\'review\',result=?', json.dumps({'secret': 'PRIVATE report'}))
        job = self.active(self.status(), 'FARM-1')
        self.assertEqual(job['review']['phase'], 'retry_wait')
        self.assertEqual((job['review']['reports'], job['review']['repairs']), (1, 2))
        self.assertEqual(job['review']['prs'][0]['retry_at'], NOW+300)
        self.assertNotIn('PRIVATE report', json.dumps(job))

    def test_current_candidate_only_terminal_runs_and_old_generations_are_not_active(self):
        run, item = self.review(('waiting_ci',), attempt='processed')
        pr = run+'-pr-0'
        self.sql('INSERT INTO review_candidates(id,pr_id,snapshot,created_at) VALUES(?,?,?,?)', 'old', pr, '{}', NOW-200)
        self.sql('INSERT INTO review_attempts(id,candidate_id,role,state,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                 'old-attempt', 'old', 'repair', 'running', NOW-2, NOW-1)
        self.assertEqual(self.active(self.status(), 'FARM-1')['review']['phase'], 'waiting_ci')
        for state in ('delivered', 'superseded'):
            self.sql('UPDATE review_runs SET state=? WHERE id=?', state, run)
            self.assertEqual(self.status()['active'], [])
        self.sql("UPDATE review_runs SET state='queued',generation=generation-1")
        self.assertEqual(self.status()['active'], [])

    def test_review_pause_and_stale_heartbeat_never_claim_a_live_worker(self):
        self.review(('reviewing',), attempt='queued')
        self.sql('UPDATE review_control SET paused=1')
        job = self.active(self.status(), 'FARM-1')
        self.assertEqual((job['review']['phase'], job['state']), ('paused', 'queued'))
        self.sql("UPDATE review_attempts SET state='running'")
        job = self.active(self.status(heartbeat=('stale', beat())), 'FARM-1')
        self.assertEqual(job['review']['phase'], 'review_running')
        self.assertTrue(job['review']['stale'])
        self.assertTrue(job['review']['paused'])
        self.assertIsNone(job['review']['prs'][0]['tracked'])

    def test_active_reviews_are_not_lost_behind_the_fifty_pr_history_limit(self):
        self.review(('waiting_ci',)*51, attempt=None)
        document = self.status()
        self.assertEqual(len(document['review']['prs']), 50)
        self.assertEqual(len(document['active'][0]['review']['prs']), 51)
        self.assertEqual(document['counts']['waiting_ci'], 1)

    def test_an_implementation_continuation_replaces_its_old_review_in_active_list(self):
        run, item = self.review()
        self.sql("UPDATE work_items SET state='queued' WHERE id=?", item['id'])
        document = self.status()
        self.assertEqual(len(document['active']), 1)
        self.assertNotIn('review', document['active'][0])
        self.assertEqual(document['counts']['queued'], 1)

    def test_withdrawn_or_changed_source_is_not_reported_as_an_active_review(self):
        _, item = self.review()
        self.sql('UPDATE work_items SET withdraw_deadline=? WHERE id=?', NOW+60, item['id'])
        self.assertEqual(self.status()['active'], [])
        self.sql('UPDATE work_items SET withdraw_deadline=NULL WHERE id=?', item['id'])
        self.sql("UPDATE issues SET fingerprint='new-source'")
        self.assertEqual(self.status()['active'], [])
