// Search a Mail Memory server through Bulwark's secret proxy and turn its answer into search results.
// Kept free of the host import so it can be tested with a fake api.

export const CREDENTIAL = 'mailmem';
export const MIN_QUERY = 3;

const escape = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export async function account(api) {
  const all = await api.proxy.accounts();
  return all.find((a) => a.credentialId === CREDENTIAL && a.connected) || null;
}

export function searchUrl(origin, query, settings) {
  const p = new URLSearchParams({ q: query, limit: String(settings.limit ?? 10) });
  if (settings.includeBulk) p.set('bulk', '1');
  return `${origin.replace(/\/+$/, '')}/search?${p}`;
}

/** "Ann Lee <ann@x.com>" -> "Ann Lee"; a bare address stays as it is. */
export function senderName(sender) {
  const s = String(sender ?? '').trim();
  const name = s.replace(/<[^>]*>/g, '').trim().replace(/^["']|["']$/g, '').trim();
  return name || s.replace(/[<>]/g, '');
}

/** One search result per conversation. The server's snippet is already escaped and marked. */
export function toResult(r, t) {
  const date = (r.date || '').slice(0, 10);
  const parts = [escape(senderName(r.sender)), date];
  if (r.attachment) parts.push(escape(t('attachment', { name: r.attachment })));
  if ((r.repeats || 1) > 1 || (r.matching_messages || 1) > 1) {
    parts.push(escape(t('times', { count: Math.max(r.repeats || 1, r.matching_messages || 1) })));
  }
  return {
    key: `m:${r.thread_id}`,
    title: r.subject || '(no subject)',
    snippet: `${parts.filter(Boolean).join(' · ')} — ${r.snippet}`,
    source: t('source'),
    email: { id: r.message_id },
  };
}

function connectEntry(t, again) {
  const k = again ? 'reconnect' : 'connect';
  return { key: 'connect', title: t(`${k}.title`), snippet: escape(t(`${k}.snippet`)), source: t('source') };
}

/** The onProvideSearchResults hook: results from Mail Memory added after the ones already there. */
export async function provide(api, results, query) {
  const q = (query || '').trim();
  if (q.length < MIN_QUERY) return undefined;
  const t = (k, v) => api.i18n.t(k, v);
  const acct = await account(api);
  if (!acct) return [...results, connectEntry(t, false)];
  const res = await api.proxy.fetch(CREDENTIAL, searchUrl(acct.origin, q, api.plugin.settings || {}),
    { responseType: 'json' });
  if (res.status === 401) return [...results, connectEntry(t, true)];
  if (!res.ok || !res.body || !Array.isArray(res.body.results)) {
    api.log.warn(`Mail Memory search failed: ${res.status} ${res.proxyError || ''}`);
    return undefined;
  }
  return [...results, ...res.body.results.map((r) => toResult(r, t))];
}

export async function action(api, { surface, actionId }) {
  if (surface === 'search-results' && actionId === 'connect') {
    await api.proxy.connect(CREDENTIAL, { mode: 'password' });
  }
}
