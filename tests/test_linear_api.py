import hashlib
import http.client
import io
import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from unittest.mock import patch

from agent import ledger as ledger_module
from agent import linear_api as linear_api_module
from agent.config import StubLinear
from agent.linear_api import UPLOAD_TIMEOUT
from agent.linear_api import (ISSUE_QUERY, LINEAR_URL, LinearAPI, TooLarge, UploadError, person, strip_signed,
                              upload_opener, upload_urls)

APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"

# Fictional people as Linear User nodes. The fake transport adds an email FarmBot never asks for.
DESIGNER = {"id": "20000000-0000-4000-8000-000000000001", "name": "Designer One",
            "url": "https://linear.app/example/profiles/designer-one", "email": "designer.one@example.com"}
OWNER = {"id": "20000000-0000-4000-8000-000000000002", "name": "Owner Two",
         "url": "https://linear.app/example/profiles/owner-two", "email": "owner.two@example.com"}
UNSIGNED = "https://uploads.linear.app/o/a/mockup"
SIGNED = UNSIGNED + "?signature=eyJhbGciOiJIUzI1NiJ9.e30.s-_1&expires=9"


def as_person(node):
    return {key: node[key] for key in ("id", "name", "url")}


class FakeHTTP:
    """Answers the token endpoint, then GraphQL by operation name."""
    def __init__(self, graphql_answers):
        self.answers = graphql_answers
        self.calls = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data) if req.get_header("Content-type") == "application/json" else req.data.decode()
        self.calls.append((req.full_url, req.headers, body))
        if req.full_url.endswith("/oauth/token"):
            return io.BytesIO(json.dumps({"access_token": "tok", "expires_in": 3600}).encode())
        name = body["query"].split("{")[0].split()[1].split("(")[0]
        answer = self.answers[name].pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def issue_page(cursor, has_next, comments, **fields):
    """One FarmBotIssue page. `fields` add or replace issue fields, such as labels, assignee and creator."""
    issue = {
        "id": "10000000-0000-4000-8000-000000000001", "identifier": "FARM-1", "url": "https://linear.app/k/issue/FARM-1",
        "branchName": "farmbot/farm-1", "title": "T", "description": "see https://uploads.linear.app/a/b/c?signature=xyz", "priority": 2,
        "archivedAt": None, "state": {"name": "Todo", "type": "unstarted"}, "team": {"id": "9676b5f9-eff3-485b-80ed-900ed137e21a"},
        "labels": {"nodes": [{"name": "Bug"}]}, "attachments": {"nodes": [{"url": "https://github.com/o/r/pull/1"}]},
        "delegate": {"id": APP},
        "comments": {"nodes": comments, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}
    issue.update(fields)
    return {"data": {"issue": issue}}


def comment_node(id, user=None, *, bot=False, parent=None, **fields):
    """One Comment node. `fields` add or replace node fields, such as url."""
    node = {"id": id, "url": f"https://linear.app/example/issue/FARM-1/t#comment-{id}", "body": f"text {id}",
            "createdAt": "2026-09-18T01:00:00.000Z", "updatedAt": "2026-09-18T01:00:00.000Z", "user": user,
            "botActor": {"id": APP} if bot else None, "parent": {"id": parent} if parent else None}
    node.update(fields)
    return node


class LinearAPITests(unittest.TestCase):
    def test_recovery_webhook_candidate_requires_its_exact_issue_app_and_id(self):
        session = {"id": "session", "issue": {"id": "issue"}, "appUser": {"id": APP}}
        api = self.api({"FarmBotRecoveryCandidate": [{"data": {"agentSession": session}}]})
        self.assertEqual(api.recovery_session_candidate("session", "issue", APP), session)
        for changed in ({"id": "other"}, {"issue": {"id": "other"}}, {"appUser": {"id": "other"}}):
            api = self.api({"FarmBotRecoveryCandidate": [{"data": {"agentSession": {**session, **changed}}}]})
            with self.assertRaises(RuntimeError):
                api.recovery_session_candidate("session", "issue", APP)

    def test_stub_session_creation_can_be_recovered_after_client_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            api = StubLinear(directory)
            session = api.create_session_on_issue("issue", APP, "https://linear.app/example#recovery")
            restarted = StubLinear(directory)
            self.assertEqual(restarted.find_recovery_session("issue", APP, "https://linear.app/example#recovery"), session)
            self.assertIsNone(restarted.find_recovery_session("other", APP, "https://linear.app/example#recovery"))

    def test_session_creation_checks_issue_app_and_recovery_marker(self):
        marker = "https://linear.app/example/issue/FARM-1#farmbot-recovery-test"
        session = {"id": "new-session", "issue": {"id": "issue"}, "appUser": {"id": APP},
                   "sourceComment": None, "creator": None, "archivedAt": None,
                   "externalLinks": [{"label": "Issue", "url": marker}]}
        api = self.api({"FarmBotOpenSession": [{"data": {"agentSessionCreateOnIssue": {
            "success": True, "agentSession": session}}}]})
        self.assertEqual(api.create_session_on_issue("issue", APP, marker), session)
        self.assertEqual(self.http.calls[-1][2]["variables"], {"input": {
            "issueId": "issue", "externalUrls": [{"label": "Issue", "url": marker}]}})
        for changes in ({"issue": {"id": "other"}}, {"appUser": {"id": "other"}},
                        {"sourceComment": {"id": "mention"}}, {"externalLinks": []},
                        {"archivedAt": "2026-10-01T00:00:00Z"}, {"id": None}):
            with self.subTest(changes=changes):
                api = self.api({"FarmBotOpenSession": [{"data": {"agentSessionCreateOnIssue": {
                    "success": True, "agentSession": {**session, **changes}}}}]})
                with self.assertRaises(RuntimeError):
                    api.create_session_on_issue("issue", APP, marker)

    def test_recover_session_paginates_and_requires_one_matching_own_session(self):
        marker = "https://linear.app/example/issue/FARM-1#farmbot-recovery-test"
        session = {"id": "new-session", "issue": {"id": "issue"}, "appUser": {"id": APP},
                   "sourceComment": None, "archivedAt": None, "externalLinks": [{"url": marker}]}
        def page(nodes, next_page=False):
            return {"data": {"issue": {"id": "issue", "agentSessions": {
                "nodes": nodes, "pageInfo": {"hasNextPage": next_page, "endCursor": "next"}}}}}
        api = self.api({"FarmBotRecoverSession": [page([], True), page([session])]})
        self.assertEqual(api.find_recovery_session("issue", APP, marker), session)
        self.assertEqual(self.http.calls[-1][2]["variables"], {"id": "issue", "after": "next"})
        for nodes in ([], [{**session, "appUser": {"id": "other"}}]):
            api = self.api({"FarmBotRecoverSession": [page(nodes)]})
            self.assertIsNone(api.find_recovery_session("issue", APP, marker))
        api = self.api({"FarmBotRecoverSession": [page([session, {**session, "id": "second"}])]})
        with self.assertRaises(RuntimeError):
            api.find_recovery_session("issue", APP, marker)

    def test_artificial_session_root_requires_matching_context_and_no_source_comment(self):
        for root, source, expected in ((True, None, True), (False, None, False),
                                       (True, {"id": "human"}, False), (None, None, False)):
            with self.subTest(root=root, source=source):
                api = self.api({"FarmBotSessionOrigin": [{"data": {"agentSession": {
                    "issue": {"id": "issue"}, "appUser": {"id": APP},
                    "comment": {"isArtificialAgentSessionRoot": root}, "sourceComment": source}}}]})
                self.assertIs(api.session_has_artificial_root("session", "issue", APP), expected)
                request = self.http.calls[-1][2]
                self.assertEqual(request["variables"], {"id": "session"})
                self.assertIn("isArtificialAgentSessionRoot", request["query"])
                self.assertIn("sourceComment", request["query"])

    def test_session_origin_rejects_missing_or_mismatched_session_context(self):
        for session in (None, {"issue": {"id": "other"}, "appUser": {"id": APP}},
                        {"issue": {"id": "issue"}, "appUser": {"id": "other"}}):
            with self.subTest(session=session):
                api = self.api({"FarmBotSessionOrigin": [{"data": {"agentSession": session}}]})
                with self.assertRaises(RuntimeError):
                    api.session_has_artificial_root("session", "issue", APP)

    def session_state(self, session):
        return self.api({"FarmBotSessionState": [{"data": {"agentSession": session}}]})

    def test_session_state_reads_status_and_archive_and_accepts_unknown_values(self):
        """Silent-delegation design §3.6 (TN1): the state of one of FarmBot's own threads, as Linear sends it. Linear
        adds status values (`stopping` came on 2026-09-24), so any value passes; only the caller decides which ones
        mean closed. A session of another issue or app, or one with no status, is not answered."""
        for status, archived_at, archived in (("awaitingInput", None, False), ("complete", None, False),
                                              ("error", "2026-09-30T01:00:00.000Z", True),
                                              ("active", "2026-09-30T01:00:00.000Z", True),
                                              ("stopping", None, False), ("somethingNew", None, False)):
            with self.subTest(status=status, archived_at=archived_at):
                api = self.session_state({"status": status, "archivedAt": archived_at, "issue": {"id": "issue"},
                                          "appUser": {"id": APP}})
                self.assertEqual(api.session_state("session", "issue", APP), {"status": status, "archived": archived})
                request = self.http.calls[-1][2]
                self.assertEqual(request["variables"], {"id": "session"})
                self.assertIn("agentSession(id: $id)", request["query"])
                for field in ("status", "archivedAt", "issue { id }", "appUser { id }"):
                    self.assertIn(field, request["query"])
        for session in (None, {"status": "complete", "archivedAt": None, "issue": {"id": "other"},
                               "appUser": {"id": APP}},
                        {"status": "complete", "archivedAt": None, "issue": {"id": "issue"},
                         "appUser": {"id": "other"}},
                        {"status": "complete", "archivedAt": None, "issue": None, "appUser": None}):
            with self.subTest(session=session), self.assertRaisesRegex(RuntimeError, "context mismatch"):
                self.session_state(session).session_state("session", "issue", APP)
        for status in (None, "", 7):
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, "no status"):
                self.session_state({"status": status, "archivedAt": None, "issue": {"id": "issue"},
                                    "appUser": {"id": APP}}).session_state("session", "issue", APP)
        with self.assertRaisesRegex(RuntimeError, "no status"):
            self.session_state({"archivedAt": None, "issue": {"id": "issue"},
                                "appUser": {"id": APP}}).session_state("session", "issue", APP)

    def test_the_session_state_read_needs_no_scope_farmbot_does_not_have(self):
        """§1.4: a scope change would revoke every token of the app, so the read must work with today's."""
        self.assertEqual(linear_api_module.SCOPES, "read,write,app:mentionable,app:assignable")

    def test_needs_more_info_adds_only_matching_label_without_replacing_labels(self):
        api = self.api({
            "FarmBotInfoLabel": [{"data": {"issue": {"team": {"id": "team"}, "labels": {"nodes": [{"name": "Bug"}]}},
                "issueLabels": {"nodes": [{"id": "wrong", "team": {"id": "other"}}, {"id": "right", "team": {"id": "team"}}],
                                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}],
            "FarmBotAddLabel": [{"data": {"issueAddLabel": {"success": True}}}]})
        api.needs_more_info("issue")
        sent = self.http.calls[-1][2]
        self.assertEqual(sent["variables"], {"id": "issue", "label": "right"})
        self.assertIn("issueAddLabel", sent["query"])

    def test_needs_more_info_is_idempotent(self):
        api = self.api({"FarmBotInfoLabel": [{"data": {
            "issue": {"team": {"id": "team"}, "labels": {"nodes": [{"name": "needs-more-info"}]}},
            "issueLabels": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}]})
        api.needs_more_info("issue")
        self.assertEqual(len(self.http.calls), 2)  # authenticate and read; no mutation

    def test_needs_more_info_creates_missing_team_label_and_requires_confirmation(self):
        api = self.api({"FarmBotInfoLabel": [{"data": {
            "issue": {"team": {"id": "team"}, "labels": {"nodes": []}},
            "issueLabels": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}],
            "FarmBotCreateInfoLabel": [{"data": {"issueLabelCreate": {"success": True, "issueLabel": {"id": "new"}}}}],
            "FarmBotAddLabel": [{"data": {"issueAddLabel": {"success": False}}}]})
        with self.assertRaises(RuntimeError):
            api.needs_more_info("issue")
        self.assertEqual(self.http.calls[-2][2]["variables"]["input"], {"name": "needs-more-info", "teamId": "team"})

    def api(self, answers):
        self.http = FakeHTTP(answers)
        return LinearAPI("client", "secret", request=self.http)

    def fetched(self, comments, **fields):
        """fetch_issue over one page whose issue carries `fields`."""
        api = self.api({"FarmBotIdentity": [{"data": {"viewer": {"id": APP, "name": "FarmBot"},
                                                      "organization": {"id": "org", "name": "K"}}}],
                        "FarmBotIssue": [issue_page(None, False, comments, **fields)]})
        return api.fetch_issue("FARM-1")

    def test_lightweight_status_has_version_archive_and_delegate(self):
        api = self.api({"FarmBotIssueStatus": [{"data": {"issue": {
            "id": "issue", "updatedAt": "2026-09-21T00:00:00Z", "archivedAt": None,
            "state": {"name": "Done", "type": "completed"}, "delegate": {"id": APP}}}}]})
        value = api.issue_status("issue")
        self.assertEqual((value["updated_at"], value["status_type"], value["delegate_id"], value["archived"]),
                         ("2026-09-21T00:00:00Z", "completed", APP, False))
        self.assertNotIn("comments", self.http.calls[-1][2]["query"])
        self.assertIn("trashed", self.http.calls[-1][2]["query"])  # a deleted issue reads as archived (design E3)

    def test_e3_trashed_issue_reads_as_archived(self):
        """Withdrawn-work design E3, U7: a deleted issue is trashed before it goes, which closes it like an archive;
        an issue Linear no longer returns at all is `not_found`, the only kind that counts toward unreachable."""
        from agent.linear_api import LinearError
        status = {"id": "issue", "updatedAt": "2026-09-21T00:00:00Z", "archivedAt": None,
                  "state": {"name": "Todo", "type": "unstarted"}, "delegate": {"id": APP}}
        api = self.api({"FarmBotIssueStatus": [{"data": {"issue": {**status, "trashed": True}}},
                                               {"data": {"issue": {**status, "trashed": None}}},
                                               {"data": {"issue": None}}]})
        self.assertIs(api.issue_status("issue")["archived"], True)
        self.assertIs(api.issue_status("issue")["archived"], False)
        with self.assertRaises(LinearError) as caught:
            api.issue_status("issue")
        self.assertEqual(caught.exception.kind, "not_found")

    def test_e6_ratelimited_http_400_is_classified_transient(self):
        """Design E6, K7: over the rate limit Linear answers HTTP 400 with RATELIMITED, which is transient, never the
        not-found an unreachable issue needs."""
        from agent.linear_api import LinearError
        body = json.dumps({"errors": [{"message": "Rate limit exceeded",
                                       "extensions": {"code": "RATELIMITED"}}]}).encode()
        limited = urllib.error.HTTPError("https://api.linear.app/graphql", 400, "Bad Request", {}, io.BytesIO(body))
        api = self.api({"FarmBotIssueStatus": [limited]})
        with self.assertRaises(LinearError) as caught:
            api.issue_status("issue")
        self.assertEqual((caught.exception.kind, str(caught.exception)),
                         ("ratelimited", "Linear GraphQL rejected the request"))
        self.assertIsInstance(caught.exception, RuntimeError)  # callers that catch RuntimeError are unchanged
        other = urllib.error.HTTPError("https://api.linear.app/graphql", 400, "Bad Request", {}, io.BytesIO(b"{}"))
        api = self.api({"FarmBotIssueStatus": [other]})
        with self.assertRaises(urllib.error.HTTPError):
            api.issue_status("issue")

    def test_graphql_errors_are_classified_by_code_then_by_a_not_found_message(self):
        """Only an error Linear names as not found can count toward an unreachable issue (design R8, P8); an
        unrecognised one is `rejected`, which never cancels anything."""
        from agent.linear_api import LinearError
        cases = (({"message": "x", "extensions": {"code": "RATELIMITED"}}, "ratelimited"),
                 ({"message": "Entity not found", "extensions": {"code": "FORBIDDEN"}}, "forbidden"),
                 ({"message": "x", "extensions": {"code": "AUTHENTICATION_ERROR"}}, "auth"),
                 ({"message": "Entity not found: Issue", "extensions": {"code": "INVALID_INPUT"}}, "not_found"),
                 ({"message": "Something else went wrong"}, "rejected"),
                 ("not an object", "rejected"))
        for error, kind in cases:
            with self.subTest(error=error):
                api = self.api({"FarmBotIssueStatus": [{"errors": [error], "data": {"issue": None}}]})
                with self.assertRaises(LinearError) as caught:
                    api.issue_status("issue")
                self.assertEqual(caught.exception.kind, kind)
        api = self.api({"FarmBotIssueStatus": [{"data": None}]})
        with self.assertRaises(LinearError) as caught:
            api.issue_status("issue")
        self.assertEqual(caught.exception.kind, "rejected")

    def test_only_a_successful_call_records_when_linear_last_answered(self):
        """R8 counts not-found reads only while the host's other calls succeed: `last_success_at` says when one did."""
        from agent.linear_api import LinearError
        status = {"id": "issue", "updatedAt": "2026-09-21T00:00:00Z", "archivedAt": None, "trashed": False,
                  "state": {"name": "Todo", "type": "unstarted"}, "delegate": None}
        api = self.api({"FarmBotIssueStatus": [{"errors": [{"message": "Entity not found"}], "data": None},
                                               {"data": {"issue": status}}]})
        self.assertEqual(api.last_success_at, 0)
        with patch("agent.linear_api.time.time", return_value=5000.0), self.assertRaises(LinearError):
            api.issue_status("issue")
        self.assertEqual(api.last_success_at, 0)
        with patch("agent.linear_api.time.time", return_value=6000.0):
            api.issue_status("issue")
        self.assertEqual(api.last_success_at, 6000.0)

    def test_identity_requires_expected_name_and_bearer_token(self):
        api = self.api({"FarmBotIdentity": [{"data": {"viewer": {"id": APP, "name": "FarmBot"}, "organization": {"id": "org", "name": "K"}}}]})
        self.assertEqual(api.identity()["viewer"]["id"], APP)
        self.assertEqual(self.http.calls[1][1]["Authorization"], "Bearer tok")
        bad = self.api({"FarmBotIdentity": [{"data": {"viewer": {"id": "x", "name": "FarmQA"}, "organization": {"id": "org", "name": "K"}}}]})
        with self.assertRaises(RuntimeError):
            bad.identity()

    def test_graphql_errors_are_failures_even_on_http_200(self):
        api = self.api({"FarmBotIdentity": [{"errors": [{"message": "nope"}], "data": None}]})
        with self.assertRaises(RuntimeError):
            api.identity()

    def test_create_activity_and_comment_return_ids(self):
        api = self.api({"FarmBotActivity": [{"data": {"agentActivityCreate": {"success": True, "agentActivity": {"id": "act-1"}}}}],
                        "FarmBotComment": [{"data": {"commentCreate": {"success": True, "comment": {"id": "com-1"}}}}]})
        result = api.create_activity("session-1", {"type": "thought", "body": "收到"}, activity_id="ack-1")
        self.assertEqual(result["agentActivity"]["id"], "act-1")
        sent = self.http.calls[1][2]["variables"]["input"]
        self.assertEqual((sent["id"], sent["agentSessionId"], sent["content"]["type"]), ("ack-1", "session-1", "thought"))
        self.assertEqual(api.create_comment("10000000-0000-4000-8000-000000000001", "已开始处理"), "com-1")

    def test_fetch_issue_paginates_and_normalizes(self):
        first = [{"id": "c1", "body": "human text", "createdAt": "2026-09-18T01:00:00.000Z", "updatedAt": "2026-09-18T01:00:00.000Z",
                  "user": {"id": "u1"}, "botActor": None}]
        second = [{"id": "c2", "body": "bot text", "createdAt": "2026-09-18T02:00:00.000Z", "updatedAt": "2026-09-18T02:00:00.000Z",
                   "user": None, "botActor": {"id": APP}}]
        api = self.api({"FarmBotIdentity": [{"data": {"viewer": {"id": APP, "name": "FarmBot"}, "organization": {"id": "org", "name": "K"}}}],
                        "FarmBotIssue": [issue_page("cur", True, first), issue_page(None, False, second)]})
        issue = api.fetch_issue("FARM-1")
        self.assertEqual(issue["identifier"], "FARM-1")
        self.assertEqual(issue["status_type"], "unstarted")
        self.assertEqual(issue["labels"], ["Bug"])
        self.assertEqual(issue["delegate_id"], APP)
        self.assertEqual(issue["description"], "see https://uploads.linear.app/a/b/c")
        self.assertEqual([c["author_kind"] for c in issue["comments"]], ["human", "bot"])
        self.assertTrue(issue["detail_complete"] and issue["comments_complete"])
        self.assertEqual(self.http.calls[3][2]["variables"]["after"], "cur")

    def test_fetch_issue_classifies_own_user_comments_as_bot_without_prior_identity_call(self):
        own = [{"id": "c9", "body": "FarmBot says", "createdAt": "2026-09-18T03:00:00.000Z", "updatedAt": "2026-09-18T03:00:00.000Z",
                "user": {"id": APP}, "botActor": None}]
        api = self.api({"FarmBotIdentity": [{"data": {"viewer": {"id": APP, "name": "FarmBot"}, "organization": {"id": "org", "name": "K"}}}],
                        "FarmBotIssue": [issue_page(None, False, own)]})
        issue = api.fetch_issue("FARM-1")
        self.assertEqual([c["author_kind"] for c in issue["comments"]], ["bot"])
        self.assertEqual([c[0].rsplit("/", 1)[-1] for c in self.http.calls][:2], ["token", "graphql"])

    def test_fetch_issue_reads_a_trashed_issue_as_archived(self):
        """Withdrawn-work design E3, U7, as `issue_status`: an issue in the trash is closed like an archived one, in the
        full read too. A settle decides on that read (silent-delegation design S14), so it drops the episode of a card
        deleted during the grace instead of keeping or telling its work."""
        self.assertIn("archivedAt trashed", " ".join(ISSUE_QUERY.split()))
        for trashed, archived in ((True, True), (None, False), (False, False)):
            with self.subTest(trashed=trashed):
                self.assertIs(self.fetched([], trashed=trashed)["archived"], archived)
        self.assertIs(self.fetched([], archivedAt="2026-09-30T00:00:00Z", trashed=None)["archived"], True)

    def test_fetch_issue_says_not_found_for_an_issue_linear_no_longer_returns(self):
        """Withdrawn-work design E3, as `issue_status`: a read that returns no issue is a `not_found` LinearError, the
        one kind that counts toward a card out of reach, whichever read met it."""
        from agent.linear_api import LinearError
        api = self.api({"FarmBotIssue": [{"data": {"issue": None}}]})
        api.app_user_id, api.token, api.expires = APP, "tok", float("inf")
        with self.assertRaises(LinearError) as caught:
            api.fetch_issue("FARM-1")
        self.assertEqual((caught.exception.kind, str(caught.exception)), ("not_found", "Issue not found"))

    def test_fetch_issue_retries_only_the_timed_out_page(self):
        api = self.api({"FarmBotIssue": [issue_page("next", True, []),
                                           urllib.error.URLError(TimeoutError("read timed out")),
                                           issue_page(None, False, [])]})
        api.app_user_id, api.token, api.expires = APP, "tok", float("inf")
        with patch("agent.linear_api.time.sleep") as sleep:
            self.assertEqual(api.fetch_issue("FARM-1")["identifier"], "FARM-1")
        self.assertEqual([call[2]["variables"]["after"] for call in self.http.calls], [None, "next", "next"])
        sleep.assert_called_once_with(0.25)

    def test_fetch_issue_stops_after_three_timeouts(self):
        api = self.api({"FarmBotIssue": [TimeoutError("slow")] * 3})
        api.app_user_id, api.token, api.expires = APP, "tok", float("inf")
        with patch("agent.linear_api.time.sleep") as sleep:
            with self.assertRaises(TimeoutError):
                api.fetch_issue("FARM-1")
        self.assertEqual(len(self.http.calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_fetch_issue_does_not_retry_unrelated_network_errors(self):
        api = self.api({"FarmBotIssue": [urllib.error.URLError("certificate failure")]})
        api.app_user_id, api.token, api.expires = APP, "tok", float("inf")
        with patch("agent.linear_api.time.sleep") as sleep:
            with self.assertRaises(urllib.error.URLError):
                api.fetch_issue("FARM-1")
        self.assertEqual(len(self.http.calls), 1)
        sleep.assert_not_called()

    def test_strip_signed_removes_upload_query_only(self):
        self.assertEqual(strip_signed("https://uploads.linear.app/a/b?signature=1&x=2"), "https://uploads.linear.app/a/b")
        self.assertEqual(strip_signed("https://github.com/o/r/pull/1?x=1"), "https://github.com/o/r/pull/1?x=1")

    def test_the_issue_query_reads_people_label_parents_and_reply_parents(self):
        query = " ".join(ISSUE_QUERY.split())
        for part in ("labels { nodes { name parent { id name } } }", "assignee { id name url app }",
                     "creator { id name url app }",
                     "nodes { id url body createdAt updatedAt user { id name url app } botActor { id } parent { id } }"):
            self.assertIn(part, query)
        self.assertNotIn("email", query)
        self.fetched([])
        self.assertEqual(self.http.calls[-1][2]["query"], ISSUE_QUERY)

    def test_fetch_issue_keeps_people_as_id_name_and_url_and_replies_as_parent_ids(self):
        issue = self.fetched([comment_node("c1", DESIGNER), comment_node("c2", OWNER, parent="c1"),
                              comment_node("c3", {"id": APP}, parent="c2"), comment_node("c4", bot=True),
                              comment_node("c5")],
                             assignee=OWNER, creator=DESIGNER)
        self.assertEqual((issue["assignee"], issue["creator"]), (as_person(OWNER), as_person(DESIGNER)))
        self.assertEqual([(c["id"], c["author_kind"], c["author"], c["parent_id"]) for c in issue["comments"]],
                         [("c1", "human", as_person(DESIGNER), None), ("c2", "human", as_person(OWNER), "c1"),
                          ("c3", "bot", None, "c2"), ("c4", "bot", None, None), ("c5", "unknown", None, None)])
        self.assertNotIn("email", json.dumps(issue))
        self.assertNotIn("@example.com", json.dumps(issue))

    def test_each_comment_keeps_its_own_linear_url_or_none(self):
        issue = self.fetched([comment_node("c1", DESIGNER), comment_node("c2", bot=True),
                              comment_node("c3", OWNER, url="https://example.com/issue/FARM-1#comment-c3"),
                              comment_node("c4", url="https://linear.app/example/issue/FARM-1 t"),
                              comment_node("c5", url=None)])
        self.assertEqual([c["url"] for c in issue["comments"]],
                         ["https://linear.app/example/issue/FARM-1/t#comment-c1",
                          "https://linear.app/example/issue/FARM-1/t#comment-c2", None, None, None])

    def test_a_user_without_a_uuid_a_name_or_a_linear_profile_url_is_null(self):
        good = as_person(DESIGNER)
        self.assertEqual(person(DESIGNER), good)
        for node in (None, "Designer One", {"id": good["id"], "url": good["url"]}, {**good, "name": " "},
                     {**good, "id": "designer-one"}, {**good, "id": None},
                     {**good, "url": "https://example.com/profiles/designer-one"},
                     {**good, "url": "http://linear.app/example/profiles/designer-one"},
                     {**good, "url": "https://linear.app.example.com/profiles/designer-one"},
                     {**good, "url": "https://linear.app/example/profiles/designer one"}):
            with self.subTest(node=node):
                self.assertIsNone(person(node))
        issue = self.fetched([comment_node("c1", {**DESIGNER, "url": "https://example.com/u/designer-one"})],
                             creator=None)
        self.assertEqual((issue["assignee"], issue["creator"]), (None, None))
        self.assertEqual((issue["comments"][0]["author_kind"], issue["comments"][0]["author"]), ("human", None))

    def test_a_name_is_trimmed_and_one_that_could_hide_text_or_hold_an_email_names_nobody(self):
        good = as_person(DESIGNER)
        self.assertEqual(person({**DESIGNER, "name": "  Designer One\t"}), good)
        # The joiners are not hidden: emoji sequences use U+200D, Persian and Indic names U+200C.
        for name in ("主策 👩‍🎨", "Designer\u200cOne", "x" * 256, "Ünïcødé Näme 名字 🏆"):
            with self.subTest(name=name):
                self.assertEqual(person({**DESIGNER, "name": name})["name"], name)
        # One representative of each family the rule refuses, written as an escape so that a review can see it:
        # C0 and C1 controls, bidirectional marks and embeddings/isolates, zero-width and invisible format characters
        # (a tag character carries invisible ASCII text), the BOM, private use, line and paragraph separators, and
        # an unpaired surrogate.
        for name in (7, "x" * 257, "Designer\nOne", "Designer\x00One", "Designer\x1b[31mOne", "Designer\x85One",
                     "Designer\u061cOne", "Designer\u200eOne", "Designer\u202aOne", "Designer \u202eenO",
                     "Designer\u2066One", "Designer\u200bOne", "Designer\u2060One", "Designer\u00adOne",
                     "Designer\U000e0049One", "Designer\ufeffOne", "Designer\ue000One", "Designer\u2028One",
                     "Designer\u2029One", "Designer \ud800", "designer.one@example.com",
                     "Designer One <designer.one@example.com>"):
            with self.subTest(name=name):
                self.assertIsNone(person({**DESIGNER, "name": name}))

    def test_an_app_user_is_never_a_person_whichever_app_it_is(self):
        """Linear marks every app user with User.app: FarmBot, TestBot, Codex and its own integration user (checked
        live on 2026-09-27), and sends their comments with a `user` and no `botActor`. Such a comment is a bot's
        with no author, and an app is never the assignee or the creator. A node without the key, as an older read
        or a webhook payload gives, is still judged by its id."""
        codex = {"id": "20000000-0000-4000-8000-0000000000aa", "name": "Codex",
                 "url": "https://linear.app/example/profiles/codex", "app": True}
        self.assertIsNone(person(codex))
        self.assertEqual(person({**DESIGNER, "app": False}), as_person(DESIGNER))
        issue = self.fetched([comment_node("c1", codex), comment_node("c2", {**DESIGNER, "app": False}),
                              comment_node("c3", {**OWNER, "app": True})],
                             assignee=codex, creator={**DESIGNER, "app": True})
        self.assertEqual([(c["author_kind"], c["author"]) for c in issue["comments"]],
                         [("bot", None), ("human", as_person(DESIGNER)), ("bot", None)])
        self.assertEqual((issue["assignee"], issue["creator"]), (None, None))

    def test_the_own_app_user_is_recognised_whatever_case_linear_gives_its_id(self):
        farmbot = {"id": APP.upper(), "name": "FarmBot", "url": "https://linear.app/example/profiles/farmbot"}
        api = self.api({"FarmBotIdentity": [{"data": {"viewer": {"id": APP.upper(), "name": "FarmBot"},
                                                      "organization": {"id": "org", "name": "K"}}}],
                        "FarmBotIssue": [issue_page(None, False, [comment_node("c1", farmbot), comment_node("c2", OWNER)],
                                                    assignee=farmbot, creator=farmbot)]})
        issue = api.fetch_issue("FARM-1")
        self.assertEqual((issue["assignee"], issue["creator"]), (None, None))
        self.assertEqual([(c["author_kind"], c["author"]) for c in issue["comments"]],
                         [("bot", None), ("human", as_person(OWNER))])

    def test_only_a_human_comment_has_an_author_and_farmbot_is_never_a_person_on_the_issue(self):
        # Linear reports FarmBot's own comments with its app user, which has a name and a profile URL too.
        farmbot = {"id": APP, "name": "FarmBot", "url": "https://linear.app/example/profiles/farmbot"}
        issue = self.fetched([comment_node("c1", farmbot), comment_node("c2", DESIGNER, bot=True),
                              comment_node("c3", OWNER)], assignee=farmbot, creator=farmbot)
        self.assertEqual([(c["author_kind"], c["author"]) for c in issue["comments"]],
                         [("bot", None), ("bot", None), ("human", as_person(OWNER))])
        self.assertEqual((issue["assignee"], issue["creator"]), (None, None))
        with tempfile.TemporaryDirectory() as tmp:  # and the ledger accepts what the read produced
            ledger = ledger_module.Ledger(Path(tmp) / "ledger.sqlite3")
            try:
                ledger.observe_issue(issue)
            finally:
                ledger.close()

    def test_label_groups_pair_each_grouped_label_with_its_parent(self):
        self.assertEqual(self.fetched([])["label_groups"], [])
        issue = self.fetched([], labels={"nodes": [
            {"name": "Bug", "parent": None}, {"name": "UI", "parent": {"id": "label-1", "name": "功能"}},
            {"name": "Android", "parent": {"id": "label-2", "name": "平台"}}, {"name": "程序"}]})
        self.assertEqual(issue["labels"], ["Bug", "UI", "Android", "程序"])
        self.assertEqual(issue["label_groups"], [{"group": "功能", "label": "UI"}, {"group": "平台", "label": "Android"}])
        # Sorted and distinct whatever order Linear returns; a parent without a usable name is no group.
        issue = self.fetched([], labels={"nodes": [
            {"name": "iOS", "parent": {"id": "label-2", "name": "平台"}}, {"name": "Code", "parent": {"id": "label-1", "name": "功能"}},
            {"name": "iOS", "parent": {"id": "label-2", "name": "平台"}}, {"name": "A", "parent": {"id": "label-3", "name": " "}},
            {"name": "B", "parent": {"id": "label-4"}}, {"name": "C", "parent": {"id": "label-5", "name": 7}}]})
        self.assertEqual(issue["label_groups"], [{"group": "功能", "label": "Code"}, {"group": "平台", "label": "iOS"}])

    def test_strip_signed_leaves_the_markdown_and_text_around_an_upload_as_written(self):
        one, two = "https://uploads.linear.app/o/a/one", "https://uploads.linear.app/o/b/two"
        cases = {f"![效果图]({SIGNED})": f"![效果图]({UNSIGNED})",
                 f"[{SIGNED}]({SIGNED}#page)": f"[{UNSIGNED}]({UNSIGNED})",
                 f"`{SIGNED}` 截图{SIGNED}见上": f"`{UNSIGNED}` 截图{UNSIGNED}见上",
                 f"<linear-image src='{SIGNED}'>": f"<linear-image src='{UNSIGNED}'>",
                 "https://uploads.linear.app.example.com/a?signature=1": "https://uploads.linear.app.example.com/a?signature=1",
                 f"**{SIGNED}**": f"**{UNSIGNED}**",
                 f"见 {SIGNED}. 下一句": f"见 {UNSIGNED}. 下一句",
                 f"see {SIGNED}, then": f"see {UNSIGNED}, then",
                 f"{SIGNED}; 然后": f"{UNSIGNED}; 然后",
                 f"{SIGNED}: 见下": f"{UNSIGNED}: 见下",
                 f"{SIGNED}? 对吗": f"{UNSIGNED}? 对吗",
                 f"{SIGNED}! 好": f"{UNSIGNED}! 好",
                 f"见 {SIGNED}# 标题": f"见 {UNSIGNED}# 标题",
                 # Entity-escaped markup after the URL is not part of its query; an escaped "&amp;" inside it is.
                 f"<linear-image src=&quot;{SIGNED}&quot; alt=&quot;图&quot;>": f"<linear-image src=&quot;{UNSIGNED}&quot; alt=&quot;图&quot;>",
                 f"&lt;{UNSIGNED}?signature=x&amp;expires=9&gt;": f"&lt;{UNSIGNED}&gt;",
                 f"{SIGNED}&nbsp;见上": f"{UNSIGNED}&nbsp;见上",
                 f"{one}?signature=x.y,{two}?signature=z 两张图": f"{one},{two} 两张图",
                 f'<img src="{UNSIGNED}?signature=x&amp;expires=9">': f'<img src="{UNSIGNED}">',
                 f"{UNSIGNED}#page": UNSIGNED,
                 f"截图是 {UNSIGNED}? 对": f"截图是 {UNSIGNED}? 对",
                 f"见 {UNSIGNED}# 标题": f"见 {UNSIGNED}# 标题",
                 f"截图是 {UNSIGNED}?对": f"截图是 {UNSIGNED}?对"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(strip_signed(text), expected)
                resigned = text.replace("s-_1", "Zq9").replace("signature=x", "signature=Q").replace("=z", "=Z")
                self.assertEqual(strip_signed(resigned), expected)  # another signature, the same stored text
        self.assertEqual(strip_signed(None), "")

    def test_strip_signed_keeps_a_linear_image_block_valid_json(self):
        data = {"src": SIGNED, "alt": "效果图 1", "width": 640}
        for separators in ((",", ":"), (", ", ": ")):
            with self.subTest(separators=separators):
                block = json.dumps(data, ensure_ascii=False, separators=separators)
                text = strip_signed(f"<linear-image>{block}</linear-image>")
                self.assertEqual(json.loads(text.removeprefix("<linear-image>").removesuffix("</linear-image>")),
                                 {**data, "src": UNSIGNED})

    def test_upload_urls_lists_each_upload_once_and_unsigned(self):
        slices, video = "https://uploads.linear.app/o/b/slices", "https://uploads.linear.app/o/c/video"
        text = "\n".join([f"![效果图]({SIGNED}) [切图.zip]({slices}?signature=2)",
                          "<linear-image>" + json.dumps({"src": SIGNED, "alt": "效果图"}) + "</linear-image>",
                          "<linear-embed>" + json.dumps({"src": video + "?signature=3", "type": "video"}) + "</linear-embed>",
                          f"原图 {slices}. 另见{video}，谢谢",
                          "https://github.com/o/r/pull/1 https://uploads.linear.app.example.com/o/d/x"])
        self.assertEqual(upload_urls(text), [UNSIGNED, slices, video])
        self.assertEqual(upload_urls(strip_signed(text)), [UNSIGNED, slices, video])
        self.assertEqual(upload_urls(None), [])
        # Markup or a comma after an unsigned URL is not part of it.
        for text, expected in ((f"**{slices}**", [slices]), (f"{slices},{video}", [slices, video]),
                               (f"src=&quot;{slices}&quot;", [slices])):
            with self.subTest(text=text):
                self.assertEqual(upload_urls(text), expected)

    def test_a_fetched_issue_is_stored_with_its_people_label_groups_and_replies(self):
        fetched = self.fetched([comment_node("c1", DESIGNER), comment_node("c2", OWNER, parent="c1")],
                               assignee=OWNER, creator=DESIGNER,
                               labels={"nodes": [{"name": "Code", "parent": {"id": "label-1", "name": "功能"}}]})
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_module.Ledger(Path(tmp) / "ledger.sqlite3")
            try:
                ledger.observe_issue(fetched)
                stored = ledger.issue(fetched["id"])
            finally:
                ledger.close()
        for key in ("assignee", "creator", "label_groups"):
            self.assertEqual(stored[key], fetched[key])
        self.assertEqual([(c["author"], c["parent_id"], c["url"]) for c in stored["comments"]],
                         [(as_person(DESIGNER), None, "https://linear.app/example/issue/FARM-1/t#comment-c1"),
                          (as_person(OWNER), "c1", "https://linear.app/example/issue/FARM-1/t#comment-c2")])
        # The ledger keeps its own copy of the Linear URL rule, because connectors stay outside it.
        self.assertEqual(ledger_module.LINEAR_URL.pattern, LINEAR_URL.pattern)


class StubLinearTests(unittest.TestCase):
    def test_the_stub_answers_a_session_state_from_its_directory_or_a_closed_thread(self):
        """The offline double (FARMBOT_LINEAR_STUB_DIR) reads a thread's state as it reads the issue: from a file of
        its directory, `session-state.json`, which answers for every thread. Without one a thread reads complete and
        not archived, as one a response closed, so a service running on the stub closes no thread."""
        with tempfile.TemporaryDirectory() as directory:
            stub = StubLinear(directory)
            self.assertEqual(stub.session_state("session-1", "issue", stub.app_user_id),
                             {"status": "complete", "archived": False})
            (Path(directory) / "session-state.json").write_text(
                json.dumps({"status": "awaitingInput", "archived": True}), encoding="utf-8")
            self.assertEqual(stub.session_state("session-2", "issue", stub.app_user_id),
                             {"status": "awaitingInput", "archived": True})
            calls = [json.loads(line) for line in
                     (Path(directory) / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, [{"method": "session_state", "session_id": "session-1", "issue_id": "issue"},
                                     {"method": "session_state", "session_id": "session-2", "issue_id": "issue"}])


class FakeUploadServer(urllib.request.HTTPSHandler):
    """Answers HTTPS requests from a script instead of the network, and keeps every request it was sent."""

    def __init__(self, *answers):
        super().__init__()
        self.answers, self.requests = list(answers), []

    def https_open(self, req):
        self.requests.append(req)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):  # the transport itself fails
            raise answer
        status, headers, body = answer
        head = "".join(f"{name}: {value}\r\n" for name, value in headers.items()) + "\r\n"
        body = body if hasattr(body, "read") else io.BytesIO(body)
        response = urllib.response.addinfourl(body, http.client.parse_headers(io.BytesIO(head.encode())),
                                              req.full_url, status)
        response.msg = "scripted"
        return response


class RecordingBody(io.BytesIO):
    """A response body that records the size of every read it is asked for."""

    def __init__(self, data, on_read=None):
        super().__init__(data)
        self.sizes, self.on_read = [], on_read

    def read1(self, size=-1):
        self.sizes.append(size)
        if self.on_read:
            self.on_read()
        return super().read1(size)

    read = read1


class StallingBody(io.BytesIO):
    """A body whose socket times out on the first receive."""

    def read1(self, size=-1):
        raise socket.timeout("timed out reading from uploads.linear.app")

    read = read1


class UploadDownloadTests(unittest.TestCase):
    URL = "https://uploads.linear.app/7b0c6c4e-2f7a-4c55-9d0e-3a1f5e6d7c8b/0f1e2d3c/4b5a6978"
    TOKEN = "dummy-token-value"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.destination = Path(self.tmp.name) / "截图 1.png"
        tokens = iter([self.TOKEN, "dummy-token-renewed", "dummy-token-third"])
        self.token_requests = []

        def token_endpoint(req, timeout=None):
            self.token_requests.append(req.full_url)
            return io.BytesIO(json.dumps({"access_token": next(tokens), "expires_in": 3600}).encode())
        self.api = LinearAPI("client", "secret", request=token_endpoint)

    def download(self, *answers, url=None, **options):
        self.server = FakeUploadServer(*answers)
        return self.api.download_upload(url or self.URL, self.destination, opener=upload_opener(self.server),
                                        **{"max_bytes": 1 << 20, **options})

    def refused(self, *answers, **options):
        with self.assertRaises(UploadError) as caught:
            self.download(*answers, **options)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no temporary file may stay behind")
        self.assertNotIn(self.TOKEN, str(caught.exception))
        return caught.exception

    def test_the_token_goes_only_as_an_unredirected_header_and_never_into_the_result(self):
        result = self.download((200, {"Content-Type": "image/png; charset=binary", "Content-Length": "4"}, b"\x89PNG"))
        self.assertEqual(result, {"size": 4, "sha256": hashlib.sha256(b"\x89PNG").hexdigest(),
                                  "content_type": "image/png"})
        self.assertEqual(self.destination.read_bytes(), b"\x89PNG")
        request = self.server.requests[0]
        self.assertEqual(request.full_url, self.URL)
        self.assertEqual(request.unredirected_hdrs["Authorization"], f"Bearer {self.TOKEN}")
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn(self.TOKEN, json.dumps(result))

    def test_every_redirect_is_refused_naming_only_the_host_it_points_at(self):
        """Following one would fetch from wherever Linear points. If uploads.linear.app redirects to signed
        storage, the host is what the operator records (plan Task 15); the signed path and query never appear."""
        for code in (301, 302, 303, 307, 308):
            for target in ("https://collector.example/steal?signature=secret-signature",
                           "https://uploads.linear.app/o/u/other"):
                with self.subTest(code=code, target=target):
                    error = self.refused((code, {"Location": target}, b""), (200, {}, b"payload"))
                    self.assertEqual(str(error), f"redirect refused (HTTP {code} to {target.split('/')[2]})")
                    self.assertEqual([request.full_url for request in self.server.requests], [self.URL])
        self.assertEqual(str(self.refused((302, {}, b""))), "HTTP 302")  # no Location: nothing to follow

    def test_other_hosts_signed_urls_and_odd_paths_are_refused_before_any_request(self):
        for url in ("https://collector.example/a/b", "http://uploads.linear.app/a/b",
                    "https://uploads.linear.app.collector.example/a/b", "https://user@uploads.linear.app/a/b",
                    "https://uploads.linear.app:8443/a/b", f"{self.URL}?signature=abc", f"{self.URL}#x",
                    "https://uploads.linear.app/a/../b", "https://uploads.linear.app//a",
                    "https://uploads.linear.app/"):
            with self.subTest(url=url):
                error = self.refused(url=url)
                self.assertNotIn("collector.example", str(error))
                self.assertEqual(self.server.requests, [])
        self.assertEqual(self.token_requests, [])

    def test_the_cap_holds_while_streaming_and_against_a_declared_length(self):
        self.assertIsInstance(self.refused((200, {}, b"x" * 11), max_bytes=10), TooLarge)
        self.assertIsInstance(self.refused((200, {"Content-Length": "11"}, b"x" * 11), max_bytes=10), TooLarge)
        self.assertEqual(self.download((200, {}, b"x" * 10), max_bytes=10)["size"], 10)

    def test_a_short_body_an_http_error_or_a_stalled_transfer_leaves_no_file(self):
        self.assertIn("ended early", str(self.refused((200, {"Content-Length": "20"}, b"x" * 11))))
        self.assertEqual(str(self.refused((404, {}, b"not found"))), "HTTP 404")
        self.assertEqual(str(self.refused((200, {}, b"x"), deadline_seconds=0)), "timed out")

    def test_an_expired_token_is_renewed_once_then_the_refusal_is_reported(self):
        self.download((401, {}, b""), (200, {}, b"ok"))
        self.assertEqual(len(self.token_requests), 2)
        self.assertEqual(self.server.requests[1].unredirected_hdrs["Authorization"], "Bearer dummy-token-renewed")
        self.destination.unlink()
        self.assertEqual(str(self.refused((401, {}, b""), (401, {}, b""))), "HTTP 401")
        self.assertEqual(len(self.token_requests), 3)

    def test_an_expired_token_is_renewed_before_the_request(self):
        self.api.token, self.api.expires = "stale-token", 0
        self.download((200, {}, b"ok"))
        self.assertEqual(len(self.token_requests), 1)
        self.assertEqual(self.server.requests[0].unredirected_hdrs["Authorization"], f"Bearer {self.TOKEN}")

    def test_a_token_endpoint_failure_is_named_as_such_and_sends_no_request(self):
        def unreachable(req, timeout=None):
            raise urllib.error.URLError(OSError("no route to api.linear.app"))
        self.api = LinearAPI("client", "secret", request=unreachable)
        self.assertEqual(str(self.refused()), "token endpoint: network error (OSError)")

        def refusing(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b""))
        self.api = LinearAPI("client", "secret", request=refusing)
        self.assertEqual(str(self.refused()), "token endpoint refused (HTTP 401)")
        self.assertEqual(self.server.requests, [])

    def test_a_trickling_transfer_stops_at_the_deadline_not_at_its_end(self):
        """HTTPResponse.read(n) waits until n bytes arrive, each resetting the socket timeout, so the body is read
        through read1, which returns after one receive: a server sending a byte at a time is cut off at the
        deadline instead of being read to its end."""
        clock = [100.0]

        class Trickle(io.BytesIO):
            served = 0

            def read1(self, size=-1):  # one receive: a byte, a quarter second later
                clock[0] += 0.25
                self.served += 1
                return super().read(1)

            def read(self, size=-1):  # what a blocking read does: wait for all of it
                data = super().read(size)
                clock[0] += 0.25 * len(data)
                self.served += len(data)
                return data

        body = Trickle(b"x" * 40)
        with patch.object(linear_api_module.time, "monotonic", lambda: clock[0]):
            self.assertEqual(str(self.refused((200, {}, body), deadline_seconds=1)), "timed out")
        self.assertLessEqual(body.served, 5)

    def test_every_read_is_bounded_and_the_request_carries_the_timeout(self):
        body = RecordingBody(b"x" * 200_000)
        self.assertEqual(self.download((200, {}, body))["size"], 200_000)
        self.assertTrue(body.sizes and all(0 < size <= 1 << 16 for size in body.sizes), body.sizes)
        self.assertEqual(self.server.requests[0].timeout, UPLOAD_TIMEOUT)

    def test_a_declared_length_over_the_cap_is_refused_before_any_read(self):
        body = RecordingBody(b"")
        self.assertIsInstance(self.refused((200, {"Content-Length": "11"}, body), max_bytes=10), TooLarge)
        self.assertEqual(body.sizes, [])

    def test_a_network_failure_names_only_its_kind(self):
        refusal = urllib.error.URLError(ConnectionRefusedError(61, "refused by uploads.linear.app/4b5a6978"))
        for answer, message in ((refusal, "network error (ConnectionRefusedError)"),
                                (socket.timeout("uploads.linear.app timed out"), "timed out"),
                                ((200, {}, StallingBody(b"")), "timed out")):
            with self.subTest(message=message):
                error = self.refused(answer)
                self.assertEqual(str(error), message)
                self.assertNotIn("uploads.linear.app", str(error))
                self.assertNotIn("4b5a6978", str(error))

    def test_only_a_200_answer_is_a_download(self):
        self.assertEqual(str(self.refused((206, {}, b"x"))), "HTTP 206")
        self.assertEqual(str(self.refused((204, {}, b""))), "HTTP 204")

    def test_the_temporary_file_is_written_beside_the_destination_and_a_storage_failure_is_named(self):
        seen = []
        body = RecordingBody(b"ok", on_read=lambda: seen.append(sorted(p.suffix for p in self.destination.parent.iterdir())))
        self.download((200, {}, body))
        self.assertIn([".part"], seen)
        self.destination.unlink()
        self.destination = Path(self.tmp.name) / "missing" / "截图.png"
        self.assertEqual(str(self.refused((200, {}, b"ok"))), "could not store the file (FileNotFoundError)")

    def test_a_dot_segment_is_refused_however_it_is_encoded(self):
        for url in ("https://uploads.linear.app/a/./b", "https://uploads.linear.app/a/%2E%2E/b",
                    "https://uploads.linear.app/%2e%2e/x", "https://uploads.linear.app/a%2F..%2Fb"):
            with self.subTest(url=url):
                self.refused(url=url)
                self.assertEqual(self.server.requests, [])

    def test_a_long_transfer_calls_keepalive_every_minute_and_stops_when_it_raises(self):
        """A file may take the whole ten-minute deadline, and a chat worker's lease is as long, so the claim is
        renewed on the way; whatever the renewal raises ends the download and leaves no file."""
        clock = [100.0]

        class Slow(io.BytesIO):
            def read1(self, size=-1):
                data = super().read(1)
                if data:
                    clock[0] += 30  # one receive every 30 s
                return data

            read = read1

        renewals = []
        with patch.object(linear_api_module.time, "monotonic", lambda: clock[0]):
            result = self.download((200, {}, Slow(b"x" * 12)), keepalive=lambda: renewals.append(clock[0]))
            self.assertEqual((result["size"], renewals), (12, [160.0, 220.0, 280.0, 340.0, 400.0, 460.0]))
            self.destination.unlink()

            def lost():
                raise UploadError("interrupted: the claim was lost")
            error = self.refused((200, {}, Slow(b"x" * 12)), keepalive=lost)
        self.assertEqual(str(error), "interrupted: the claim was lost")

    def test_the_default_opener_refuses_redirects_too(self):
        """download-uploads passes no opener; the one built for it must refuse a redirect like the test one."""
        server = FakeUploadServer((302, {"Location": "https://collector.example/steal"}, b""), (200, {}, b"payload"))
        with patch.object(urllib.request.HTTPSHandler, "https_open", server.https_open):
            with self.assertRaises(UploadError) as caught:
                self.api.download_upload(self.URL, self.destination, max_bytes=1 << 20)
        self.assertEqual(str(caught.exception), "redirect refused (HTTP 302 to collector.example)")
        self.assertEqual([request.full_url for request in server.requests], [self.URL])
        self.assertFalse(self.destination.exists())
