"""Prototype MCP server (stdio): search a Mail Memory index by meaning, and find attachments, over SSH.

Runs on the user's machine. Each call runs `mailmem search --json` or `mailmem files --json` on the server
through the existing SSH access, so no search port is exposed. Results carry JMAP message and thread ids, which the JMAP mail MCP
can open in full.

Configured by environment:
  MAILMEM_SSH_HOST  the server, as ssh knows it
  MAILMEM_ACCOUNTS  JSON, {"user@example.com": "/srv/mail-memory/user.sqlite"}: index files on the server by
                    the account name the tool accepts. Only these can be searched; the first is the default.
  MAILMEM_REMOTE    the mailmem command on the server (default /opt/mail-memory/venv/bin/mailmem)
"""
import json
import os
import shlex
import subprocess
import sys

HOST = os.environ.get("MAILMEM_SSH_HOST", "")
REMOTE = os.environ.get("MAILMEM_REMOTE", "/opt/mail-memory/venv/bin/mailmem")
ACCOUNTS = json.loads(os.environ.get("MAILMEM_ACCOUNTS") or "{}")
DEFAULT = next(iter(ACCOUNTS), None)

TOOL = {
    "name": "search_mail_by_meaning",
    "description": (
        "Search the user's mail by meaning as well as keywords: describe what happened in plain words "
        "(\"the payment company that promised webhooks in October\") and get the best-matching conversations, "
        "newest-relevant first, each with the passage that matched. Exact names, invoice numbers and email "
        "addresses still match directly. Use this before keyword search when you don't know the exact words. "
        "Returns JMAP message_id/thread_id; open full messages with the JMAP mail tools."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you're looking for, in plain words"},
            "account": {"type": "string", "enum": list(ACCOUNTS), "default": DEFAULT},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
            "after": {"type": "string", "description": "Only mail on or after this date, YYYY-MM-DD"},
            "before": {"type": "string", "description": "Only mail before this date, YYYY-MM-DD"},
            "from": {"type": "string", "description": "Sender name or address contains this"},
            "include_bulk": {"type": "boolean", "default": False,
                             "description": "Include newsletters and automated notifications"},
        },
        "required": ["query"],
    },
}


FILES_TOOL = {
    "name": "find_attachments",
    "description": (
        "Find files attached to the user's mail by type, file name, sender and date: \"the spreadsheet Judith "
        "sent in August\" is type=spreadsheet, from=judith, after=2026-08-01. Exact, not by meaning: use this "
        "whenever the user is after a file (an xlsx, a PDF, a contract, a photo), and search_mail_by_meaning when "
        "they describe what the mail was about. Images are left out unless type asks for them. Returns each "
        "file with its message_id, thread_id and blob_id; text_indexed means the file's text is also covered "
        "by search_mail_by_meaning."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "type": {"type": "array", "items": {"type": "string"},
                     "description": "Any of: spreadsheet, document, pdf, presentation, image, archive, calendar, "
                                    "or a file extension such as xlsx or csv"},
            "name": {"type": "string", "description": "Words that must all appear in the file name"},
            "account": {"type": "string", "enum": list(ACCOUNTS), "default": DEFAULT},
            "from": {"type": "string", "description": "Sender name or address contains this"},
            "after": {"type": "string", "description": "Only mail on or after this date, YYYY-MM-DD"},
            "before": {"type": "string", "description": "Only mail before this date, YYYY-MM-DD"},
            "min_size": {"type": "integer", "description": "Smallest file size in bytes"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        },
    },
}


def _db(args):
    if not HOST or not ACCOUNTS:
        raise RuntimeError("set MAILMEM_SSH_HOST and MAILMEM_ACCOUNTS for the mail-memory MCP server")
    account = args.get("account") or DEFAULT
    if account not in ACCOUNTS:
        raise ValueError(f"unknown account {account}")
    return account, ACCOUNTS[account]


def _remote(cmd):
    # ssh joins its arguments into one remote shell command, so every piece is quoted.
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, " ".join(shlex.quote(c) for c in cmd)],
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.strip()[-500:] or f"{cmd[3]} failed ({p.returncode})")
    return json.loads(p.stdout)


def run_search(args):
    account, db = _db(args)
    cmd = [REMOTE, "--db", db, "search", "--json", "-n", str(int(args.get("limit") or 10)), args["query"]]
    for flag, key in (("--after", "after"), ("--before", "before"), ("--from", "from")):
        if args.get(key):
            cmd += [flag, str(args[key])]
    if not args.get("include_bulk"):
        cmd.append("--no-bulk")
    results = _remote(cmd)
    for r in results:
        r["passage"] = r["passage"][:1200]
    return {"account": account, "results": results}


def files_command(args):
    """The remote command for find_attachments; separate so it can be tested without ssh."""
    _, db = _db(args)
    types = args.get("type") or []
    if isinstance(types, str):
        types = [types]
    cmd = [REMOTE, "--db", db, "files", "--json", "-n", str(min(int(args.get("limit") or 25), 100))]
    for t in types:
        cmd += ["--type", str(t)]
    for flag, key in (("--name", "name"), ("--from", "from"), ("--after", "after"), ("--before", "before"),
                      ("--min-size", "min_size")):
        if args.get(key):
            cmd += [flag, str(args[key])]
    return cmd


def run_files(args):
    account, _ = _db(args)
    return {"account": account, "files": _remote(files_command(args))}


HANDLERS = {TOOL["name"]: run_search, FILES_TOOL["name"]: run_files}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        method, id_ = req.get("method"), req.get("id")
        if id_ is None:  # notifications need no answer
            continue
        if method == "initialize":
            reply(id_, {"protocolVersion": req["params"].get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mail-memory", "version": "0.2.0"}})
        elif method == "tools/list":
            reply(id_, {"tools": [TOOL, FILES_TOOL]})
        elif method == "tools/call":
            try:
                name = req["params"].get("name")
                if name not in HANDLERS:
                    raise ValueError(f"no tool {name}")
                out = HANDLERS[name](req["params"].get("arguments") or {})
                reply(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]})
            except Exception as e:
                reply(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        elif method == "ping":
            reply(id_, {})
        else:
            reply(id_, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    main()
