"""Turn a JMAP email into clean passages: body text without quoted replies or signatures, split for embedding."""
import html
import re
from html.parser import HTMLParser

MAX_CHARS = 1500       # ~350 tokens: one passage
OVERLAP = 200
MAX_PASSAGES = 12      # cap for very long messages
BULK_CHARS = 800       # newsletters and notifications get one short passage

# A reply or forward starts here; everything from the first match down is quoted history.
# Each is matched at the start of a 5-line window, so "On ... wrote:" may wrap onto a second line.
_CUT = [
    re.compile(r"On .{0,200}?wrote:[ \t]*$", re.I | re.S | re.M),
    re.compile(r"-{2,}\s*(Original|Forwarded) Message\s*-{2,}", re.I),
    re.compile(r"_{20,}[ \t]*$", re.M),
    re.compile(r"From:\s.+\n(.*\n){0,3}?(Sent|Date):\s", re.I),
    re.compile(r"-- ?$", re.M),  # signature delimiter
]
_NOISE = re.compile(r"^(Sent from my (iPhone|iPad|Android|phone)|Get Outlook for \w+).*$", re.I | re.M)


class _Html2Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.quote = [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        # Gmail/Outlook wrap quoted history in these; drop it like plain-text quotes.
        cls = dict(attrs).get("class") or ""
        if tag == "blockquote" or "gmail_quote" in cls or dict(attrs).get("id") == "divRplyFwdMsg":
            self.quote += 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip = max(0, self.skip - 1)
        if tag == "blockquote":
            self.quote = max(0, self.quote - 1)
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip and not self.quote:
            self.out.append(data)


def html_to_text(s):
    p = _Html2Text()
    p.feed(s)
    return html.unescape("".join(p.out))


def body_text(email):
    """Plain-text body from JMAP textBody parts, falling back to HTML parts."""
    values = email.get("bodyValues") or {}
    parts = [values[p["partId"]]["value"] for p in email.get("textBody") or []
             if p.get("partId") in values and p.get("type") == "text/plain"]
    if parts:
        return "\n\n".join(parts)
    parts = [html_to_text(values[p["partId"]]["value"]) for p in email.get("htmlBody") or []
             if p.get("partId") in values]
    return "\n\n".join(parts) or email.get("preview") or ""


def strip_quotes(text):
    """Keep only what this message added: cut at the first reply/forward header or signature, drop '>' lines."""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    for i in range(len(lines)):
        window = "\n".join(lines[i:i + 5])
        if any(rx.match(window) for rx in _CUT):
            lines = lines[:i]
            break
    kept = "\n".join(l for l in lines if not l.lstrip().startswith(">"))
    kept = _NOISE.sub("", kept)
    kept = re.sub(r"[ \t ]+", " ", kept)
    kept = re.sub(r"\n\s*\n+", "\n\n", kept).strip()
    # A bare forward has nothing of its own; index the forwarded content instead.
    return kept or re.sub(r"\s+", " ", text).strip()


def chunk(text, max_chars=MAX_CHARS, overlap=OVERLAP, limit=MAX_PASSAGES):
    """Pack paragraphs into passages of at most max_chars, overlapping by ~overlap chars."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces = []
    for p in paras:  # split oversized paragraphs at sentence ends, then hard
        while len(p) > max_chars:
            cut = p.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 2 else max_chars
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            pieces.append(p)
    out, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > max_chars:
            out.append(cur)
            cur = cur[-overlap:].split(" ", 1)[-1] + "\n\n" + p if overlap else p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out[:limit]


def is_bulk(email):
    prec = (email.get("header:Precedence") or "").strip().lower()
    return bool((email.get("header:List-Unsubscribe") or "").strip()) or prec in ("bulk", "list", "junk")


def addr(lst):
    return ", ".join((a.get("name") + " <" + a["email"] + ">") if a.get("name") else a.get("email", "")
                     for a in lst or [])


def passages(email):
    """(bulk, [passage text]) for one JMAP email."""
    text = strip_quotes(body_text(email))
    if is_bulk(email):
        return True, [text[:BULK_CHARS]] if text else [email.get("subject") or ""]
    return False, chunk(text) or [email.get("subject") or ""]
