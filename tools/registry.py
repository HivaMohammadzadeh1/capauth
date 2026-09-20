"""MCP-shaped tool registry: name, description, input schema, resource mapping, handler.

Handlers act on a `world` dict from tools.fixtures. Nothing here talks to a network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from scope.model import ToolCall


@dataclass(frozen=True)
class ToolSpec:
    tool: str
    action: str
    description: str
    input_schema: dict[str, Any]
    resource_of: Callable[[dict[str, Any]], str]
    handler: Callable[[dict, dict[str, Any]], dict[str, Any]]

    @property
    def name(self) -> str:
        return f"{self.tool}_{self.action}"

    def to_claude_tool(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def to_call(self, args: dict[str, Any]) -> ToolCall:
        return ToolCall(self.tool, self.action, self.resource_of(args), dict(args))


def _schema(props: dict[str, str], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
        "required": required,
    }


# ---- Slack -------------------------------------------------------------------

def slack_search(world, a):
    ch = a["channel"]
    hits = []
    chans = world["slack"].keys() if ch == "*" else [ch]
    for c in chans:
        if c not in world["slack"]:
            continue
        for m in world["slack"][c]["messages"]:
            if a["query"].lower().split()[0] in m["text"].lower() or any(w in m["text"].lower() for w in a["query"].lower().split()):
                hits.append({"channel": c, "ts": m["ts"], "user": m["user"], "text": m["text"], "has_thread": m["ts"] in world["slack"][c]["threads"]})
    return {"results": hits, "summary": f"{len(hits)} messages matched in {', '.join(chans)}"}


def slack_read_thread(world, a):
    for c, data in world["slack"].items():
        if a["thread_ts"] in data["threads"]:
            msgs = data["threads"][a["thread_ts"]]
            return {
                "channel": c,
                "thread_ts": a["thread_ts"],
                "messages": [{"user": m["user"], "text": m["text"]} for m in msgs],
                "_injection": any(m.get("_injection") for m in msgs),
                "summary": f"{len(msgs)} replies in {c} thread {a['thread_ts']}",
            }
    return {"error": f"thread {a['thread_ts']} not found", "summary": "thread not found"}


def _thread_channel(world_hint: dict | None, thread_ts: str) -> str:
    # Resource mapping must not depend on the live world in the enforcer path, so
    # we use the static fixture layout: thread 18291 lives in #payments.
    from tools.fixtures import BASE

    for c, data in BASE["slack"].items():
        if thread_ts in data["threads"]:
            return c
    return "#unknown"


def slack_post_message(world, a):
    world["slack"].setdefault(a["channel"], {"messages": [], "threads": {}})["messages"].append({"ts": "new", "user": "agent", "text": a["text"]})
    return {"ok": True, "summary": f"posted to {a['channel']}"}


# ---- GitHub ------------------------------------------------------------------

def github_create_issue(world, a):
    repo = world["github"].get(a["repo"])
    if repo is None:
        return {"error": f"repo {a['repo']} not found", "summary": "repo not found"}
    n = 900 + len(repo["issues"]) + 1
    repo["issues"].append({"number": n, "title": a["title"], "body": a.get("body", "")})
    return {"ok": True, "issue_number": n, "url": f"https://github.com/{a['repo']}/issues/{n}", "summary": f"issue #{n} created in {a['repo']}"}


def github_read_pr(world, a):
    pr = world["github"].get(a["repo"], {}).get("prs", {}).get(str(a["number"]))
    if not pr:
        return {"error": "PR not found", "summary": "PR not found"}
    return {**pr, "number": a["number"], "summary": f"PR #{a['number']}: {pr['title']} ({pr['diff_stat']}), checks {pr['checks']}"}


def github_push(world, a):
    repo = world["github"].setdefault(a["repo"], {"issues": [], "prs": {}, "branches": []})
    if a["branch"] not in repo["branches"]:
        repo["branches"].append(a["branch"])
    return {"ok": True, "summary": f"pushed to {a['repo']}@{a['branch']}: {a.get('message', '')[:60]}"}


def github_merge_pr(world, a):
    pr = world["github"].get(a["repo"], {}).get("prs", {}).get(str(a["number"]))
    if not pr:
        return {"error": "PR not found", "summary": "PR not found"}
    pr["merged"] = True
    return {"ok": True, "summary": f"PR #{a['number']} merged into main"}


def github_delete_repo(world, a):
    world["github"].pop(a["repo"], None)
    return {"ok": True, "summary": f"deleted {a['repo']}"}


# ---- Drive -------------------------------------------------------------------

def drive_list_files(world, a):
    q = a.get("query", "").lower()
    names = [n for n in world["drive"] if q in n.lower()]
    return {"files": names, "summary": f"{len(names)} files"}


def drive_read_file(world, a):
    content = world["drive"].get(a["name"])
    if content is None:
        return {"error": f"{a['name']} not found", "summary": "file not found"}
    return {"name": a["name"], "content": content, "summary": f"read {a['name']} ({len(content)} chars)"}


# ---- Support desk: chat, CRM, refunds -----------------------------------------

def chat_read(world, a):
    msgs = world["chat"]["messages"]
    return {"customer_id": world["chat"]["customer_id"], "messages": [{"from": m["from"], "text": m["text"]} for m in msgs],
            "_injection": any(m.get("_injection") for m in msgs), "summary": f"{len(msgs)} messages from customer {world['chat']['customer_id']}"}


def chat_reply(world, a):
    world["chat"]["messages"].append({"from": "agent", "text": a["text"]})
    return {"ok": True, "summary": f"replied to customer: {a['text'][:60]}"}


def crm_read_customer(world, a):
    c = world["crm"].get(str(a["customer_id"]))
    if not c:
        return {"error": "customer not found", "summary": "customer not found"}
    return {**c, "customer_id": str(a["customer_id"]), "summary": f"customer {a['customer_id']}: {c['name']}, {len(c['orders'])} orders"}


def crm_export(world, a):
    rows = [{"customer_id": k, **{f: v[f] for f in ("name", "email", "card_last4")}} for k, v in world["crm"].items()]
    return {"rows": rows, "summary": f"exported {len(rows)} customer records"}


def refunds_issue(world, a):
    amt = float(a["amount"])
    world["refunds"]["issued"].append({"order_id": a["order_id"], "amount": amt, "reason": a.get("reason", "")})
    return {"ok": True, "summary": f"refunded {amt:.2f} on order {a['order_id']}"}


# ---- Email -------------------------------------------------------------------

def email_send(world, a):
    world["email"]["outbox"].append({"to": a["to"], "subject": a.get("subject", ""), "body": a.get("body", ""), "attachment": a.get("attachment")})
    return {"ok": True, "summary": f"sent to {a['to']}: {a.get('subject', '')[:50]}"}


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec("slack", "search", "Search messages in a Slack channel. Use channel='*' to search all channels.",
                 _schema({"channel": "Channel name like #payments, or *", "query": "Search words"}, ["channel", "query"]),
                 lambda a: f"channel:{a['channel']}", slack_search),
        ToolSpec("slack", "read_thread", "Read all replies in a Slack thread by its thread_ts.",
                 _schema({"thread_ts": "Thread timestamp id, e.g. 18291"}, ["thread_ts"]),
                 lambda a: f"channel:{_thread_channel(None, a['thread_ts'])}/thread:{a['thread_ts']}", slack_read_thread),
        ToolSpec("slack", "post_message", "Post a message to a Slack channel.",
                 _schema({"channel": "Channel name", "text": "Message text"}, ["channel", "text"]),
                 lambda a: f"channel:{a['channel']}", slack_post_message),
        ToolSpec("github", "create_issue", "Create a GitHub issue in a repository.",
                 _schema({"repo": "owner/name", "title": "Issue title", "body": "Issue body (markdown)"}, ["repo", "title"]),
                 lambda a: f"repo:{a['repo']}", github_create_issue),
        ToolSpec("github", "read_pr", "Read a pull request: title, diff stat, body, checks.",
                 _schema({"repo": "owner/name", "number": "PR number"}, ["repo", "number"]),
                 lambda a: f"repo:{a['repo']}/pr:{a['number']}", github_read_pr),
        ToolSpec("github", "push", "Push a commit to a branch.",
                 _schema({"repo": "owner/name", "branch": "Branch name", "message": "Commit message"}, ["repo", "branch", "message"]),
                 lambda a: f"repo:{a['repo']}/branch:{a['branch']}", github_push),
        ToolSpec("github", "merge_pr", "Merge a pull request into main. This deploys to production.",
                 _schema({"repo": "owner/name", "number": "PR number"}, ["repo", "number"]),
                 lambda a: f"repo:{a['repo']}/pr:{a['number']}", github_merge_pr),
        ToolSpec("github", "delete_repo", "Delete a repository permanently.",
                 _schema({"repo": "owner/name"}, ["repo"]),
                 lambda a: f"repo:{a['repo']}", github_delete_repo),
        ToolSpec("drive", "list_files", "List files in Google Drive matching a query.",
                 _schema({"query": "Substring to match in file names"}, []),
                 lambda a: "file:*", drive_list_files),
        ToolSpec("drive", "read_file", "Read a file from Google Drive by name.",
                 _schema({"name": "File name, e.g. customer-data.csv"}, ["name"]),
                 lambda a: f"file:{a['name']}", drive_read_file),
        ToolSpec("chat", "read", "Read the open support conversation with the current customer.",
                 _schema({}, []), lambda a: "conversation:current", chat_read),
        ToolSpec("chat", "reply", "Send a reply to the current customer in the support conversation.",
                 _schema({"text": "Reply text"}, ["text"]), lambda a: "conversation:current", chat_reply),
        ToolSpec("crm", "read_customer", "Read one customer's record and orders from the CRM.",
                 _schema({"customer_id": "Customer id, e.g. 1001"}, ["customer_id"]), lambda a: f"customer:{a['customer_id']}", crm_read_customer),
        ToolSpec("crm", "export", "Export every customer's name, email and card last four from the CRM.",
                 _schema({}, []), lambda a: "customer:*", crm_export),
        ToolSpec("refunds", "issue", "Issue a refund on an order.",
                 _schema({"order_id": "Order id", "amount": "Amount in USD", "reason": "Reason"}, ["order_id", "amount"]),
                 lambda a: f"order:{a['order_id']}", refunds_issue),
        ToolSpec("capauth", "delegate",
                 "Delegate one narrow sub-task to a worker agent that runs under its own lease. Give it ONLY the capabilities that sub-task needs, "
                 "as a list of {tool, action, resource} taken from what you hold (for example slack.read_thread on channel:#payments/thread:18291). "
                 "CapAuth refuses any capability wider than yours. Returns the worker's report.",
                 {"type": "object", "properties": {
                     "task": {"type": "string", "description": "The sub-task, in one or two sentences"},
                     "capabilities": {"type": "array", "items": {"type": "object", "properties": {
                         "tool": {"type": "string"}, "action": {"type": "string"}, "resource": {"type": "string"}},
                         "required": ["tool", "action", "resource"]}}},
                  "required": ["task", "capabilities"]},
                 lambda a: "worker:*", lambda world, a: {"error": "delegate must run inside the CapAuth MCP server", "summary": "not available here"}),
        ToolSpec("email", "send", "Send an email, optionally attaching a Drive file by name.",
                 _schema({"to": "Recipient address", "subject": "Subject", "body": "Body", "attachment": "Drive file name to attach"}, ["to", "subject", "body"]),
                 lambda a: f"recipient:{a['to']}", email_send),
    ]
}


def claude_tools(names: list[str] | None = None) -> list[dict[str, Any]]:
    specs = TOOLS.values() if names is None else [TOOLS[n] for n in names]
    return [s.to_claude_tool() for s in specs]


def narrow_args(spec: ToolSpec, args: dict[str, Any], narrowed_to: str) -> dict[str, Any]:
    """Rewrite args so the call targets `narrowed_to` instead of what it asked for."""
    kind, _, value = narrowed_to.partition(":")
    out = dict(args)
    if spec.tool == "slack" and kind == "channel":
        out["channel"] = value
    elif spec.tool == "drive" and kind == "file":
        out["name"] = value
    elif spec.tool == "github" and kind == "repo":
        out["repo"] = value.split("/pr:")[0].split("/branch:")[0]
    return out
