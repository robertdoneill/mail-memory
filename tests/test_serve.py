import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from mailmem import indexer
from mailmem.serve import make_handler, snippet
from mailmem.store import Store
from tests.test_attachments import FakeEmb, FakeJmap, _docx, _email


class FakeAuth:
    def username(self, token):
        return {"key-a": "a@x.com", "key-b": "b@x.com"}.get(token)


def _get(port, path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_snippet_escapes_and_marks():
    s = snippet("Invoice <b>INV-2041</b> & the pet deposit is due", 'deposit "INV-2041"')
    assert "&lt;b&gt;<mark>INV-2041</mark>&lt;/b&gt;" in s and "&amp;" in s and "<b>" not in s


def test_search_is_per_user(tmp_path):
    att = {"blobId": "B1", "name": "lease.docx", "type": "application/octet-stream", "size": 100}
    db = str(tmp_path / "a.sqlite")
    indexer._run(Store(db), FakeJmap({"1": _email("1", [att])}, {"B1": _docx("pet deposit is 500")}),
                 FakeEmb(), ["1"], None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler({"a@x.com": db}, FakeAuth(), FakeEmb()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert _get(port, "/search?q=pet+deposit")[0] == 401
        assert _get(port, "/search?q=pet+deposit", "wrong")[0] == 401
        assert _get(port, "/search?q=pet+deposit", "key-b")[0] == 404  # valid key, but no index for b
        code, body = _get(port, "/search?q=pet+deposit", "key-a")
        assert code == 200 and body["account"] == "a@x.com"
        hit = body["results"][0]
        assert hit["message_id"] == "1" and hit["attachment"] == "lease.docx" and "<mark>" in hit["snippet"]
        assert "passage" not in hit
        assert _get(port, "/search?q=pe", "key-a")[1]["results"] == []
    finally:
        srv.shutdown()


def test_snippet_drops_links_and_ids():
    raw = ("Intuit Developer Group <IntuitPartners@intuit.com> 6AF5A4B9A127F0B2E95E1079C60B04 "
           "Migrate to the new webhooks format by May https://links.intuit.com/ls/click?upn=abc123 [ ] ------ thanks")
    s = snippet(raw, "webhooks migration")
    assert "http" not in s and "6AF5A4B9" not in s and "IntuitPartners@" not in s and "----" not in s
    assert "<mark>webhooks</mark>" in s and "Migrate to the new" in s
    s = snippet("View on web <https://x.co/a?b=1> Change endpoints from the Dashboard (https://d.co/x) now",
                "that dashboard")
    assert "&lt;" not in s and "(" not in s and "<mark>Dashboard</mark>" in s and "<mark>that" not in s
    assert "INV-2041" in snippet("Invoice INV-2041 is attached", '"INV-2041"')  # short codes survive


def test_files_endpoint(tmp_path):
    from tests.test_files import _msg
    db = str(tmp_path / "a.sqlite")
    s = Store(db)
    s.add_message(_msg(1, "2026-08-28T21:08:01Z", "Judith <j@r.org>", "RFP costs"), "Judith <j@r.org>",
                  "Robert <r@x.com>", False, [], [],
                  [("b1", "Cost Form.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 9, 0),
                   ("b2", "RFP.pdf", "application/pdf", 9, 0)])
    s.db.commit()
    s.db.close()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler({"a@x.com": db}, FakeAuth(), FakeEmb()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert _get(port, "/files?type=xlsx")[0] == 401
        assert _get(port, "/files?type=xlsx", "key-b")[0] == 404     # a user with no index
        code, body = _get(port, "/files?type=xlsx&type=pdf&q=judith", "key-a")
        assert code == 200 and body["account"] == "a@x.com" and body["truncated"] is False
        assert [f["name"] for f in body["files"]] == ["Cost Form.xlsx", "RFP.pdf"]
        f = body["files"][0]
        assert f["recipients"] == "Robert <r@x.com>" and f["mailbox_ids"] == ["inbox"] and f["keywords"] == []
        assert _get(port, "/files?type=pdf&q=nobody", "key-a")[1]["files"] == []
        code, body = _get(port, "/files?type=pdf&limit=1", "key-a")
        assert body["truncated"] is True
        assert _get(port, "/files?limit=lots", "key-a")[0] == 400
    finally:
        srv.shutdown()
