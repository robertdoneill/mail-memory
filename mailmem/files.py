"""Find attachments by file type, name, sender and date: an exact lookup in the index, no embeddings.

Search by meaning finds mail that talks about a spreadsheet; this finds mail that carries one.
"""
import json
import re

# A type can be a group or a bare extension ("xlsx"). Groups match by extension and by MIME type, since
# senders get one or the other wrong (an .xlsx sent as application/octet-stream, a CSV named "export").
GROUPS = {
    "spreadsheet": (("xlsx", "xls", "xlsm", "xlsb", "ods", "csv", "tsv", "numbers"),
                    ("spreadsheet", "ms-excel", "text/csv", "tab-separated")),
    "document": (("doc", "docx", "odt", "rtf", "pages", "txt", "md"),
                 ("wordprocessing", "msword", "opendocument.text", "rtf", "text/plain", "text/markdown")),
    "pdf": (("pdf",), ("application/pdf",)),
    "presentation": (("ppt", "pptx", "odp", "key"), ("presentation", "ms-powerpoint")),
    "image": (("png", "jpg", "jpeg", "gif", "heic", "heif", "webp", "tif", "tiff", "bmp", "svg"), ("image/",)),
    "archive": (("zip", "rar", "7z", "tar", "gz", "tgz"), ("zip", "x-rar", "x-7z", "x-tar", "gzip")),
    "calendar": (("ics",), ("text/calendar",)),
}
# For a file with no extension: the MIME type that means this extension and no other.
EXT_MIME = {"xlsx": "spreadsheetml", "xls": "ms-excel", "ods": "opendocument.spreadsheet", "csv": "text/csv",
            "tsv": "tab-separated", "docx": "wordprocessingml", "doc": "msword", "odt": "opendocument.text",
            "pdf": "application/pdf", "pptx": "presentationml", "ppt": "ms-powerpoint", "ics": "text/calendar",
            "zip": "application/zip", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "txt": "text/plain"}
ALIASES = {"excel": "spreadsheet", "sheet": "spreadsheet", "spreadsheets": "spreadsheet", "word": "document",
           "doc": "document", "documents": "document", "images": "image", "photo": "image", "picture": "image",
           "slides": "presentation", "powerpoint": "presentation", "zip": "archive", "invite": "calendar"}


def _ext(name):
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", name or "")
    return m.group(1).lower() if m else ""


def matches_type(name, mime, want):
    """want: a group name, an alias, or a bare extension (with or without the dot)."""
    want = want.lower().lstrip(".")
    want = ALIASES.get(want, want)
    ext, mime = _ext(name), (mime or "").lower()
    if want in GROUPS:
        exts, mimes = GROUPS[want]
        return ext in exts or any(p in mime for p in mimes)
    # A bare extension; the MIME type answers only when the name has no extension at all.
    if ext:
        return ext == want
    return want in EXT_MIME and EXT_MIME[want] in mime


def find_files(store, types=(), name=None, sender=None, after=None, before=None, min_size=None, max_size=None,
               limit=25, text=None, details=False):
    """Attachments, newest first. Images are left out unless a type asks for them: most are signature logos
    and inline pictures. Every word of `name` must be in the file name; every word of `text` in the file
    name, subject or sender; `sender` is a substring of From. `details` adds what a mail client needs to
    show the message as a row: recipients, folder ids, keywords and preview."""
    where, args = [], []
    for w in (text or "").split():
        where.append("(lower(a.name) LIKE ? OR lower(m.subject) LIKE ? OR lower(m.sender) LIKE ?)")
        args += [f"%{w.lower()}%"] * 3
    for w in (name or "").split():
        where.append("lower(a.name) LIKE ?")
        args.append(f"%{w.lower()}%")
    if sender:
        where.append("lower(m.sender) LIKE ?")
        args.append(f"%{sender.lower()}%")
    if after:
        where.append("m.received_at >= ?")
        args.append(after)
    if before:
        where.append("m.received_at < ?")
        args.append(before)
    if min_size:
        where.append("a.size >= ?")
        args.append(int(min_size))
    if max_size:
        where.append("a.size <= ?")
        args.append(int(max_size))
    sql = ("SELECT a.name, a.type, a.size, a.blob_id, m.id, m.thread_id, m.received_at, m.subject, m.sender, "
           "EXISTS (SELECT 1 FROM attachments x WHERE x.blob_id = a.blob_id AND x.chars > 0), "
           "m.recipients, m.mailboxes, m.keywords, m.preview "
           "FROM attachments a JOIN messages m ON m.id = a.message_id"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY m.received_at DESC, a.name")
    types = [t for t in types if t]
    out = []
    for row in store.db.execute(sql, args):
        fname, mime = row[0] or "", row[1] or ""
        if types:
            if not any(matches_type(fname, mime, t) for t in types):
                continue
        elif matches_type(fname, mime, "image"):
            continue
        out.append({"name": fname, "type": mime, "size": row[2], "date": row[6], "subject": row[7],
                    "sender": row[8], "message_id": row[4], "thread_id": row[5], "blob_id": row[3],
                    "text_indexed": bool(row[9])})
        if details:
            out[-1].update({"recipients": row[10] or "", "mailbox_ids": json.loads(row[11] or "[]"),
                            "keywords": json.loads(row[12] or "[]"), "preview": row[13] or ""})
        if len(out) >= limit:
            break
    return out


def _size(n):
    n = n or 0
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def format_files(files):
    return "\n".join(f"{f['date'][:10]}  {f['name']}  ({_size(f['size'])})\n"
                     f"    {f['sender']}: {f['subject'] or '(no subject)'}  [{f['message_id']}]" for f in files)
