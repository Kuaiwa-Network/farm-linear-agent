import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from agent.linear_api import LinearAPI, strip_signed

APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"


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


def issue_page(cursor, has_next, comments):
    return {"data": {"issue": {
        "id": "10000000-0000-4000-8000-000000000001", "identifier": "FARM-1", "url": "https://linear.app/k/issue/FARM-1",
        "branchName": "farmbot/farm-1", "title": "T", "description": "see https://uploads.linear.app/a/b/c?signature=xyz", "priority": 2,
        "archivedAt": None, "state": {"name": "Todo", "type": "unstarted"}, "team": {"id": "9676b5f9-eff3-485b-80ed-900ed137e21a"},
        "labels": {"nodes": [{"name": "Bug"}]}, "attachments": {"nodes": [{"url": "https://github.com/o/r/pull/1"}]},
        "delegate": {"id": APP},
        "comments": {"nodes": comments, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}}}


class LinearAPITests(unittest.TestCase):
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

    def test_lightweight_status_has_version_archive_and_delegate(self):
        api = self.api({"FarmBotIssueStatus": [{"data": {"issue": {
            "id": "issue", "updatedAt": "2026-09-21T00:00:00Z", "archivedAt": None,
            "state": {"name": "Done", "type": "completed"}, "delegate": {"id": APP}}}}]})
        value = api.issue_status("issue")
        self.assertEqual((value["updated_at"], value["status_type"], value["delegate_id"], value["archived"]),
                         ("2026-09-21T00:00:00Z", "completed", APP, False))
        self.assertNotIn("comments", self.http.calls[-1][2]["query"])

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
