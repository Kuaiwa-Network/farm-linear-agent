import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from agent import ledger as ledger_module
from agent.linear_api import ISSUE_QUERY, LINEAR_URL, LinearAPI, person, strip_signed, upload_urls

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

    def test_the_issue_query_reads_people_label_parents_and_reply_parents(self):
        query = " ".join(ISSUE_QUERY.split())
        for part in ("labels { nodes { name parent { id name } } }", "assignee { id name url }",
                     "creator { id name url }",
                     "nodes { id url body createdAt updatedAt user { id name url } botActor { id } parent { id } }"):
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
        self.assertEqual(person({**DESIGNER, "name": "主策 👩‍🎨"})["name"], "主策 👩‍🎨")  # a joiner is not hidden
        self.assertEqual(person({**DESIGNER, "name": "x" * 256})["name"], "x" * 256)
        for name in (7, "x" * 257, "Designer\nOne", "Designer\x00One", "Designer\x1b[31mOne",
                     "Designer ‮enO", "Designer​One", "Designer \ud800", "designer.one@example.com",
                     "Designer One <designer.one@example.com>"):
            with self.subTest(name=name):
                self.assertIsNone(person({**DESIGNER, "name": name}))

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
