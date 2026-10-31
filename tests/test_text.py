from mailmem import text as T


def test_strips_gmail_reply():
    body = "Sounds good, Thursday works.\n\nOn Mon, Oct 27, 2026 at 9:14 AM Jane Doe <jane@example.com>\nwrote:\n> Can the plumber come Thursday?\n> Thanks"
    assert T.strip_quotes(body) == "Sounds good, Thursday works."


def test_strips_outlook_reply():
    body = "Approved.\n\nFrom: Bob Smith <bob@example.com>\nSent: Monday, October 27, 2026 9:14 AM\nTo: Dana\nSubject: Invoice 4417\n\nPlease approve invoice 4417."
    assert T.strip_quotes(body) == "Approved."


def test_strips_signature_and_mobile_footer():
    body = "See attached lease.\n\nSent from my iPhone\n-- \nDana Reyes\nReyes Property Co"
    assert T.strip_quotes(body) == "See attached lease."


def test_bare_forward_keeps_forwarded_text():
    body = "---------- Forwarded message ---------\nFrom: City of Austin\nDate: Oct 1\n\nYour permit 22-0815 was approved."
    assert "permit 22-0815" in T.strip_quotes(body)


def test_ordinary_sentence_starting_with_on_is_kept():
    body = "On Tuesday the HVAC tech replaced the capacitor.\nTotal was $240."
    assert T.strip_quotes(body) == body


def test_chunk_limits_and_overlap():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(20))
    out = T.chunk(text)
    assert all(len(c) <= T.MAX_CHARS + T.OVERLAP + 10 for c in out)
    assert len(out) <= T.MAX_PASSAGES and len(out) > 1


def test_html_drops_quoted_blocks():
    html = '<div>Yes, confirmed.</div><div class="gmail_quote">On Mon wrote:<blockquote>old text</blockquote></div>'
    assert T.html_to_text(html).strip() == "Yes, confirmed."


def test_bulk_gets_one_short_passage():
    email = {"header:List-Unsubscribe": " <mailto:u@x.com>", "subject": "Weekly deals",
             "textBody": [{"partId": "1", "type": "text/plain"}], "bodyValues": {"1": {"value": "Deal " * 500}}}
    bulk, ps = T.passages(email)
    assert bulk and len(ps) == 1 and len(ps[0]) <= T.BULK_CHARS
