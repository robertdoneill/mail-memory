"""Text from attachments: PDF (pdftotext, optional OCR for scans), Office files, plain text and HTML."""
import os
import re
import subprocess
import tempfile
import zipfile
from html import unescape

from .text import html_to_text

MAX_BYTES = 25_000_000
OCR = os.environ.get("MAILMEM_OCR") == "1"   # scanned PDFs; slow on a small CPU, so off unless asked
OCR_PAGES = 10

OFFICE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}


def kind(att):
    """What extractor applies, or None to skip (images, calendar invites, unknown binaries, oversized)."""
    t = (att.get("type") or "").lower()
    name = (att.get("name") or "").lower()
    if (att.get("size") or 0) > MAX_BYTES:
        return None
    if t == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if t in OFFICE:
        return OFFICE[t]
    for ext in ("docx", "xlsx", "pptx"):
        if name.endswith("." + ext):
            return ext
    if t in ("text/plain", "text/csv", "text/markdown") or name.endswith((".txt", ".csv", ".md")):
        return "text"
    if t == "text/html" or name.endswith((".html", ".htm")):
        return "html"
    return None


def _xml_text(xml, para_tag):
    xml = re.sub(rf"</{para_tag}>", "\n", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml))


def _office(data, k):
    with tempfile.TemporaryFile() as f:
        f.write(data)
        f.seek(0)
        z = zipfile.ZipFile(f)
        names = z.namelist()
        if k == "docx":
            return _xml_text(z.read("word/document.xml").decode("utf-8", "ignore"), "w:p")
        if k == "pptx":
            slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.findall(r"\d+", n)[-1]))
            return "\n\n".join(_xml_text(z.read(n).decode("utf-8", "ignore"), "a:p") for n in slides)
        if k == "xlsx" and "xl/sharedStrings.xml" in names:  # cell text; numbers alone carry little meaning
            return _xml_text(z.read("xl/sharedStrings.xml").decode("utf-8", "ignore"), "si")
    return ""


def _pdf(data):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "a.pdf")
        with open(src, "wb") as f:
            f.write(data)
        p = subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", src, "-"], capture_output=True, timeout=120)
        text = p.stdout.decode("utf-8", "ignore")
        if len(text.strip()) >= 200 or not OCR:
            return text
        # Little or no text layer: a scan. Render the first pages and OCR them.
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-l", str(OCR_PAGES), src, os.path.join(d, "p")],
                       capture_output=True, timeout=300)
        pages = sorted(n for n in os.listdir(d) if n.startswith("p") and n.endswith(".png"))
        out = []
        for n in pages:
            r = subprocess.run(["tesseract", os.path.join(d, n), "-", "--psm", "3"], capture_output=True, timeout=120)
            out.append(r.stdout.decode("utf-8", "ignore"))
        return "\n\n".join(out)


def extract(att, data):
    """Plain text of one attachment ('' when nothing usable). Never raises: one bad file must not stop indexing."""
    k = kind(att)
    try:
        if k == "pdf":
            text = _pdf(data)
        elif k in ("docx", "xlsx", "pptx"):
            text = _office(data, k)
        elif k == "text":
            text = data.decode("utf-8", "ignore")
        elif k == "html":
            text = html_to_text(data.decode("utf-8", "ignore"))
        else:
            return ""
    except Exception:
        return ""
    # Some PDF fonts map the space glyph to a control character (often U+0003); treat them all as spaces.
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    text = re.sub(r"[ \t \f]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
