"""Prototype MCP server (stdio): search a Mail Memory index by meaning, run on the mail server over SSH.

Runs on the user's machine. Each call runs `mailmem search --json` on the server through the existing SSH
access, so no search port is exposed. Results carry JMAP message and thread ids, which the JMAP mail MCP
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


def run_search(args):
    if not HOST or not ACCOUNTS:
        raise RuntimeError("set MAILMEM_SSH_HOST and MAILMEM_ACCOUNTS for the mail-memory MCP server")
    account = args.get("account") or DEFAULT
    if account not in ACCOUNTS:
        raise ValueError(f"unknown account {account}")
    cmd = [REMOTE, "--db", ACCOUNTS[account], "search", "--json", "-n", str(int(args.get("limit") or 10)),
           args["query"]]
    for flag, key in (("--after", "after"), ("--before", "before"), ("--from", "from")):
        if args.get(key):
            cmd += [flag, str(args[key])]
    if not args.get("include_bulk"):
        cmd.append("--no-bulk")
    # ssh joins its arguments into one remote shell command, so every piece is quoted.
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, " ".join(shlex.quote(c) for c in cmd)],
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.strip()[-500:] or f"search failed ({p.returncode})")
    results = json.loads(p.stdout)
    for r in results:
        r["passage"] = r["passage"][:1200]
    return {"account": account, "results": results}


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
                        "serverInfo": {"name": "mail-memory", "version": "0.0.1"}})
        elif method == "tools/list":
            reply(id_, {"tools": [TOOL]})
        elif method == "tools/call":
            try:
                out = run_search(req["params"].get("arguments") or {})
                reply(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]})
            except Exception as e:
                reply(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        elif method == "ping":
            reply(id_, {})
        else:
            reply(id_, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    main()
