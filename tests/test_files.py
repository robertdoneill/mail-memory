import json

import pytest

from mailmem import cli, mcp_ssh
from mailmem.files import find_files, format_files, matches_type
from mailmem.store import Store


def _msg(i, date, sender, subject):
    return {"id": f"m{i}", "threadId": f"t{i}", "receivedAt": date, "subject": subject, "mailboxIds": {"inbox": True},
            "keywords": {}, "hasAttachment": True, "preview": ""}


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "x.sqlite"))
    rows = [
        (1, "2026-08-28T21:08:01Z", "Judith Silver <judith@recyclesmart.org>", "RFP",
         [("b1", "Cost Forms.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 40_000, 900),
          ("b2", "RFP_FINAL.pdf", "application/pdf", 300_000, 5000),
          ("b3", "image001.png", "image/png", 4_000, 0)]),
        (2, "2026-03-27T17:14:08Z", "Worldpay <assist@worldpay.com>", "Assist overview",
         [("b4", "export", "text/csv", 2_000, 10)]),          # no extension, CSV by MIME type
        (3, "2026-05-01T10:00:00Z", "Bank <no-reply@bank.com>", "Statement",
         [("b5", "Statement May.XLS", "application/octet-stream", 80_000, 0)]),  # extension, wrong MIME type
        (4, "2026-06-01T10:00:00Z", "Judith Silver <judith@recyclesmart.org>", "Fwd: RFP",
         [("b1", "Cost Forms.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 40_000, 0)]),
    ]
    for i, date, sender, subject, atts in rows:
        s.add_message(_msg(i, date, sender, subject), sender, "", False, [], [], atts)
    s.db.commit()
    return s


def test_matches_type():
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert matches_type("a.xlsx", xlsx, "spreadsheet")
    assert matches_type("a.xlsx", xlsx, "excel")
    assert matches_type("a.xlsx", xlsx, ".XLSX")
    assert not matches_type("a.xlsx", xlsx, "pdf")
    assert matches_type("export", "text/csv", "csv")          # no extension: the MIME type decides
    assert not matches_type("notes.txt", "text/csv", "csv")   # an extension wins over a wrong MIME type
    assert matches_type("photo.HEIC", "application/octet-stream", "image")


def test_spreadsheets(store):
    names = [f["name"] for f in find_files(store, ["spreadsheet"])]
    assert names == ["Cost Forms.xlsx", "Cost Forms.xlsx", "Statement May.XLS", "export"]


def test_extension_and_filters(store):
    assert [f["message_id"] for f in find_files(store, ["xlsx"])] == ["m1", "m4"]
    assert [f["message_id"] for f in find_files(store, ["xlsx"], after="2026-07-01")] == ["m1"]
    assert [f["message_id"] for f in find_files(store, ["xlsx"], before="2026-07-01")] == ["m4"]
    assert [f["name"] for f in find_files(store, sender="worldpay")] == ["export"]
    assert [f["name"] for f in find_files(store, name="cost FORMS")] == ["Cost Forms.xlsx", "Cost Forms.xlsx"]
    assert find_files(store, name="cost invoice") == []
    assert [f["name"] for f in find_files(store, min_size=100_000)] == ["RFP_FINAL.pdf"]
    assert len(find_files(store, limit=2)) == 2


def test_images_only_when_asked(store):
    assert "image001.png" not in [f["name"] for f in find_files(store)]
    assert [f["name"] for f in find_files(store, ["image"])] == ["image001.png"]


def test_text_indexed_follows_the_blob(store):
    # The file's text is stored once, on the first message; the forwarded copy is covered by it too.
    by_msg = {f["message_id"]: f["text_indexed"] for f in find_files(store, ["xlsx"])}
    assert by_msg == {"m1": True, "m4": True}
    assert find_files(store, ["xls"])[0]["text_indexed"] is False


def test_result_fields_and_format(store):
    f = find_files(store, ["pdf"])[0]
    assert f == {"name": "RFP_FINAL.pdf", "type": "application/pdf", "size": 300_000,
                 "date": "2026-08-28T21:08:01Z", "subject": "RFP",
                 "sender": "Judith Silver <judith@recyclesmart.org>", "message_id": "m1", "thread_id": "t1",
                 "blob_id": "b2", "text_indexed": True}
    assert format_files([f]) == ("2026-08-28  RFP_FINAL.pdf  (293.0 KB)\n"
                                 "    Judith Silver <judith@recyclesmart.org>: RFP  [m1]")


def test_cli_json(store, capsys):
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    cli.main(["--db", path, "files", "--type", "spreadsheet", "--from", "judith", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert [f["message_id"] for f in out] == ["m1", "m4"]


def test_mcp_files_command(monkeypatch):
    monkeypatch.setattr(mcp_ssh, "HOST", "mail")
    monkeypatch.setattr(mcp_ssh, "ACCOUNTS", {"a@x": "/srv/a.sqlite", "b@x": "/srv/b.sqlite"})
    monkeypatch.setattr(mcp_ssh, "DEFAULT", "a@x")
    cmd = mcp_ssh.files_command({"type": ["xlsx", "csv"], "from": "judith", "after": "2026-01-01", "limit": 500})
    assert cmd[1:] == ["--db", "/srv/a.sqlite", "files", "--json", "-n", "100", "--type", "xlsx", "--type", "csv",
                       "--from", "judith", "--after", "2026-01-01"]
    assert mcp_ssh.files_command({"type": "pdf", "account": "b@x"})[2] == "/srv/b.sqlite"
    with pytest.raises(ValueError):
        mcp_ssh.files_command({"account": "evil@x"})


def test_mcp_lists_both_tools(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})))
    mcp_ssh.main()
    names = [t["name"] for t in json.loads(capsys.readouterr().out)["result"]["tools"]]
    assert names == ["search_mail_by_meaning", "find_attachments"]


def test_cli_missing_index_is_not_created(tmp_path):
    path = tmp_path / "none.sqlite"
    with pytest.raises(SystemExit):
        cli.main(["--db", str(path), "files"])
    assert not path.exists()
