import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.ledger import Ledger, LedgerError
from agent.publication import PublicationError, PublicationVerifier
from agent.version_targets import GROUP, LabelSync, Store, TargetController, TargetError, selection, settings
from agent.version_labels import sync
from agent.workflow import JobWorktrees
from agent.worktrees import Worktrees
from test_ledger import ISSUE, SESSION, issue
from test_worktrees import git


def selected(version='1.0', **changes):
    return issue(labels=[version], label_groups=[{'group': GROUP, 'label': version}], **changes)


class SelectionTests(unittest.TestCase):
    def test_only_target_group_selects_and_explicit_branch_overrides(self):
        self.assertEqual(selection(issue(labels=['1.0'])), {'kind': 'default'})
        self.assertEqual(selection(selected()), {'kind': 'version', 'value': '1.0'})
        self.assertEqual(selection(selected(description='Implement it.\nTarget branch: feature/new-market')),
                         {'kind': 'branch', 'value': 'feature/new-market'})

    def test_bad_or_ambiguous_selections_never_fall_back(self):
        for value in ('-main', '../main', 'refs/heads/main', 'origin/main', 'main~1', 'HEAD', 'foo.lock', '', '/main'):
            with self.subTest(value=value), self.assertRaises(TargetError):
                selection(issue(description='Target branch: ' + value))
        with self.assertRaises(TargetError):
            selection(issue(description='Target branch: main\nTarget branch: release/1.0'))
        with self.assertRaises(TargetError):
            selection(selected('1.0.1'))
        value = selected()
        value['label_groups'].append({'group': GROUP, 'label': '1.1'})
        with self.assertRaises(TargetError):
            selection(value)

    def test_settings_validate_every_repository_override(self):
        self.assertFalse(settings({}, {})['sync_labels'])
        self.assertEqual(settings({}, {})['sync_seconds'], 300)
        for value in ({'sync_labels': 1}, {'sync_labels': True}, {'sync_seconds': 0},
                      {'overrides': {'1.0': {'unknown': 'main'}}},
                      {'overrides': {'1.0': {'Farm-Client': '--all'}}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings(value, {})


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='version targets 空格 ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origins = {}
        for repo in ('Farm-Client', 'common'):
            path = self.root / repo
            path.mkdir()
            git('init', '-q', '-b', 'main', '.', cwd=path)
            (path / 'source.txt').write_text('old version', encoding='utf-8')
            git('add', '.', cwd=path)
            git('commit', '-qm', 'release start', cwd=path)
            git('branch', 'release/1.0', cwd=path)
            (path / 'next.txt').write_text('next version only', encoding='utf-8')
            git('add', '.', cwd=path)
            git('commit', '-qm', 'next version', cwd=path)
            self.origins[repo] = str(path)
        self.trees = Worktrees(self.root / 'repos', self.root / 'worktrees', self.origins)
        self.ledger = Ledger(self.root / 'ledger.sqlite3')
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(selected())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='work')
        self.controller = TargetController(self.ledger, self.trees, {})

    def prepare(self):
        return self.controller.prepare(self.ledger.item(self.item['id']), tuple(self.origins))

    def checkout(self, repo='Farm-Client', record=None):
        record = record or self.prepare()
        return self.trees.add(repo, self.item['id'], 'farmbot/farm-1', target=record['repositories'][repo])

    def test_release_checkout_is_not_main_and_records_all_repos_before_work(self):
        record = self.prepare()
        for repo, target in record['repositories'].items():
            self.assertEqual(target['branch'], 'release/1.0')
            self.assertEqual(target['commit'], git('rev-parse', 'release/1.0', cwd=self.origins[repo]))
            path = self.checkout(repo, record)
            self.assertFalse((path / 'next.txt').exists())
            self.assertEqual(self.trees.head(path), target['commit'])
            self.assertEqual(git('rev-parse', 'refs/farmbot/targets/' + self.item['id'] + '/' + target['commit'],
                                 cwd=self.trees.clone_path(repo)), target['commit'])

    def test_missing_branch_does_not_save_partial_target_or_create_worktrees(self):
        git('branch', '-D', 'release/1.0', cwd=self.origins['common'])
        with self.assertRaisesRegex(TargetError, 'common.*does not exist'):
            self.prepare()
        self.assertIsNone(Store(self.ledger).get(self.item['id']))
        self.assertFalse((self.root / 'worktrees' / self.item['id']).exists())

    def test_existing_remote_issue_branch_is_not_adopted_into_new_target(self):
        git('branch', 'farmbot/farm-1', 'main', cwd=self.origins['Farm-Client'])
        path = self.checkout()
        self.assertFalse((path / 'next.txt').exists())
        self.assertTrue(git('branch', '--show-current', cwd=path).startswith('farmbot/farm-1-'))

    def test_failed_recording_pins_do_not_block_a_later_corrected_target(self):
        with patch.object(Store, 'save', side_effect=TargetError('issue changed before save')):
            with self.assertRaises(TargetError):
                self.prepare()
        self.ledger.observe_issue(selected('1.1'))
        for origin in self.origins.values():
            git('branch', 'release/1.1', 'main', cwd=origin)
        record = self.prepare()
        self.assertEqual(record['selection']['value'], '1.1')
        self.assertTrue((self.checkout(record=record) / 'next.txt').exists())

    def test_repository_override_is_explicit_and_saved(self):
        self.controller = TargetController(self.ledger, self.trees, {'overrides': {'1.0': {'common': 'main'}}})
        record = self.prepare()
        self.assertEqual(record['repositories']['common']['branch'], 'main')
        self.assertTrue((self.checkout('common', record) / 'next.txt').exists())

    def test_restart_and_branch_advancement_keep_candidate_and_saved_target(self):
        record = self.prepare()
        path = self.checkout(record=record)
        git('commit', '--allow-empty', '-qm', 'candidate', cwd=path)
        candidate = self.trees.head(path)
        origin = self.origins['Farm-Client']
        git('switch', '-q', 'release/1.0', cwd=origin)
        git('commit', '--allow-empty', '-qm', 'later release fix', cwd=origin)
        self.ledger.close()
        self.ledger = Ledger(self.root / 'ledger.sqlite3')
        self.addCleanup(self.ledger.close)
        self.controller = TargetController(self.ledger, self.trees, {})
        self.assertEqual(self.prepare(), record)
        self.assertEqual(self.trees.head(self.checkout()), candidate)

    def test_target_change_blocks_revalidate_and_claim_but_keeps_snapshot(self):
        record = self.prepare()
        token = self.ledger.claim(self.item['id'], worker_id='test')['token']
        changed = self.ledger.observe_issue(selected('1.1'))
        with self.assertRaisesRegex(LedgerError, 'Target changed'):
            self.ledger.revalidate(self.item['id'], token, changed['fingerprint'])
        self.assertEqual(Store(self.ledger).get(self.item['id']), record)
        self.ledger.renew(self.item['id'], token)  # allow checkpoint/teardown
        self.ledger.observe_issue(selected())
        self.assertEqual(Store(self.ledger).current(self.item['id']), record)

    def test_target_change_fences_no_change_delivery_and_checkpoint_cannot_retarget(self):
        record = self.prepare()
        token = self.ledger.claim(self.item['id'], worker_id='test')['token']
        self.ledger.checkpoint(self.item['id'], token, {'summary': 'saved progress', 'branch_target': {'branch': 'main'}})
        self.assertEqual(Store(self.ledger).get(self.item['id']), record)
        self.ledger.observe_issue(selected('1.1'))
        with self.assertRaisesRegex(LedgerError, 'Target changed'):
            self.ledger.finish(self.item['id'], token, 'delivered',
                {'summary': 'no change', 'verification': 'checked', 'no_change': 'already fixed'}, check_only=True)
        self.assertEqual(self.ledger.issue_context(self.item['id'])['coordination']['branch_target'], record)

    def test_label_change_fingerprints_but_unrelated_labels_do_not(self):
        original = self.ledger.observe_issue(selected())['fingerprint']
        value = selected()
        value['labels'].append('Bug')
        self.assertEqual(self.ledger.observe_issue(value)['fingerprint'], original)
        self.assertNotEqual(self.ledger.observe_issue(selected('1.1'))['fingerprint'], original)

    def test_status_refresh_updates_target_and_older_full_snapshot_cannot_undo_it(self):
        first = selected(updated_at='2026-10-26T01:00:00Z')
        self.ledger.observe_issue(first)
        record = self.prepare()
        later = selected('1.1', updated_at='2026-10-26T02:00:00Z')
        self.ledger.apply_issue_status(later)
        self.ledger.observe_issue(first)
        with self.assertRaisesRegex(TargetError, 'Target changed'):
            Store(self.ledger).current(self.item['id'])
        self.assertEqual(Store(self.ledger).get(self.item['id']), record)

    def test_selection_change_during_resolution_leaves_no_saved_record(self):
        original = self.trees.resolve_target
        def resolve(repo, branch):
            result = original(repo, branch)
            self.ledger.observe_issue(selected('1.1'))
            return result
        with patch.object(self.trees, 'resolve_target', side_effect=resolve), self.assertRaises(TargetError):
            self.prepare()
        self.assertIsNone(Store(self.ledger).get(self.item['id']))

    def test_unrecorded_explicit_target_cannot_be_claimed(self):
        with self.assertRaisesRegex(LedgerError, 'no recorded branch target'):
            self.ledger.claim(self.item['id'], worker_id='test')

    def test_continuation_inherits_original_commits_not_latest_release(self):
        record = self.prepare()
        self.ledger.connection.execute("UPDATE work_items SET state='cancelled' WHERE id=?", (self.item['id'],))
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='work')
        self.assertEqual(self.prepare(), record)
        self.ledger.observe_issue(selected('1.1'))
        with self.assertRaises(TargetError):
            self.prepare()

    def test_changed_config_and_forged_candidate_are_refused(self):
        record = self.prepare()
        path = self.checkout(record=record)
        git('checkout', '--orphan', 'farmbot/farm-1-other', cwd=path)
        git('commit', '-qm', 'unrelated history', cwd=path)
        with self.assertRaisesRegex(TargetError, 'saved target commit'):
            self.trees.verify_target('Farm-Client', path, record['repositories']['Farm-Client'])
        self.trees.remotes['common'] = 'https://github.com/wrong/repo.git'
        with self.assertRaisesRegex(TargetError, 'configured repository'):
            self.prepare()

    def test_continuation_checks_all_repository_identities_before_touching_clones(self):
        self.prepare()
        self.ledger.connection.execute("UPDATE work_items SET state='cancelled' WHERE id=?", (self.item['id'],))
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='work')
        self.trees.remotes['common'] = 'https://github.com/wrong/repo.git'
        with patch.object(self.trees, 'retain_target', side_effect=AssertionError('must validate first')):
            with self.assertRaisesRegex(TargetError, 'configured repository'):
                self.prepare()
        self.assertIsNone(Store(self.ledger).get(self.item['id']))

    def test_publication_requires_selected_pr_base_and_preserves_remote_checks(self):
        # Operator-owned URL rewriting sends Git to a local remote. The configured
        # identity and GitHub metadata still have to agree on the real destination.
        remote = 'https://github.com/example/Farm-Client.git'
        config = self.root / 'host.gitconfig'
        local = self.origins['Farm-Client'].replace('\\', '/')
        config.write_text('[url "' + local + '"]\n\tinsteadOf = ' + remote + '\n'
                          '[url "' + remote + '"]\n\tpushInsteadOf = ' + remote + '\n', encoding='utf-8')
        self.trees.remotes['Farm-Client'] = remote
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1'}):
            record = self.prepare()
            path = self.checkout(record=record)
            git('commit', '--allow-empty', '-qm', 'release fix', cwd=path)
            head = self.trees.head(path)
            metadata = {'full_name': 'example/Farm-Client', 'html_url': remote[:-4], 'private': True,
                        'owner': {'login': 'example'}, 'permissions': {'push': True}, 'default_branch': 'main'}
            url = remote[:-4] + '/pull/1'
            pr = {'html_url': url, 'state': 'open', 'draft': True,
                  'head': {'ref': 'farmbot/farm-1', 'sha': head, 'repo': metadata},
                  'base': {'ref': 'release/1.0', 'repo': metadata}}
            change_during_lookup = [False]
            def api(endpoint, **kwargs):
                if '/branches/' in endpoint and change_during_lookup[0]:
                    self.ledger.observe_issue(selected('1.1'))
                return pr if '/pulls/' in endpoint else None if '/branches/' in endpoint else metadata
            verifier = PublicationVerifier(JobWorktrees(self.trees, self.ledger), api=api)
            result = verifier.verify('Farm-Client', self.item['id'], 'FARM-1')
            self.assertEqual(result['base_branch'], 'release/1.0')
            self.assertEqual(result['base_commit'], record['repositories']['Farm-Client']['commit'])
            # Upstream report files brought in by an intentional merge are not
            # outgoing private reports. The candidate need not include newer base
            # commits, but when it does, diff against the verified current base.
            git('switch', '-q', 'release/1.0', cwd=local)
            reports = Path(local) / 'reports'
            reports.mkdir()
            (reports / 'existing.txt').write_text('upstream record', encoding='utf-8')
            git('add', '.', cwd=local)
            git('commit', '-qm', 'upstream report', cwd=local)
            self.trees.fetch('Farm-Client')
            git('merge', '-q', '--no-edit', 'origin/release/1.0', cwd=path)
            pr['head']['sha'] = self.trees.head(path)
            self.assertEqual(verifier.verify_pr('Farm-Client', self.item['id'], 'FARM-1', url), url)
            pr['base']['ref'] = 'main'
            with self.assertRaises(PublicationError):
                verifier.verify_pr('Farm-Client', self.item['id'], 'FARM-1', url)
            self.ledger.observe_issue(selected('1.1'))
            with self.assertRaisesRegex(PublicationError, 'Target changed'):
                verifier.verify('Farm-Client', self.item['id'], 'FARM-1')
            self.ledger.observe_issue(selected())
            change_during_lookup[0] = True
            with self.assertRaisesRegex(PublicationError, 'Target changed'):
                verifier.verify('Farm-Client', self.item['id'], 'FARM-1')
            change_during_lookup[0] = False
            self.ledger.observe_issue(selected())
            git('switch', '-q', 'main', cwd=local)
            git('branch', '-D', 'release/1.0', cwd=local)
            with self.assertRaisesRegex(PublicationError, 'does not exist'):
                verifier.verify('Farm-Client', self.item['id'], 'FARM-1')


class LabelAPI:
    def __init__(self):
        self.labels, self.calls = [], []
        self.lose_create_response = False

    def graphql(self, query, variables):
        self.calls.append((query, variables))
        if 'query FarmBotVersionLabels' in query:
            return {'issueLabels': {'nodes': copy.deepcopy(self.labels), 'pageInfo': {'hasNextPage': False}}}
        if 'mutation FarmBotCreateVersionLabel' in query:
            values = variables['input']
            label = {'id': str(len(self.labels) + 1), 'team': None, 'parent': None, 'isGroup': False, **values}
            if values.get('parentId'):
                label['parent'] = {'id': values['parentId']}
            self.labels.append(label)
            if self.lose_create_response:
                self.lose_create_response = False
                raise TimeoutError('response lost after creation')
            return {'issueLabelCreate': {'success': True, 'issueLabel': {'id': label['id']}}}
        if 'mutation FarmBotUpdateVersionLabel' in query:
            next(v for v in self.labels if v['id'] == variables['id']).update(variables['input'])
            return {'issueLabelUpdate': {'success': True}}
        raise AssertionError(query)

    def sync_target_versions(self, versions):
        return sync(self, versions)


class LabelSyncTests(unittest.TestCase):
    def test_startup_and_five_minute_discovery_filter_only_major_minor_releases(self):
        class Trees:
            remotes = {'Farm-Client': 'local'}
            def remote_branches(self, repo):
                return dict.fromkeys(['main', 'release/1.0', 'release/2.1', 'release/1.0.1', 'feature/foo'])
        api = LabelAPI()
        now = [100]
        controller = LabelSync(Trees(), api, {'sync_labels': True}, clock=lambda: now[0])
        self.assertTrue(controller.tick())
        self.assertEqual({v['name'] for v in api.labels}, {GROUP, 'main', '1.0', '2.1'})
        now[0] += 299
        self.assertFalse(controller.tick())
        now[0] += 1
        count = len(api.labels)
        self.assertTrue(controller.tick())
        self.assertEqual(len(api.labels), count)

    def test_removed_branch_retains_label_and_marks_unavailable_then_restores(self):
        api = LabelAPI()
        sync(api, {'1.0': 'release/1.0'})
        label = next(v for v in api.labels if v['name'] == '1.0')
        original_id = label['id']
        sync(api, {})
        self.assertIn('Unavailable', label['description'])
        sync(api, {'1.0': 'release/1.0'})
        self.assertEqual(label['id'], original_id)
        self.assertNotIn('Unavailable', label['description'])
        self.assertFalse(any('issueUpdate' in q or 'issueLabelDelete' in q for q, _ in api.calls))

    def test_lost_response_reconciles_without_duplicate_and_keeps_human_descriptions(self):
        api = LabelAPI()
        api.lose_create_response = True
        with self.assertRaises(TimeoutError):
            sync(api, {'1.0': 'release/1.0'})
        sync(api, {'1.0': 'release/1.0'})
        self.assertEqual(len(api.labels), 2)
        api.labels[1]['description'] = 'Human explanation'
        sync(api, {})
        self.assertEqual(api.labels[1]['description'], 'Human explanation')

    def test_duplicate_group_is_refused_without_mutation(self):
        api = LabelAPI()
        sync(api, {'1.0': 'release/1.0'})
        api.labels.append({**api.labels[0], 'id': 'duplicate'})
        count = len(api.calls)
        with self.assertRaisesRegex(RuntimeError, 'unambiguous'):
            sync(api, {'1.1': 'release/1.1'})
        self.assertEqual(len(api.calls), count + 1)

    def test_only_designated_sync_instance_writes_and_failures_back_off(self):
        from unittest.mock import Mock
        trees = Mock(remotes={'Farm-Client': 'local'})
        api = LabelAPI()
        self.assertFalse(LabelSync(trees, api, {}).tick())
        trees.remote_branches.assert_not_called()
        now = [0]
        controller = LabelSync(trees, api, {'sync_labels': True}, clock=lambda: now[0])
        trees.remote_branches.side_effect = RuntimeError('remote unavailable')
        with self.assertRaises(RuntimeError):
            controller.tick()
        self.assertFalse(controller.tick())
        self.assertEqual(trees.remote_branches.call_count, 1)


if __name__ == '__main__':
    unittest.main()
