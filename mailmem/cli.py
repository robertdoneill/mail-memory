"""mailmem: index a JMAP mailbox and search it by meaning and by keyword.

The JMAP token is read from the file named by MAILMEM_TOKEN_FILE (or MAILMEM_TOKEN), never from argv.
"""
import argparse
import os
import sys
import time

from . import indexer
from .embed import Embedder
from .jmap import Jmap
from .search import format_results, search, to_json
from .store import Store


def _token():
    if os.environ.get("MAILMEM_TOKEN_FILE"):
        return open(os.environ["MAILMEM_TOKEN_FILE"]).read().strip()
    if os.environ.get("MAILMEM_TOKEN"):
        return os.environ["MAILMEM_TOKEN"].strip()
    sys.exit("set MAILMEM_TOKEN_FILE (or MAILMEM_TOKEN) to a read-only JMAP API key")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mailmem")
    ap.add_argument("--db", help="index file for one account (all commands but serve)")
    ap.add_argument("--jmap", default=os.environ.get("MAILMEM_JMAP_URL"),
                    help="JMAP server base URL, e.g. https://mail.example.com (or MAILMEM_JMAP_URL)")
    ap.add_argument("--embed", default=os.environ.get("MAILMEM_EMBED_URL", "http://127.0.0.1:8091"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="first pass (resumable), then catch up")
    w = sub.add_parser("watch", help="keep the index current")
    w.add_argument("--every", type=int, default=60, help="seconds between change checks")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("--after")
    s.add_argument("--before")
    s.add_argument("--from", dest="sender")
    s.add_argument("--no-bulk", action="store_true", help="leave out newsletters and notifications")
    s.add_argument("--json", action="store_true")
    sub.add_parser("attachments", help="extract attachments on messages indexed before attachment support")
    sub.add_parser("stats")
    v = sub.add_parser("serve", help="HTTP search API; each request is checked against the mail server")
    v.add_argument("--listen", default="127.0.0.1:8095")
    v.add_argument("--accounts", required=True, help='JSON file: {"user@example.com": "/path/index.sqlite"}')
    a = ap.parse_args(argv)
    if a.cmd not in ("stats", "search") and not a.jmap:
        ap.error("--jmap (or MAILMEM_JMAP_URL) is required")
    if a.cmd == "serve":
        from .serve import serve
        return serve(a.listen, a.accounts, a.jmap, a.embed)
    if not a.db:
        ap.error("--db is required")

    store = Store(a.db)
    emb = Embedder(a.embed)
    if a.cmd == "stats":
        for k, v in store.stats().items():
            print(f"{k:22} {v}")
    elif a.cmd == "search":
        t = time.time()
        res = search(store, emb, a.query, a.limit, a.after, a.before, a.sender, not a.no_bulk)
        print(to_json(res) if a.json else format_results(res))
        print(f"({len(res)} threads, {(time.time() - t) * 1000:.0f} ms)", file=sys.stderr)
    elif a.cmd == "index":
        indexer.full(store, Jmap(a.jmap, _token()), emb)
        print(store.stats(), file=sys.stderr)
    elif a.cmd == "attachments":
        indexer.attachments(store, Jmap(a.jmap, _token()), emb)
        print(store.stats(), file=sys.stderr)
    elif a.cmd == "watch":
        jm = Jmap(a.jmap, _token())
        indexer.full(store, jm, emb)
        while True:
            time.sleep(a.every)
            try:
                indexer.sync(store, jm, emb)
            except Exception as e:  # a network blip must not kill the watcher
                print(f"sync failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
