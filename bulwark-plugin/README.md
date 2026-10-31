# Mail Memory for Bulwark

Finds mail by what it means, not only the words in it. Type "the plumber who said he'd come back Thursday"
into Bulwark's search. Mail Memory adds the best-matching conversations under **By meaning**, including text
found in attachments. Click one to open it.

It needs a [Mail Memory server](https://github.com/robertdoneill/mail-memory) that indexes your mailbox. The
server runs a small embedding model on your own hardware, so no mail leaves your servers.

## Setup

1. Run the Mail Memory search API (`mailmem serve`) somewhere Bulwark can reach. If it is on a private
   address, add it to Bulwark's `PLUGIN_PROXY_PRIVATE_ORIGINS`. See the
   [server README](https://github.com/robertdoneill/mail-memory#search-from-bulwark).
2. Install the plugin. Search for anything, then choose **Connect Mail Memory**.
3. Enter the server address, for example `http://172.17.0.1:8095`. Then enter a **read-only JMAP API key**
   for your account. You create the key in your mail account's security settings.

The server checks the key with your mail server and searches only that account's mail.

## Permissions

- `email:read` is required by the search-results hook that this plugin uses to add results.
- `http:proxy` sends the search to your Mail Memory server through Bulwark's proxy. Bulwark adds your API key
  to the request, so the plugin never sees the key.

The plugin makes no other requests and collects nothing.

## Settings

- **Results by meaning**: how many conversations to add, from 1 to 20. The default is 10.
- **Include newsletters and notifications**: off by default.

## Build

```bash
npm ci
npm test
npm run build      # dist/index.js
```
