"""Linear GraphQL client using the application's own client-credentials token."""
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

SCOPES = "read,write,app:mentionable,app:assignable"
# An upload URL's path is ASCII ID segments; its query and fragment are what Linear signs (a signature and its
# parameters, percent-encoded or with "&" HTML-escaped as "&amp;"). Neither class holds characters that follow a
# URL in prose or markup: whitespace, quotes, brackets, "<", ">", backticks, "*", ",", "!" and any non-ASCII
# character end it; "&" continues a query only before another parameter, so an entity after the URL such as
# "&quot;" or "&gt;" stays; and a query never ends in ".", ":", "?" or "#". So the Markdown, JSON, HTML or
# sentence around the URL survives, and two URLs joined by a comma stay two.
_PATH_CHARACTERS = r"A-Za-z0-9\-._~%/"
_QUERY_CHARACTERS = r"A-Za-z0-9\-._~%=+/:@?#"
_PARAMETER = r"&(?:amp;)?(?=[A-Za-z0-9_\-]+=)"
UPLOAD = re.compile(rf"(https://uploads\.linear\.app/[{_PATH_CHARACTERS}]+)"
                    rf"(?:[?#](?:[{_QUERY_CHARACTERS}]|{_PARAMETER})+(?<![.:?#]))?")
# A Linear web URL: a person's profile, which Linear renders as a mention in a comment, or a comment's own link.
LINEAR_URL = re.compile(r"https://linear\.app/\S+")
# A person's name as FarmBot keeps it (it is printed into comments and ruling markers): at most NAME_LIMIT
# characters, none of them a control, format (a bidirectional mark or an invisible character, other than the
# joiners emoji sequences and some scripts use), private-use or line-separator character or an unpaired
# surrogate, and never an email address, which FarmBot does not store.
NAME_LIMIT = 256
_HIDDEN_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}
_JOINERS = {"\u200c", "\u200d"}
_EMAIL = re.compile(r"""[^\s@<>()\[\],;:"']+@[^\s@<>()\[\],;:"']+\.[A-Za-z]{2,}""")
ISSUE_QUERY = """query FarmBotIssue($id: String!, $after: String) {
  issue(id: $id) {
    id identifier url branchName title description priority archivedAt updatedAt
    state { name type } team { id } labels { nodes { name parent { id name } } } attachments { nodes { url } }
    delegate { id } assignee { id name url } creator { id name url }
    comments(first: 50, after: $after) {
      nodes { id url body createdAt updatedAt user { id name url } botActor { id } parent { id } }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""
ISSUE_READ_ATTEMPTS = 3


def strip_signed(text):
    """Drop signed query strings (and fragments) from Linear upload URLs so fingerprints stay stable.

    Only the URL changes: the Markdown, JSON, HTML or sentence around it, a <linear-image> block included, stays
    as written.
    """
    return UPLOAD.sub(r"\1", text or "")


def upload_urls(text):
    """The uploads.linear.app URLs in text, unsigned, each once, sorted.

    Markdown images and links, bare URLs (less the sentence punctuation after one) and the src of <linear-image>
    and <linear-embed> blocks, in signed text or in stored text that strip_signed has already cleaned.
    """
    return sorted({match.group(1).rstrip(".,:;!") for match in UPLOAD.finditer(text or "")})


def _linear_url(value):
    """value when it is an https://linear.app/ URL, else None."""
    return value if isinstance(value, str) and LINEAR_URL.fullmatch(value) else None


def _hides_text(name):
    """True when a character of name could hide, reorder or split text once the name is printed."""
    return any(unicodedata.category(ch) in _HIDDEN_CATEGORIES and ch not in _JOINERS for ch in name)


def person(node):
    """A Linear User node as exactly {"id", "name", "url"}, or None.

    None unless the node has a UUID id, a name and an https://linear.app/ profile URL. The name is trimmed, and a
    name that is too long, holds hidden characters or contains an email address names nobody. Every other field,
    the email included, is dropped.
    """
    if not isinstance(node, dict):
        return None
    user_id, name, url = node.get("id"), node.get("name"), _linear_url(node.get("url"))
    if not (isinstance(user_id, str) and isinstance(name, str) and url):
        return None
    name = name.strip()
    if not name or len(name) > NAME_LIMIT or _hides_text(name) or _EMAIL.search(name):
        return None
    try:
        return {"id": str(UUID(user_id)), "name": name, "url": url}
    except ValueError:
        return None


def _label_groups(nodes):
    """Sorted, distinct {"group": parent name, "label": name} pairs for the labels inside a label group."""
    pairs = set()
    for node in nodes:
        group, label = (node.get("parent") or {}).get("name"), node.get("name")
        if isinstance(group, str) and group.strip() and isinstance(label, str) and label.strip():
            pairs.add((group, label))
    return [{"group": group, "label": label} for group, label in sorted(pairs)]


class LinearAPI:
    def __init__(self, client_id, client_secret, *, expected_name="FarmBot", request=None,
                 expected_app_user_id="", expected_organization_id=""):
        self.client_id, self.client_secret = client_id, client_secret
        self.expected_name = expected_name
        self.expected_app_user_id = expected_app_user_id
        self.expected_organization_id = expected_organization_id
        self.request = request or urllib.request.urlopen
        self.token, self.expires = None, 0
        self.app_user_id = None

    def _request(self, url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers)
        with self.request(req, timeout=8) as response:
            return json.load(response)

    def authenticate(self):
        result = self._request("https://api.linear.app/oauth/token", urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": self.client_id,
            "client_secret": self.client_secret, "scope": SCOPES}).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.token = result["access_token"]
        self.expires = time.time() + float(result["expires_in"]) - 60

    def graphql(self, query, variables=None):
        if not self.token or time.time() >= self.expires:
            self.authenticate()
        for attempt in range(2):
            try:
                result = self._request("https://api.linear.app/graphql",
                                       json.dumps({"query": query, "variables": variables or {}}).encode(),
                                       {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 401 or attempt:
                    raise
                self.authenticate()
        if result.get("errors") or not isinstance(result.get("data"), dict):
            raise RuntimeError("Linear GraphQL rejected the request")
        return result["data"]

    def identity(self):
        self.app_user_id = None
        data = self.graphql("query FarmBotIdentity { viewer { id name } organization { id name } }")
        viewer, organization = data.get('viewer') or {}, data.get('organization') or {}
        if (viewer.get('name') != self.expected_name or not viewer.get('id') or not organization.get('id')
                or (self.expected_app_user_id and viewer['id'] != self.expected_app_user_id)
                or (self.expected_organization_id and organization['id'] != self.expected_organization_id)):
            raise RuntimeError(f"Expected {self.expected_name} app identity")
        self.app_user_id = data["viewer"]["id"]
        return data

    def create_activity(self, session_id, content, activity_id=None):
        activity = {"agentSessionId": session_id, "content": content}
        if activity_id:
            activity["id"] = activity_id
        result = self.graphql("""mutation FarmBotActivity($input: AgentActivityCreateInput!) {
            agentActivityCreate(input: $input) { success agentActivity { id } } }""", {"input": activity})["agentActivityCreate"]
        if result.get("success") is not True or not result.get("agentActivity", {}).get("id"):
            raise RuntimeError("Linear did not confirm activity creation")
        return result

    def create_comment(self, issue_id, body):
        result = self.graphql("""mutation FarmBotComment($input: CommentCreateInput!) {
            commentCreate(input: $input) { success comment { id } } }""", {"input": {"issueId": issue_id, "body": body}})["commentCreate"]
        if result.get("success") is not True or not result.get("comment", {}).get("id"):
            raise RuntimeError("Linear did not confirm comment creation")
        return result["comment"]["id"]

    def issue_delegate(self, issue_id):
        data = self.graphql("query FarmBotDelegate($id: String!) { issue(id: $id) { delegate { id } } }", {"id": issue_id})
        delegate = (data.get("issue") or {}).get("delegate")
        return delegate["id"] if delegate else None

    def session_has_artificial_root(self, session_id, issue_id, app_user_id):
        """Distinguish Linear's synthetic session thread from a comment mention.

        The flag is not always included in webhook comments. Read it from Linear;
        never infer delegation authority from the placeholder's display text.
        A source comment still indicates a mention, even with an artificial root.
        """
        session = self.graphql("""query FarmBotSessionOrigin($id: String!) {
            agentSession(id: $id) {
                issue { id } appUser { id }
                comment { isArtificialAgentSessionRoot } sourceComment { id }
            }
        }""", {"id": session_id})["agentSession"]
        if (not session or (session.get("issue") or {}).get("id") != issue_id
                or (session.get("appUser") or {}).get("id") != app_user_id):
            raise RuntimeError("Linear session context mismatch")
        return ((session.get("comment") or {}).get("isArtificialAgentSessionRoot") is True
                and not session.get("sourceComment"))

    def needs_more_info(self, issue_id):
        """Add the clarification label without replacing any existing labels."""
        after, labels = None, []
        while True:
            data = self.graphql("""query FarmBotInfoLabel($id: String!, $after: String) {
                issue(id: $id) { team { id } labels { nodes { name } } }
                issueLabels(filter: {name: {eq: "needs-more-info"}}, first: 100, after: $after) {
                    nodes { id team { id } } pageInfo { hasNextPage endCursor }
                } }""", {"id": issue_id, "after": after})
            issue = data["issue"]
            if issue is None:
                raise RuntimeError("Issue not found")
            if any(label["name"] == "needs-more-info" for label in issue["labels"]["nodes"]):
                return
            labels.extend(data["issueLabels"]["nodes"])
            page = data["issueLabels"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        team = issue["team"]["id"]
        label = next((v for v in labels if (v.get("team") or {}).get("id") == team), None)
        label = label or next((v for v in labels if v.get("team") is None), None)
        if label is None:
            created = self.graphql("""mutation FarmBotCreateInfoLabel($input: IssueLabelCreateInput!) {
                issueLabelCreate(input: $input) { success issueLabel { id } } }""",
                {"input": {"name": "needs-more-info", "teamId": team}})["issueLabelCreate"]
            if not created.get("success") or not created.get("issueLabel", {}).get("id"):
                raise RuntimeError("Linear did not confirm label creation")
            label = created["issueLabel"]
        result = self.graphql("""mutation FarmBotAddLabel($id: String!, $label: String!) {
            issueAddLabel(id: $id, labelId: $label) { success } }""",
            {"id": issue_id, "label": label["id"]})["issueAddLabel"]
        if result.get("success") is not True:
            raise RuntimeError("Linear did not confirm needs-more-info label")

    def issue_status(self, issue_id):
        issue = self.graphql("""query FarmBotIssueStatus($id: String!) {
            issue(id: $id) { id updatedAt archivedAt state { name type } delegate { id } }
        }""", {"id": issue_id})["issue"]
        if not issue:
            raise RuntimeError("Issue not found")
        return {"id": issue["id"], "updated_at": issue["updatedAt"],
                "archived": issue["archivedAt"] is not None, "status": issue["state"]["name"],
                "status_type": issue["state"]["type"], "delegate_id": (issue.get("delegate") or {}).get("id")}

    def fetch_issue(self, issue_ref):
        """Complete detail plus every comment page, shaped for Ledger.observe_issue."""
        if self.app_user_id is None:
            self.identity()
        comments, after, issue = [], None, None
        while True:
            # A timed-out issue query has no side effect. Retry only this read;
            # replaying GraphQL mutations after a lost response is unsafe.
            for attempt in range(ISSUE_READ_ATTEMPTS):
                try:
                    issue = self.graphql(ISSUE_QUERY, {"id": issue_ref, "after": after})["issue"]
                    break
                except (TimeoutError, urllib.error.URLError) as exc:
                    if (isinstance(exc, urllib.error.URLError)
                            and not isinstance(exc.reason, TimeoutError)) or attempt == ISSUE_READ_ATTEMPTS - 1:
                        raise
                    time.sleep(0.25 * (attempt + 1))
            if issue is None:
                raise RuntimeError("Issue not found")
            for node in issue["comments"]["nodes"]:
                if node.get("botActor") or (self.app_user_id and (node.get("user") or {}).get("id") == self.app_user_id):
                    kind = "bot"
                elif node.get("user"):
                    kind = "human"
                else:
                    kind = "unknown"
                # Only a human comment has an author: FarmBot's own and integrations' comments have none.
                comments.append({"id": node["id"], "url": _linear_url(node.get("url")),
                                 "body": strip_signed(node["body"]), "author_kind": kind,
                                 "author": person(node.get("user")) if kind == "human" else None,
                                 "parent_id": (node.get("parent") or {}).get("id"),
                                 "created_at": node["createdAt"], "updated_at": node["updatedAt"]})
            page = issue["comments"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        # FarmBot's own app user is never the issue's owner or its author, whatever Linear reports.
        people = {field: person(issue.get(field)) for field in ("assignee", "creator")}
        people = {field: None if found and found["id"] == (self.app_user_id or "").lower() else found
                  for field, found in people.items()}
        return {"id": issue["id"], "identifier": issue["identifier"], "team_id": issue["team"]["id"], "url": issue["url"],
                "updated_at": issue.get("updatedAt"), "branch_name": issue.get("branchName") or "", "title": issue["title"],
                "description": strip_signed(issue.get("description") or ""), "status": issue["state"]["name"],
                "status_type": issue["state"]["type"], "labels": [n["name"] for n in issue["labels"]["nodes"]],
                "label_groups": _label_groups(issue["labels"]["nodes"]),
                "assignee": people["assignee"], "creator": people["creator"],
                "priority": int(issue["priority"] or 0), "archived": issue.get("archivedAt") is not None,
                "delegate_id": (issue.get("delegate") or {}).get("id"),
                "attachments": sorted({strip_signed(n["url"]) for n in issue["attachments"]["nodes"]}),
                "comments": comments, "detail_complete": True, "comments_complete": True}
