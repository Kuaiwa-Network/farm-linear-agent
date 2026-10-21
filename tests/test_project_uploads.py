"""Project Overview resources use the same claimed, authenticated path in chat and work."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent import dispatch, uploads
from agent.__main__ import parser, run
from agent.execution import CONTRACT, LEGACY
from agent.ledger import LedgerError
from agent.linear_api import LinearError
from agent.skills import load_skills
from test_ledger import LedgerBase, ISSUE, SESSION, issue
from test_uploads import ART, EXTRA, SHOT, FakeUploads, UploadDirectory, archive, png

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = '30000000-0000-4000-8000-000000000001'
OTHER_PROJECT = '30000000-0000-4000-8000-000000000002'


def embed(url, name):
    return '<linear-embed node-type="file">' + json.dumps(
        {'href': url, 'name': name, 'mimetype': 'application/zip'}, ensure_ascii=False) + '</linear-embed>'


def project(content='', **changes):
    return {'id': PROJECT_ID, 'name': '公会优化', 'url': 'https://linear.app/example/project/guild',
            'description': '', 'content': content, **changes}


class ProjectDownloadTests(UploadDirectory):
    def test_embedded_archives_keep_names_extract_safely_and_reuse_the_cache(self):
        self.issue['project'] = project(embed(EXTRA + '?signature=secret', '项目 切图.zip'))
        self.api.files[EXTRA] = archive({'切图/等级.png': png(12, 24), '../outside.png': png(1, 1)})
        result = self.run_once()
        added = next(x for x in result['uploads'] if x['name'] == '项目 切图.zip')
        self.assertEqual(added['sources'], [f'project:{PROJECT_ID}:content'])
        self.assertEqual(added['members'], {'ok': 1, 'refused': 1, 'failed': 0})
        self.assertEqual((self.out / '项目 切图/切图/等级.png').read_bytes(), png(12, 24))
        self.assertFalse((self.out / 'outside.png').exists())
        self.assertNotIn('signature', json.dumps(result) + json.dumps(self.manifest()))
        self.assertEqual(self.api.calls.count(EXTRA), 1)
        self.run_once()
        self.assertEqual(self.api.calls.count(EXTRA), 1)

    def test_duplicate_upload_keeps_issue_title_and_all_sources_but_downloads_once(self):
        self.issue['project'] = project(embed(SHOT, '项目名.png'), description=f'![摘要.png]({SHOT})')
        result = self.run_once(urls=[SHOT])
        shot = result['uploads'][0]
        self.assertEqual(shot['name'], '截图 1.png')
        self.assertEqual(shot['sources'], ['description', 'comment:c1',
                         f'project:{PROJECT_ID}:description', f'project:{PROJECT_ID}:content'])
        self.assertEqual(self.api.calls, [SHOT])

    def test_removed_or_reassigned_project_cannot_authorize_cached_uploads(self):
        self.issue['project'] = project(embed(EXTRA, '切图.zip'))
        self.api.files[EXTRA] = archive({'a.png': png(1, 2)})
        self.run_once(urls=[EXTRA])
        for current in (None, project(), project(id=OTHER_PROJECT)):
            with self.subTest(project=current):
                changed = {**self.issue, 'project': current}
                with self.assertRaisesRegex(LedgerError, 'current Project Overview'):
                    self.run_once(issue=changed, urls=[EXTRA])
                self.run_once(issue=changed, urls=[])
                files = [x for x in self.manifest()['files'] if x['url_path'] == EXTRA.removeprefix('https://uploads.linear.app')]
                self.assertTrue(files)
                self.assertTrue(all(not x['on_issue'] for x in files))
        self.assertEqual(self.api.calls, [EXTRA])

    def test_links_to_other_projects_and_bot_comments_do_not_expand_sources(self):
        self.issue['project'] = project('[Other project](https://linear.app/example/project/other)',
                                       documents=[{'content': embed(EXTRA, 'unrelated.zip')}])
        self.assertNotIn(EXTRA, uploads.issue_uploads(self.issue))  # also present in a bot comment
        with self.assertRaisesRegex(LedgerError, 'current Project Overview'):
            self.run_once(urls=[EXTRA])
        self.assertEqual(self.api.calls, [])

    def test_json_image_titles_and_invalid_embeds(self):
        content = '<linear-image>' + json.dumps({'type': 'image', 'attrs': {
            'src': EXTRA, 'title': '等级 预览.png'}}) + '</linear-image>'
        self.issue['project'] = project(content)
        self.assertEqual(uploads.issue_uploads(self.issue)[EXTRA]['title'], '等级 预览.png')
        for body in ('not json', '[]', '{"attrs": null}', '{"href": 2, "name": []}'):
            with self.subTest(body=body):
                self.assertEqual(uploads._titles('<linear-embed>' + body + '</linear-embed>'), {})


class ProjectCliTests(LedgerBase):
    def owner(self, skill):
        self.raw = issue(project=project(embed(ART, '项目切图.zip')), description=f'![截图.png]({SHOT})')
        self.ledger.observe_issue(self.raw)
        self.ledger.ensure_session(SESSION, ISSUE, delegation=skill == 'work')
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill=skill,
                                            execution_contract=CONTRACT if skill == 'work' else LEGACY)
        self.item = item['id']
        self.token = self.ledger.claim(self.item, worker_id='worker')['token']
        self.token_file = Path(self.tmp.name) / 'token'
        self.token_file.write_text(self.token, encoding='utf-8')
        self.out = Path(self.tmp.name) / '状态 目录' / 'inputs'
        self.api = FakeUploads({SHOT: png(3, 4), ART: archive({'等级.png': png(5, 6)})})
        self.api.fetch_issue = Mock(side_effect=lambda _: copy.deepcopy(self.raw))
        self.args = parser().parse_args(['--db', str(self.path), 'download-uploads', '--item', self.item,
                                        '--token-file', str(self.token_file), '--out', str(self.out)])

    def check_download(self, skill):
        self.owner(skill)
        summary = run(self.args, self.ledger, lambda: self.api)
        self.assertEqual(summary['counts']['downloaded'], 2)
        self.assertEqual((self.out / '项目切图/等级.png').read_bytes(), png(5, 6))
        self.assertEqual(self.ledger.issue_context(self.item)['issue']['project'], self.raw['project'])
        self.api.fetch_issue.assert_called_once_with(ISSUE)
        self.assertNotIn(self.token, json.dumps(summary))
        self.assertEqual(run(self.args, self.ledger, lambda: self.api)['counts']['unchanged'], 2)
        # A stale ledger/cache cannot keep granting access after a live project reassignment.
        self.raw['project'] = project(id=OTHER_PROJECT)
        self.args.url = [ART]
        with self.assertRaisesRegex(LedgerError, 'current Project Overview'):
            run(self.args, self.ledger, lambda: self.api)
        self.assertEqual(self.api.calls, [SHOT, ART])

    def test_chat_downloads_issue_and_project_resources(self):
        self.check_download('chat')

    def test_work_downloads_issue_and_project_resources(self):
        self.check_download('work')

    def test_selected_project_upload_does_not_fetch_other_files(self):
        self.owner('work')
        self.args.url = [ART]
        self.assertEqual(run(self.args, self.ledger, lambda: self.api)['counts']['downloaded'], 1)
        self.assertEqual(self.api.calls, [ART])

    def test_bad_claim_cannot_read_linear_or_create_output(self):
        self.owner('chat')
        self.token_file.write_text('wrong', encoding='utf-8')
        with self.assertRaisesRegex(LedgerError, 'matching token'):
            run(self.args, self.ledger, lambda: self.api)
        self.api.fetch_issue.assert_not_called()
        self.assertEqual(self.api.calls, [])
        self.assertFalse(self.out.exists())

    def test_project_access_error_does_not_use_stored_context(self):
        self.owner('chat')
        self.api.fetch_issue.side_effect = LinearError('forbidden')
        with self.assertRaises(LinearError):
            run(self.args, self.ledger, lambda: self.api)
        self.assertEqual(self.api.calls, [])
        self.assertFalse(self.out.exists())

    def test_project_snapshot_is_optional_and_does_not_requeue_all_project_work(self):
        self.owner('work')
        original = self.ledger.observe_issue(self.raw)['fingerprint']
        current = {**self.raw, 'project': project('New project reference material', extra='discard')}
        self.assertEqual(self.ledger.observe_issue(current)['fingerprint'], original)
        self.assertNotIn('extra', self.ledger.issue(ISSUE)['project'])
        self.ledger.observe_issue({**self.raw, 'project': None})
        self.assertIsNone(self.ledger.issue(ISSUE)['project'])
        self.ledger.observe_issue(issue())
        self.assertNotIn('project', self.ledger.issue(ISSUE))

    def test_invalid_project_snapshot_is_rejected(self):
        for value in ('project-id', {}, project(id='bad'), project(content=[]), project(url='https://example.com')):
            with self.subTest(project=value), self.assertRaises(LedgerError):
                self.ledger.observe_issue(issue(project=value))


class ProjectInstructionTests(unittest.TestCase):
    def test_both_current_profiles_receive_the_same_download_authority(self):
        from agent.worker_dispatch import UPLOAD_AUTHORITY
        skills = load_skills(ROOT / 'skills')
        for skill in ('chat', 'work'):
            with self.subTest(skill=skill):
                prompt = dispatch.dispatch_message(
                    item={'id': 'uploads', 'skill': skill,
                          'execution_contract': CONTRACT if skill == 'work' else LEGACY},
                    issue={'identifier': 'FARM-1', 'url': 'https://linear.app/example/issue/FARM-1'},
                    skill_path=skills[skill].skill_md, worktrees={}, db_path='/state/db', runtime='codex',
                    guidance='', repo_root=ROOT, state_dir='/state/uploads', budget=skills[skill].budget)
                self.assertIn(UPLOAD_AUTHORITY, prompt.split('\n\n', 1)[0])
                self.assertIn('Project Overview', skills[skill].skill_md.read_text(encoding='utf-8'))
