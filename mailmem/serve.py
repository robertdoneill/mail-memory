"""HTTP search API for clients such as the Bulwark plugin.

Each request carries the user's own JMAP API key as a bearer token. The key is checked against the mail
server's JMAP session, and the account it belongs to picks the index: a user can only ever search their
own mail. Accounts and index files are listed in a JSON file, {"user@example.com": "/srv/index.sqlite"}.

  GET /search?q=...&limit=10&after=YYYY-MM-DD&before=YYYY-MM-DD&from=...&bulk=1
  GET /files?type=xlsx&type=pdf&name=...&q=...&from=...&after=...&before=...&limit=500
  GET /health
"""
import hashlib
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .embed import Embedder
from .files import find_files
from .search import _exact_terms, search
from .store import Store

TOKEN_TTL = 600  # seconds a checked key is trusted before asking the mail server again
MAX_LIMIT = 30
MAX_FILES = 1000


class Auth:
    """Bearer token -> JMAP username, checked against the mail server and cached by hash."""

    def __init__(self, jmap_url):
        self.session_url = jmap_url.rstrip("/") + "/.well-known/jmap"
        self.cache = {}
        self.lock = threading.Lock()

    def username(self, token):
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            hit = self.cache.get(h)
            if hit and hit[1] > time.time():
                return hit[0]
        req = urllib.request.Request(self.session_url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                user = json.load(r).get("username")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return None
            raise
        if user:
            with self.lock:
                self.cache[h] = (user, time.time() + TOKEN_TTL)
        return user


# Noise that fills a two-line snippet without saying anything: links, tracking ids, encoded blobs.
_JUNK = [
    re.compile(r"[<(\[]\s*(?:https?://|www\.|mailto:)[^>)\]]*[>)\]]", re.I),  # links in brackets, brackets too
    re.compile(r"https?://\S+|www\.\S+|mailto:\S+", re.I),
    re.compile(r"<[^<>\s]+@[^<>\s]+>"),                                     # <addr@host> after a name
    re.compile(r"\b(?=[A-Za-z0-9+/=_-]*\d)(?=[A-Za-z0-9+/=_-]*[A-Za-z])[A-Za-z0-9+/=_-]{24,}\b"),  # ids, base64
    re.compile(r"[\[\(]\s*[\]\)]"),                                          # brackets left empty
    re.compile(r"([-=_*~#|•·]\s*){4,}"),                                     # rules and separator runs
]


# Words too common to be worth highlighting.
_STOP = set("""about after also and are because been but can could did does for from had has have her his how
into its just more not now our out over said she should than that the their them then there these they this
those through was were what when where which while who why will with would you your""".split())


def clean(text):
    for rx in _JUNK:
        text = rx.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def snippet(text, query, width=220):
    """Plain text around the first matching term, cleaned of links and ids, escaped, matches in <mark>."""
    terms = [t for t in _exact_terms(query) if len(t) > 2] or \
        [w for w in re.findall(r"\w{3,}", query.lower()) if w not in _STOP]
    flat = clean(text)
    low = flat.lower()
    pos = min((low.find(t) for t in terms if t in low), default=0)
    start = max(0, pos - width // 3)
    cut = flat[start:start + width]
    out = html.escape(cut, quote=False)
    for t in sorted(set(terms), key=len, reverse=True):
        out = re.sub(re.escape(html.escape(t, quote=False)), lambda m: f"<mark>{m.group(0)}</mark>", out,
                     flags=re.I)
    return ("…" if start else "") + out + ("…" if start + width < len(flat) else "")


def add_details(store, results):
    """What a mail client needs to show each result's message as a row: recipients, folder ids, keywords,
    preview and whether it has attachments."""
    for r in results:
        row = store.db.execute("SELECT recipients, mailboxes, keywords, preview, has_attachment FROM messages "
                               "WHERE id = ?", (r["message_id"],)).fetchone()
        if row:
            r.update(recipients=row[0] or "", mailbox_ids=json.loads(row[1] or "[]"),
                     keywords=json.loads(row[2] or "[]"), preview=row[3] or "", has_attachment=bool(row[4]))


def make_handler(accounts, auth, emb):

    class Handler(BaseHTTPRequestHandler):
        server_version = "mailmem"

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # no queries in the log: they are private
            print(f"{self.address_string()} {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}",
                  file=sys.stderr)

        def _index(self):
            """The caller's index file, or None after sending the error."""
            m = re.match(r"Bearer\s+(\S+)$", self.headers.get("Authorization", ""))
            if not m:
                return self._send(401, {"error": "missing API key"})
            try:
                user = auth.username(m.group(1))
            except Exception:
                return self._send(502, {"error": "could not reach the mail server to check the key"})
            if not user:
                return self._send(401, {"error": "API key not accepted by the mail server"})
            db = accounts.get(user.lower())
            if not db:
                return self._send(404, {"error": f"no Mail Memory index for {user}"})
            return user, db

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/health":
                return self._send(200, {"ok": True})
            if url.path not in ("/search", "/files"):
                return self._send(404, {"error": "not found"})
            found = self._index()
            if not found:
                return
            user, db = found
            multi = urllib.parse.parse_qs(url.query)
            p = {k: v[0] for k, v in multi.items()}
            if url.path == "/files":
                return self._files(user, db, multi, p)
            q = (p.get("q") or "").strip()
            if len(q) < 3:
                return self._send(200, {"account": user, "results": []})
            limit = max(1, min(int(p.get("limit") or 10), MAX_LIMIT))
            store = Store(db, readonly=True)
            try:
                res = search(store, emb, q, limit, p.get("after"), p.get("before"), p.get("from"),
                             include_bulk=p.get("bulk") == "1")
                add_details(store, res)
            finally:
                store.db.close()
            for r in res:
                r["snippet"] = snippet(r.pop("passage"), q)
            return self._send(200, {"account": user, "results": res})

        def _files(self, user, db, multi, p):
            try:
                limit = max(1, min(int(p.get("limit") or 500), MAX_FILES))
            except ValueError:
                return self._send(400, {"error": "limit must be a number"})
            types = [t for t in multi.get("type", []) if t.strip()][:20]
            store = Store(db, readonly=True)
            try:
                res = find_files(store, types, p.get("name"), p.get("from"), p.get("after"), p.get("before"),
                                 limit=limit, text=p.get("q"), details=True)
            finally:
                store.db.close()
            return self._send(200, {"account": user, "files": res, "truncated": len(res) >= limit})

    return Handler


def serve(listen, accounts_file, jmap_url, embed_url):
    accounts = {k.lower(): v for k, v in json.load(open(accounts_file)).items()}
    host, port = listen.rsplit(":", 1)
    srv = ThreadingHTTPServer((host, int(port)), make_handler(accounts, Auth(jmap_url), Embedder(embed_url)))
    print(f"mailmem serving {len(accounts)} accounts on {listen}", file=sys.stderr)
    srv.serve_forever()
