/**
 * Mail Memory for Bulwark: adds conversations found by meaning to the mail search.
 *
 * The search runs on a Mail Memory server (https://github.com/robertdoneill/mail-memory) through
 * Bulwark's secret proxy. Each user connects with their own read-only API key; the server checks it
 * with the mail server and only searches that user's mail. The plugin never sees the key.
 */
const api = require('@plugin-host');

import { action, provide } from './lib.js';

export const hooks = {
  onProvideSearchResults: (results, { query }) => provide(api, results, query),
  onAction: (ev) => action(api, ev),
};
