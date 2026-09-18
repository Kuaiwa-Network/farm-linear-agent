"""Linear GraphQL client using the application's own client-credentials token."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

SCOPES = "read,write,app:mentionable,app:assignable"
UPLOAD = re.compile(r"(https://uploads\.linear\.app/[^\s?#)]+)[^\s)]*")
ISSUE_QUERY = """query FarmBotIssue($id: String!, $after: String) {
  issue(id: $id) {
    id identifier url branchName title description priority archivedAt
    state { name type } team { id } labels { nodes { name } } attachments { nodes { url } } delegate { id }
    comments(first: 50, after: $after) {
      nodes { id body createdAt updatedAt user { id } botActor { id } }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""


def strip_signed(text):
    """Drop signed query strings from Linear upload URLs so fingerprints stay stable."""
    return UPLOAD.sub(r"\1", text or "")


class LinearAPI:
    def __init__(self, client_id, client_secret, *, expected_name="FarmBot", request=None):
        self.client_id, self.client_secret = client_id, client_secret
        self.expected_name = expected_name
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
        data = self.graphql("query FarmBotIdentity { viewer { id name } organization { id name } }")
        if data["viewer"]["name"] != self.expected_name:
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

    def fetch_issue(self, issue_ref):
        """Complete detail plus every comment page, shaped for Ledger.observe_issue."""
        comments, after, issue = [], None, None
        while True:
            issue = self.graphql(ISSUE_QUERY, {"id": issue_ref, "after": after})["issue"]
            if issue is None:
                raise RuntimeError("Issue not found")
            for node in issue["comments"]["nodes"]:
                if node.get("botActor") or (self.app_user_id and (node.get("user") or {}).get("id") == self.app_user_id):
                    kind = "bot"
                elif node.get("user"):
                    kind = "human"
                else:
                    kind = "unknown"
                comments.append({"id": node["id"], "body": strip_signed(node["body"]), "author_kind": kind,
                                 "created_at": node["createdAt"], "updated_at": node["updatedAt"]})
            page = issue["comments"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        return {"id": issue["id"], "identifier": issue["identifier"], "team_id": issue["team"]["id"], "url": issue["url"],
                "branch_name": issue.get("branchName") or "", "title": issue["title"],
                "description": strip_signed(issue.get("description") or ""), "status": issue["state"]["name"],
                "status_type": issue["state"]["type"], "labels": [n["name"] for n in issue["labels"]["nodes"]],
                "priority": int(issue["priority"] or 0), "archived": issue.get("archivedAt") is not None,
                "delegate_id": (issue.get("delegate") or {}).get("id"),
                "attachments": sorted({strip_signed(n["url"]) for n in issue["attachments"]["nodes"]}),
                "comments": comments, "detail_complete": True, "comments_complete": True}
