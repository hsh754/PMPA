#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_USER = "peter.huang@email.com"
TEAM_USER = "alice.chen@email.com"


def default_state_path():
    configured = os.environ.get("VIRTUAL_EMAIL_STATE")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "environment" / "virtual-email" / "mailbox.json"


DEFAULT_STATE = default_state_path()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_addresses(values):
    addresses = []
    for value in values or []:
        addresses.extend(part.strip() for part in value.split(",") if part.strip())
    return addresses


def initial_state():
    return {
        "version": 1,
        "owner": DEFAULT_USER,
        "current_user": DEFAULT_USER,
        "accounts": {
            DEFAULT_USER: {
                "inbox": ["msg_000001"],
                "sent": ["msg_000002"],
            },
            TEAM_USER: {
                "inbox": ["msg_000002"],
                "sent": ["msg_000001"],
            },
        },
        "messages": {
            "msg_000001": {
                "id": "msg_000001",
                "thread_id": "thread_000001",
                "from": TEAM_USER,
                "to": [DEFAULT_USER],
                "cc": [],
                "subject": "Project onboarding checklist",
                "body": "Hi Peter, please review the harness onboarding checklist before the next evaluation run.",
                "timestamp": "2026-07-03T05:19:04Z",
                "read": False,
                "reply_to": None,
            },
            "msg_000002": {
                "id": "msg_000002",
                "thread_id": "thread_000002",
                "from": DEFAULT_USER,
                "to": [TEAM_USER],
                "cc": [],
                "subject": "Environment reset confirmation",
                "body": "The local virtual email environment has been reset with deterministic seed data.",
                "timestamp": "2026-07-03T05:20:04Z",
                "read": True,
                "reply_to": None,
            },
        },
        "next_message": 3,
        "next_thread": 3,
    }


def empty_state():
    return initial_state()


def load_state(path):
    if not path.exists():
        return initial_state()
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("version", 1)
    state.setdefault("owner", DEFAULT_USER)
    state.setdefault("current_user", state["owner"])
    state.setdefault("accounts", {})
    state.setdefault("messages", {})
    state.setdefault("next_message", 1)
    state.setdefault("next_thread", 1)
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def ensure_account(state, account):
    state["accounts"].setdefault(account, {"inbox": [], "sent": []})


def next_id(state, key, prefix):
    value = state[key]
    state[key] += 1
    return f"{prefix}_{value:06d}"


def visible_ids(state, account, box):
    ensure_account(state, account)
    if box == "all":
        seen = []
        for msg_id in state["accounts"][account]["inbox"] + state["accounts"][account]["sent"]:
            if msg_id not in seen:
                seen.append(msg_id)
        return seen
    return list(state["accounts"][account][box])


def summary(message):
    return {
        "id": message["id"],
        "thread_id": message["thread_id"],
        "from": message["from"],
        "to": message["to"],
        "cc": message["cc"],
        "subject": message["subject"],
        "timestamp": message["timestamp"],
        "read": message["read"],
        "reply_to": message["reply_to"],
    }


def emit(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def require_visible(state, account, msg_id):
    if msg_id not in state["messages"]:
        raise SystemExit(f"message not found: {msg_id}")
    if msg_id not in visible_ids(state, account, "all"):
        raise SystemExit(f"message {msg_id} is not visible to account {account}")
    return state["messages"][msg_id]


def cmd_init(args):
    state = load_state(args.state)
    for account in args.account:
        ensure_account(state, account)
    save_state(args.state, state)
    emit({"ok": True, "action": "init", "state": str(args.state), "accounts": sorted(state["accounts"])})


def cmd_reset(args):
    state = initial_state()
    for account in args.account:
        ensure_account(state, account)
    save_state(args.state, state)
    emit({"ok": True, "action": "reset", "state": str(args.state), "accounts": sorted(state["accounts"])})


def cmd_send(args):
    state = load_state(args.state)
    to = parse_addresses(args.to)
    cc = parse_addresses(args.cc)
    if not to:
        raise SystemExit("send requires at least one --to address")

    ensure_account(state, args.sender)
    for account in to + cc:
        ensure_account(state, account)

    msg_id = next_id(state, "next_message", "msg")
    thread_id = args.thread_id or next_id(state, "next_thread", "thread")
    body = args.body_file.read_text(encoding="utf-8") if args.body_file else args.body
    message = {
        "id": msg_id,
        "thread_id": thread_id,
        "from": args.sender,
        "to": to,
        "cc": cc,
        "subject": args.subject,
        "body": body,
        "timestamp": now_iso(),
        "read": False,
        "reply_to": args.reply_to,
    }

    state["messages"][msg_id] = message
    state["accounts"][args.sender]["sent"].append(msg_id)
    for account in to + cc:
        state["accounts"][account]["inbox"].append(msg_id)
    save_state(args.state, state)

    if args.full:
        emit(message)
    else:
        emit({
            "ok": True,
            "action": "send",
            "id": msg_id,
            "thread_id": thread_id,
            "from": args.sender,
            "to": to,
            "cc": cc,
            "subject": args.subject,
            "box_updates": [f"{args.sender}:sent"] + [f"{account}:inbox" for account in to + cc],
        })


def cmd_list(args):
    state = load_state(args.state)
    messages = [summary(state["messages"][msg_id]) for msg_id in visible_ids(state, args.account, args.box) if msg_id in state["messages"]]
    if args.unread:
        messages = [message for message in messages if not message["read"]]
    emit({"ok": True, "action": "list", "account": args.account, "box": args.box, "count": len(messages), "messages": messages})


def cmd_read(args):
    state = load_state(args.state)
    message = require_visible(state, args.account, args.id)
    if args.mark_read:
        message["read"] = True
        save_state(args.state, state)
    emit(message if args.full else {"ok": True, "action": "read", "account": args.account, "message": message})


def cmd_search(args):
    state = load_state(args.state)
    query = args.query.lower()
    matches = []
    for msg_id in visible_ids(state, args.account, args.box):
        message = state["messages"].get(msg_id)
        if not message:
            continue
        haystack = " ".join([message["from"], " ".join(message["to"]), " ".join(message["cc"]), message["subject"], message["body"]]).lower()
        if query in haystack:
            matches.append(summary(message))
    emit({"ok": True, "action": "search", "account": args.account, "box": args.box, "query": args.query, "count": len(matches), "messages": matches})


def cmd_reply(args):
    state = load_state(args.state)
    original = require_visible(state, args.account, args.id)
    subject = original["subject"] if original["subject"].lower().startswith("re:") else f"Re: {original['subject']}"
    send_args = argparse.Namespace(
        state=args.state,
        sender=args.account,
        to=[original["from"]],
        cc=[],
        subject=subject,
        body=args.body_file.read_text(encoding="utf-8") if args.body_file else args.body,
        body_file=None,
        reply_to=original["id"],
        thread_id=original["thread_id"],
        full=args.full,
    )
    cmd_send(send_args)


def cmd_mark(args):
    state = load_state(args.state)
    message = require_visible(state, args.account, args.id)
    message["read"] = args.read
    save_state(args.state, state)
    emit({"ok": True, "action": "mark", "account": args.account, "id": args.id, "read": args.read})


def add_state_arg(parser):
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="Mailbox state path")


def bool_arg(value):
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser():
    parser = argparse.ArgumentParser(description="Local virtual email mailbox")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    add_state_arg(p)
    p.add_argument("--account", action="append", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("reset")
    add_state_arg(p)
    p.add_argument("--account", action="append", default=[])
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("send")
    add_state_arg(p)
    p.add_argument("--from", dest="sender", required=True)
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--subject", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--body-file", type=Path)
    p.add_argument("--reply-to")
    p.add_argument("--thread-id")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("list")
    add_state_arg(p)
    p.add_argument("--account", required=True)
    p.add_argument("--box", choices=["inbox", "sent", "all"], default="inbox")
    p.add_argument("--unread", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("read")
    add_state_arg(p)
    p.add_argument("--account", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--mark-read", action="store_true")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("search")
    add_state_arg(p)
    p.add_argument("--account", required=True)
    p.add_argument("--box", choices=["inbox", "sent", "all"], default="all")
    p.add_argument("--query", required=True)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("reply")
    add_state_arg(p)
    p.add_argument("--account", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--body-file", type=Path)
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_reply)

    p = sub.add_parser("mark")
    add_state_arg(p)
    p.add_argument("--account", required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--read", type=bool_arg, required=True)
    p.set_defaults(func=cmd_mark)

    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
