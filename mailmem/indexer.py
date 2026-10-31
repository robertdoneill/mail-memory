"""Builds and keeps the index current: a resumable first pass, then Email/changes from the saved state."""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import text as T
from .extract import extract, kind

BATCH = 50  # emails fetched per round trip; their passages are embedded together
# Passages per embedding request. A GPU keeps up with bigger requests; on a small CPU 32 is plenty.
EMBED_BATCH = int(os.environ.get("MAILMEM_EMBED_BATCH", "32"))
MAX_ATTACHMENT_PASSAGES = 20


def _prepare(jm, ids, seen_blobs):
    """Network and extraction work for one batch (runs ahead, off the embedding thread).
    Returns [(email, bulk, [(source, title, text)], [(blob_id, name, type, size, chars)])]."""
    out = []
    for m in jm.get_emails(ids):
        bulk, body = T.passages(m)
        items = [("body", m.get("subject"), t) for t in body]
        atts = []
        for a in m.get("attachments") or []:
            blob, name = a.get("blobId"), a.get("name") or "attachment"
            if not blob:
                continue
            chars = 0
            if blob not in seen_blobs and kind(a) and not bulk:
                text = extract(a, jm.download(blob, name, a.get("type")))
                if text:
                    seen_blobs.add(blob)
                    chars = len(text)
                    chunks = T.chunk(text, limit=MAX_ATTACHMENT_PASSAGES)
                    items += [(name, f"{name} ({m.get('subject') or 'no subject'})", c) for c in chunks]
            atts.append((blob, name, a.get("type"), a.get("size"), chars))
        out.append((m, bulk, items, atts))
    return out


def _store_batch(store, emb, prepared):
    flat = [(title, text) for _, _, items, _ in prepared for _, title, text in items]
    vectors = []
    for i in range(0, len(flat), EMBED_BATCH):
        vectors += emb.documents(flat[i:i + EMBED_BATCH])
    pos = 0
    for m, bulk, items, atts in prepared:
        n = len(items)
        store.add_message(m, T.addr(m.get("from")), T.addr((m.get("to") or []) + (m.get("cc") or [])),
                          bulk, [(src, text) for src, _, text in items], vectors[pos:pos + n], atts)
        pos += n
    store.db.commit()
    return len(flat)


def _run(store, jm, emb, ids, label):
    """Index the given ids in batches, downloading and extracting the next batch while this one embeds."""
    seen = store.known_blobs()
    total, passages, t0 = len(ids), 0, time.time()
    starts = list(range(0, total, BATCH))
    with ThreadPoolExecutor(max_workers=1) as pool:
        nxt = pool.submit(_prepare, jm, ids[0:BATCH], seen) if starts else None
        for n, i in enumerate(starts):
            prepared = nxt.result()
            if n + 1 < len(starts):
                j = starts[n + 1]
                nxt = pool.submit(_prepare, jm, ids[j:j + BATCH], seen)
            passages += _store_batch(store, emb, prepared)
            if label:
                rate = passages / max(time.time() - t0, 1e-6)
                print(f"  {label}: {min(i + BATCH, total)}/{total} messages, {passages} passages, "
                      f"{rate:.1f} passages/s", file=sys.stderr)


def full(store, jm, emb):
    """Index every message not yet in the store, oldest first. Safe to interrupt and rerun."""
    if store.get_meta("email_state"):
        return sync(store, jm, emb)
    state = jm.email_state()  # taken first, so changes during the pass are picked up by sync
    known = store.known_ids()
    todo = [i for i in jm.all_email_ids() if i not in known]
    print(f"{jm.username}: {len(known)} already indexed, {len(todo)} to go", file=sys.stderr)
    _run(store, jm, emb, todo, "index")
    store.set_meta("email_state", state)
    store.db.commit()
    return sync(store, jm, emb)


def attachments(store, jm, emb):
    """Re-index messages whose attachments were never extracted (indexes built before attachment support)."""
    ids = store.messages_missing_attachments()
    print(f"{jm.username}: {len(ids)} messages with attachments to re-index", file=sys.stderr)
    _run(store, jm, emb, ids, "attachments")


def sync(store, jm, emb):
    """Apply changes since the saved state: new mail embedded, moves and flags updated, deletions removed."""
    since = store.get_meta("email_state")
    for created, updated, destroyed, new_state in jm.changes(since):
        for mid in destroyed:
            store.delete_message(mid)
        known = store.known_ids()
        fresh = [i for i in created + updated if i not in known]
        moved = [i for i in updated if i in known]
        _run(store, jm, emb, fresh, None)
        for m in jm.get_emails(moved) if moved else []:
            store.update_flags(m)
        store.set_meta("email_state", new_state)
        store.db.commit()
        if created or updated or destroyed:
            print(f"sync: +{len(fresh)} new, {len(moved)} changed, -{len(destroyed)} deleted", file=sys.stderr)
