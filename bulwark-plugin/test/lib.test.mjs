import assert from 'node:assert/strict';
import { test } from 'node:test';

import { action, provide, searchUrl, senderName, toResult } from '../src/lib.js';

function fakeApi({ connected = true, status = 200, body = { results: [] }, settings = {} } = {}) {
  const calls = { fetch: [], connect: [] };
  return {
    calls,
    i18n: { t: (k, v) => (v ? `${k}:${JSON.stringify(v)}` : k) },
    log: { warn: () => {} },
    plugin: { settings },
    proxy: {
      accounts: async () => [{ credentialId: 'mailmem', connected, origin: 'http://mm.lan:8095' }],
      fetch: async (cred, url, opts) => { calls.fetch.push({ cred, url, opts }); return { status, ok: status < 300, body }; },
      connect: async (cred, opts) => { calls.connect.push({ cred, opts }); return { connected: true }; },
    },
  };
}

const hit = { thread_id: 't1', message_id: 'm1', subject: 'Lease', sender: 'Ann <a@x.com>', date: '2026-03-02T10:00:00Z',
  snippet: 'the <mark>pet deposit</mark> is 500', attachment: 'lease.pdf', repeats: 1, matching_messages: 3 };

test('short queries are left alone', async () => {
  const api = fakeApi();
  assert.equal(await provide(api, [], 'ab'), undefined);
  assert.equal(api.calls.fetch.length, 0);
});

test('not connected: offers to connect, and the click opens the host dialog', async () => {
  const api = fakeApi({ connected: false });
  const out = await provide(api, [], 'pet deposit');
  assert.equal(out.at(-1).key, 'connect');
  assert.equal(out.at(-1).email, undefined);
  await action(api, { surface: 'search-results', actionId: 'connect' });
  assert.deepEqual(api.calls.connect, [{ cred: 'mailmem', opts: { mode: 'password' } }]);
});

test('results are appended, open the message, and escape the sender', async () => {
  const api = fakeApi({ body: { results: [hit] } });
  const existing = [{ key: 'other', title: 'x', snippet: 'y' }];
  const out = await provide(api, existing, 'pet deposit');
  assert.equal(out.length, 2);
  const r = out[1];
  assert.deepEqual(r.email, { id: 'm1' });
  assert.equal(r.title, 'Lease');
  assert.match(r.snippet, /^Ann · 2026-03-02 · attachment:/);
  assert.match(r.snippet, /<mark>pet deposit<\/mark>/);
  assert.equal(api.calls.fetch[0].url, 'http://mm.lan:8095/search?q=pet+deposit&limit=10');
});

test('a rejected key asks to reconnect; other failures add nothing', async () => {
  assert.equal((await provide(fakeApi({ status: 401 }), [], 'pet deposit')).at(-1).title, 'reconnect.title');
  assert.equal(await provide(fakeApi({ status: 502, body: null }), [], 'pet deposit'), undefined);
});

test('settings reach the query', () => {
  assert.equal(searchUrl('https://mm.example.com/', 'a b', { limit: 8, includeBulk: true }),
    'https://mm.example.com/search?q=a+b&limit=8&bulk=1');
  assert.equal(toResult({ ...hit, subject: '' }, (k) => k).title, '(no subject)');
});

test('sender shown by name', () => {
  assert.equal(senderName('"Lee, Ann" <a@x.com>'), 'Lee, Ann');
  assert.equal(senderName('<a@x.com>'), 'a@x.com');
  assert.equal(senderName('a@x.com'), 'a@x.com');
});
