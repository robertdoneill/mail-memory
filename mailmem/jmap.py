"""Minimal read-only JMAP client: session discovery, paging through Email/query, Email/get, Email/changes."""
import json
import urllib.parse
import urllib.request

MAIL = "urn:ietf:params:jmap:mail"
USING = ["urn:ietf:params:jmap:core", MAIL]

EMAIL_PROPS = [
    "id", "threadId", "mailboxIds", "keywords", "receivedAt", "subject", "from", "to", "cc",
    "preview", "hasAttachment", "textBody", "htmlBody", "bodyValues", "attachments",
    # Raw form: JMAP only allows List-Unsubscribe as raw or URLs, not as text.
    "header:List-Unsubscribe", "header:Precedence",
]


class Jmap:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token
        s = self._http("GET", f"{self.base_url}/.well-known/jmap")
        self.api_url = s["apiUrl"]
        self.download_url = s["downloadUrl"]
        self.username = s["username"]
        self.account_id = s["primaryAccounts"][MAIL]
        limits = s["capabilities"]["urn:ietf:params:jmap:core"]
        self.max_get = min(int(limits.get("maxObjectsInGet", 500)), 200)

    def _http(self, method, url, body=None):
        req = urllib.request.Request(
            url, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    def download(self, blob_id, name="file", type_="application/octet-stream"):
        q = urllib.parse.quote
        url = (self.download_url.replace("{accountId}", q(self.account_id)).replace("{blobId}", q(blob_id))
               .replace("{name}", q(name or "file")).replace("{type}", q(type_ or "application/octet-stream")))
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.read()

    def call(self, method, args):
        args = {"accountId": self.account_id, **args}
        res = self._http("POST", self.api_url, {"using": USING, "methodCalls": [[method, args, "0"]]})
        name, out, _ = res["methodResponses"][0]
        if name == "error":
            raise RuntimeError(f"{method}: {out}")
        return out

    def mailboxes(self):
        return {m["id"]: m for m in self.call("Mailbox/get", {"properties": ["name", "role"]})["list"]}

    def email_state(self):
        return self.call("Email/get", {"ids": [], "properties": ["id"]})["state"]

    def all_email_ids(self, page=1000):
        """Every email id, oldest first."""
        pos = 0
        while True:
            out = self.call("Email/query", {
                "sort": [{"property": "receivedAt", "isAscending": True}],
                "position": pos, "limit": page, "calculateTotal": True})
            ids = out["ids"]
            yield from ids
            pos += len(ids)
            if not ids or pos >= out.get("total", pos):
                return

    def get_emails(self, ids):
        out = []
        for i in range(0, len(ids), self.max_get):
            out += self.call("Email/get", {
                "ids": ids[i:i + self.max_get], "properties": EMAIL_PROPS,
                "fetchTextBodyValues": True, "fetchHTMLBodyValues": True,
                "maxBodyValueBytes": 256_000})["list"]
        return out

    def changes(self, since):
        """Yields (created, updated, destroyed, new_state) batches until caught up."""
        while True:
            out = self.call("Email/changes", {"sinceState": since, "maxChanges": 500})
            yield out["created"], out["updated"], out["destroyed"], out["newState"]
            since = out["newState"]
            if not out["hasMoreChanges"]:
                return
