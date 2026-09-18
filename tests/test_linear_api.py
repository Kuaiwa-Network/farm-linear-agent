import io
import json
import unittest

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
        return io.BytesIO(json.dumps(self.answers[name].pop(0)).encode())


def issue_page(cursor, has_next, comments):
    return {"data": {"issue": {
        "id": "10000000-0000-4000-8000-000000000001", "identifier": "FARM-1", "url": "https://linear.app/k/issue/FARM-1",
        "branchName": "farmbot/farm-1", "title": "T", "description": "see https://uploads.linear.app/a/b/c?signature=xyz", "priority": 2,
        "archivedAt": None, "state": {"name": "Todo", "type": "unstarted"}, "team": {"id": "9676b5f9-eff3-485b-80ed-900ed137e21a"},
        "labels": {"nodes": [{"name": "Bug"}]}, "attachments": {"nodes": [{"url": "https://github.com/o/r/pull/1"}]},
        "delegate": {"id": APP},
        "comments": {"nodes": comments, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}}}


class LinearAPITests(unittest.TestCase):
    def api(self, answers):
        self.http = FakeHTTP(answers)
        return LinearAPI("client", "secret", request=self.http)

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

    def test_strip_signed_removes_upload_query_only(self):
        self.assertEqual(strip_signed("https://uploads.linear.app/a/b?signature=1&x=2"), "https://uploads.linear.app/a/b")
        self.assertEqual(strip_signed("https://github.com/o/r/pull/1?x=1"), "https://github.com/o/r/pull/1?x=1")
