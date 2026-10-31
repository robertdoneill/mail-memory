import io
import zipfile

from mailmem import indexer
from mailmem.extract import extract, kind
from mailmem.search import search
from mailmem.store import Store


def _docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                                        "<w:p><w:r><w:t>Second &amp; last</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def test_kind():
    assert kind({"type": "application/pdf", "size": 10}) == "pdf"
    assert kind({"type": "application/octet-stream", "name": "Lease.DOCX"}) == "docx"
    assert kind({"type": "image/png", "name": "logo.png"}) is None
    assert kind({"type": "application/pdf", "size": 30_000_000}) is None


def test_control_char_spaces():
    assert extract({"name": "a.txt"}, b"under\x03this\x03Agreement\x0cnext") == "under this Agreement next"


def test_extract_docx_and_bad_file():
    assert extract({"name": "a.docx"}, _docx("Rent is due on the first")) == "Rent is due on the first\nSecond & last"
    assert extract({"name": "a.docx"}, b"not a zip") == ""


class FakeJmap:
    username = "test"

    def __init__(self, emails, blobs):
        self.emails, self.blobs, self.downloads = emails, blobs, []

    def get_emails(self, ids):
        return [self.emails[i] for i in ids]

    def download(self, blob, name, type_):
        self.downloads.append(blob)
        return self.blobs[blob]


class FakeEmb:
    def _v(self, s):
        v = [0.0] * 768
        for w in s.lower().split():
            v[hash(w) % 768] += 1.0
        return v

    def documents(self, items):
        return [self._v(t) for _, t in items]

    def query(self, q):
        return self._v(q)


def _email(i, atts):
    return {"id": i, "threadId": "t" + i, "mailboxIds": {"inbox": True}, "receivedAt": f"2026-10-0{i}T00:00:00Z",
            "subject": "Lease", "from": [{"email": "a@b.c"}], "textBody": [{"partId": "1"}],
            "bodyValues": {"1": {"value": "See attached."}}, "hasAttachment": True, "attachments": atts}


def test_index_attachments_once(tmp_path):
    att = {"blobId": "B1", "name": "lease.docx", "type": "application/octet-stream", "size": 100}
    logo = {"blobId": "B2", "name": "logo.png", "type": "image/png", "size": 100}
    jm = FakeJmap({"1": _email("1", [att, logo]), "2": _email("2", [att])}, {"B1": _docx("pet deposit is 500")})
    store = Store(str(tmp_path / "x.sqlite"))
    indexer._run(store, jm, FakeEmb(), ["1", "2"], None)
    assert jm.downloads == ["B1"]  # same file on two messages fetched once; the image never
    st = store.stats()
    assert (st["attachments"], st["attachments_with_text"], st["attachment_passages"]) == (3, 1, 1)
    assert store.messages_missing_attachments() == []
    hit = search(store, FakeEmb(), "pet deposit")[0]
    assert hit["attachment"] == "lease.docx" and "pet deposit" in hit["passage"]
