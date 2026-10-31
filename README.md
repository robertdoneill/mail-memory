# Mail Memory

Search a JMAP mailbox by what the mail means, not only the words in it. Ask for "the plumber who said he'd
come back Thursday" and get the conversation, even if it never says "plumber".

Mail Memory indexes a mailbox over JMAP into one SQLite file per account. It uses a small embedding model
(EmbeddingGemma 300M) on your own hardware, so no mail goes to a third party. Search combines nearest-neighbour
vectors with BM25 keywords, so exact names, invoice numbers and addresses still match directly. Text from
attachments (PDF, Office documents, plain text, and scanned PDFs through OCR) is indexed too.

It was built for [Stalwart](https://stalw.art) and [Bulwark](https://bulwarkmail.org). Any JMAP server
should work.

## Parts

| Part | What it is |
| --- | --- |
| `mailmem` | Python CLI: `index`, `watch`, `search`, `attachments`, `stats`, `serve` |
| `mailmem serve` | HTTP search API for the Bulwark plugin. Each request is checked against the mail server |
| `bulwark-plugin/` | Bulwark plugin that adds results "by meaning" to Bulwark's search |
| `mailmem.mcp_ssh` | MCP server (stdio) that lets an AI agent search the index over SSH |
| `deploy/` | systemd units for the embedding server, the indexer and the search API |

## Requirements

- Python 3.11+.
- A [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` running
  [EmbeddingGemma 300M Q8_0](https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF) with `--embedding
  --pooling mean`. It runs well on a CPU. An index records the model it was built with and refuses another.
- For attachments: `pdftotext` and `pdftoppm` (poppler), and `tesseract` for scanned PDFs.
- A read-only JMAP API key for each account you index.

## Install

```bash
python3 -m venv /opt/mail-memory/venv
/opt/mail-memory/venv/bin/pip install git+https://github.com/robertdoneill/mail-memory
```

## Index an account

```bash
export MAILMEM_JMAP_URL=https://mail.example.com
export MAILMEM_TOKEN_FILE=/etc/mail-memory/user.token   # a read-only JMAP API key, never on the command line
mailmem --db /srv/mail-memory/user.sqlite index          # first pass; resumable
mailmem --db /srv/mail-memory/user.sqlite watch          # keep it current
mailmem --db /srv/mail-memory/user.sqlite search "the lease renewal we argued about in spring"
```

The embedding server defaults to `http://127.0.0.1:8091`. Set it with `--embed` or `MAILMEM_EMBED_URL`.

`index` skips newsletters and automated mail when it embeds, but it still records them. `search --no-bulk`
leaves them out of results.

## Search from Bulwark

`mailmem serve` answers `GET /search?q=...` for the [Bulwark plugin](bulwark-plugin/). The plugin sends the
user's own JMAP API key through Bulwark's secret proxy, so the plugin never sees it. The server asks the mail
server whose key it is and searches that user's index only. A key that the mail server does not accept gets
`401`.

```bash
cat /etc/mail-memory/accounts.json
{"user@example.com": "/srv/mail-memory/user.sqlite"}

mailmem serve --listen 172.17.0.1:8095 --accounts /etc/mail-memory/accounts.json
```

The search API has no TLS of its own. Keep it on a private address that Bulwark can reach, for example the
Docker bridge with a firewall rule for the Docker range only. Add that origin to Bulwark's
`PLUGIN_PROXY_PRIVATE_ORIGINS`:

```yaml
environment:
  PLUGIN_PROXY_PRIVATE_ORIGINS: "http://172.17.0.1:8095"
```

`deploy/` has systemd units for all three services. They read `MAILMEM_JMAP_URL` from
`/etc/mail-memory/env`.

## Search from an AI agent

`mailmem.mcp_ssh` is a stdio MCP server that runs on your machine. It runs `mailmem search --json` on the
mail server over SSH, so no port is exposed. Results carry JMAP message and thread ids, which a JMAP mail MCP
can open.

```json
{
  "command": "/path/to/venv/bin/python",
  "args": ["-I", "-m", "mailmem.mcp_ssh"],
  "env": {
    "MAILMEM_SSH_HOST": "mail-server",
    "MAILMEM_ACCOUNTS": "{\"user@example.com\": \"/srv/mail-memory/user.sqlite\"}"
  }
}
```

## Tests

```bash
python -m pytest tests
cd bulwark-plugin && npm test
```

## Credits

Inspired by [DigUp](https://github.com/ARahim3/DigUp), Abdur Rahim's search for files by what is in them,
and its Linux port [ShowMe](https://github.com/Simen1331/ShowMe). Mail Memory takes the same idea, a local
embedding model with keyword search alongside, to a mailbox.

## License

MIT
