"""Hybrid search: vector nearest neighbours + BM25 keywords, merged with reciprocal rank fusion, grouped by thread."""
import json
import re

from .store import pack

K = 200        # candidates from each side
RRF_K = 60
EXACT_BOOST = 0.05


def _fts_query(q):
    """Quoted phrases stay phrases; other words are OR'd so a partial match still ranks."""
    phrases = re.findall(r'"([^"]+)"', q)
    words = re.findall(r"[\w@.\-']+", re.sub(r'"[^"]+"', " ", q))
    terms = [f'"{p}"' for p in phrases] + ['"' + w.replace('"', "") + '"' for w in words if len(w) > 1]
    return " OR ".join(terms)


def _exact_terms(q):
    """Phrases and identifier-like tokens (numbers, emails, codes) that should win when present verbatim."""
    terms = re.findall(r'"([^"]+)"', q)
    terms += [w for w in re.findall(r"[\w@.\-]+", q) if re.search(r"\d|@", w) and len(w) > 2]
    return [t.lower() for t in terms]


def search(store, emb, q, limit=20, after=None, before=None, sender=None, include_bulk=True):
    db = store.db
    scores, best = {}, {}

    vec = db.execute("SELECT rowid FROM passages_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                     (pack(emb.query(q)), K)).fetchall()
    for rank, (pid,) in enumerate(vec):
        scores[pid] = scores.get(pid, 0) + 1 / (RRF_K + rank)

    fq = _fts_query(q)
    if fq:
        kw = db.execute("SELECT rowid FROM passages_fts WHERE passages_fts MATCH ? ORDER BY rank LIMIT ?",
                        (fq, K)).fetchall()
        for rank, (pid,) in enumerate(kw):
            scores[pid] = scores.get(pid, 0) + 1 / (RRF_K + rank)

    if not scores:
        return []
    exact = _exact_terms(q)
    rows = db.execute(f"""
        SELECT p.id, p.text, p.source, m.id, m.thread_id, m.received_at, m.subject, m.sender, m.bulk, m.mailboxes
        FROM passages p JOIN messages m ON m.id = p.message_id
        WHERE p.id IN ({",".join("?" * len(scores))})""", list(scores)).fetchall()

    threads = {}
    for pid, text, source, mid, tid, date, subject, frm, bulk, mailboxes in rows:
        if (after and date < after) or (before and date >= before) or (not include_bulk and bulk):
            continue
        if sender and sender.lower() not in frm.lower():
            continue
        s = scores[pid] + (EXACT_BOOST if any(t in text.lower() or t in subject.lower() for t in exact) else 0)
        t = threads.setdefault(tid, {"thread_id": tid, "score": 0, "messages": set(), "first": date, "last": date})
        t["messages"].add(mid)
        t["first"], t["last"] = min(t["first"], date), max(t["last"], date)
        if s > t["score"]:
            t.update(score=s, subject=subject, sender=frm, date=date, message_id=mid, passage=text, bulk=bool(bulk),
                     attachment=None if source in (None, "body") else source)

    # Repeated notifications (same sender, same subject) arrive as separate threads; show them once.
    groups = {}
    for t in sorted(threads.values(), key=lambda t: -t["score"]):
        key = (_norm_subject(t["subject"]), t["sender"].lower())
        g = groups.get(key)
        if g is None:
            groups[key] = {**t, "repeats": 1}
        else:
            g["repeats"] += 1
            g["messages"] |= t["messages"]
            g["first"], g["last"] = min(g["first"], t["first"]), max(g["last"], t["last"])

    out = sorted(groups.values(), key=lambda t: -t["score"])[:limit]
    for t in out:
        t["matching_messages"] = len(t.pop("messages"))
        t["score"] = round(t["score"], 4)
    return out


def _norm_subject(s):
    return re.sub(r"^((re|fwd?|aw)\s*:\s*)+", "", (s or "").strip(), flags=re.I).lower()


def format_results(results, width=220):
    lines = []
    for i, r in enumerate(results, 1):
        snippet = re.sub(r"\s+", " ", r["passage"])[:width]
        span = r["first"][:10] if r["first"][:10] == r["last"][:10] else f"{r['first'][:10]} → {r['last'][:10]}"
        reps = f"  ×{r['repeats']}" if r.get("repeats", 1) > 1 else ""
        att = f"\n    in attachment {r['attachment']}" if r.get("attachment") else ""
        lines.append(f"{i:>2}. {r['subject'] or '(no subject)'}  [{span}]{reps}{'  [bulk]' if r['bulk'] else ''}\n"
                     f"    {r['sender']}{att}\n    {snippet}")
    return "\n".join(lines)


def to_json(results):
    return json.dumps(results, indent=1)
