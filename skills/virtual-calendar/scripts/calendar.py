#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, time, timezone
from pathlib import Path


DEFAULT_TIMEZONE = "Pacific/Auckland"
DEFAULT_USER = "peter.huang@email.com"
TEAM_USER = "alice.chen@email.com"


def default_state_path():
    configured = os.environ.get("VIRTUAL_CALENDAR_STATE")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "environment" / "virtual-calendar" / "calendar.json"


DEFAULT_STATE = default_state_path()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def empty_state():
    return {
        "version": 1,
        "owner": DEFAULT_USER,
        "current_user": DEFAULT_USER,
        "calendars": {
            "primary": {
                "id": "primary",
                "name": "Primary",
                "description": "Default local simulated calendar",
                "owner": DEFAULT_USER,
            }
        },
        "events": {
            "evt_000001": {
                "id": "evt_000001",
                "calendar_id": "primary",
                "title": "Harness Environment Review",
                "description": "Review the seeded virtual environment state and confirm evaluation readiness.",
                "start": "2026-07-07T10:00:00+12:00",
                "end": "2026-07-07T11:00:00+12:00",
                "timezone": DEFAULT_TIMEZONE,
                "location": "Virtual Lab",
                "attendees": [
                    {
                        "email": TEAM_USER,
                        "response": "needsAction",
                    }
                ],
                "created_at": "2026-07-03T05:19:04Z",
                "updated_at": "2026-07-03T05:19:04Z",
            }
        },
        "next_event": 2,
    }


def load_state(path):
    if not path.exists():
        return empty_state()
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("version", 1)
    state.setdefault("owner", DEFAULT_USER)
    state.setdefault("current_user", state["owner"])
    state.setdefault("calendars", {})
    state.setdefault("events", {})
    state.setdefault("next_event", 1)
    state["calendars"].setdefault(
        "primary",
        {
            "id": "primary",
            "name": "Primary",
            "description": "Default local simulated calendar",
            "owner": DEFAULT_USER,
        },
    )
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def emit(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def next_event_id(state):
    value = state["next_event"]
    state["next_event"] += 1
    return f"evt_{value:06d}"


def parse_dt(value, end_of_day=False):
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        if "T" in text:
            return datetime.fromisoformat(text)
        parsed_date = datetime.fromisoformat(text).date()
        return datetime.combine(parsed_date, time.max if end_of_day else time.min)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date/datetime: {value}") from exc


def event_sort_key(event):
    try:
        return comparable_dt(parse_dt(event["start"])) or datetime.max
    except argparse.ArgumentTypeError:
        return datetime.max


def comparable_dt(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def normalize_attendees(values):
    attendees = []
    for value in values or []:
        for part in value.split(","):
            email = part.strip()
            if email:
                attendees.append({"email": email, "response": "needsAction"})
    return attendees


def calendar_summary(calendar):
    return {
        "id": calendar["id"],
        "name": calendar.get("name", calendar["id"]),
        "description": calendar.get("description", ""),
        "owner": calendar.get("owner", DEFAULT_USER),
    }


def event_summary(event):
    return {
        "id": event["id"],
        "calendar_id": event["calendar_id"],
        "title": event["title"],
        "start": event["start"],
        "end": event["end"],
        "timezone": event.get("timezone", DEFAULT_TIMEZONE),
        "location": event.get("location", ""),
        "attendees": [attendee["email"] for attendee in event.get("attendees", [])],
        "created_at": event.get("created_at"),
        "updated_at": event.get("updated_at"),
    }


def in_range(event, start, end):
    event_start = comparable_dt(parse_dt(event["start"]))
    event_end = comparable_dt(parse_dt(event["end"]))
    start = comparable_dt(start)
    end = comparable_dt(end)
    if start and event_end < start:
        return False
    if end and event_start > end:
        return False
    return True


def visible_events(state, calendar_id=None, start=None, end=None):
    events = list(state["events"].values())
    if calendar_id:
        events = [event for event in events if event["calendar_id"] == calendar_id]
    if start or end:
        events = [event for event in events if in_range(event, start, end)]
    return sorted(events, key=event_sort_key)


def require_event(state, event_id):
    event = state["events"].get(event_id)
    if not event:
        raise SystemExit(f"event not found: {event_id}")
    return event


def ensure_calendar(state, calendar_id):
    state["calendars"].setdefault(
        calendar_id,
        {
            "id": calendar_id,
            "name": calendar_id,
            "description": "Local simulated calendar",
            "owner": DEFAULT_USER,
        },
    )


def cmd_reset(args):
    state = empty_state()
    for calendar_id in args.calendar:
        ensure_calendar(state, calendar_id)
    save_state(args.state, state)
    emit(
        {
            "ok": True,
            "action": "reset",
            "state": str(args.state),
            "calendars": [calendar_summary(cal) for cal in state["calendars"].values()],
            "event_count": len(state["events"]),
        }
    )


def cmd_list(args):
    state = load_state(args.state)
    start = parse_dt(args.start)
    end = parse_dt(args.end, end_of_day=True)
    events = visible_events(state, args.calendar, start, end)
    emit(
        {
            "ok": True,
            "action": "list",
            "state": str(args.state),
            "calendar": args.calendar,
            "from": args.start,
            "to": args.end,
            "calendars": [calendar_summary(cal) for cal in state["calendars"].values()],
            "count": len(events),
            "events": [event_summary(event) for event in events],
        }
    )


def cmd_read(args):
    state = load_state(args.state)
    event = require_event(state, args.id)
    emit({"ok": True, "action": "read", "event": event})


def cmd_search(args):
    state = load_state(args.state)
    query = args.query.lower()
    start = parse_dt(args.start)
    end = parse_dt(args.end, end_of_day=True)
    matches = []
    for event in visible_events(state, args.calendar, start, end):
        haystack = " ".join(
            [
                event.get("title", ""),
                event.get("description", ""),
                event.get("location", ""),
                event.get("calendar_id", ""),
                " ".join(attendee.get("email", "") for attendee in event.get("attendees", [])),
            ]
        ).lower()
        if query in haystack:
            matches.append(event)
    emit(
        {
            "ok": True,
            "action": "search",
            "query": args.query,
            "calendar": args.calendar,
            "from": args.start,
            "to": args.end,
            "count": len(matches),
            "events": [event_summary(event) for event in matches],
        }
    )


def cmd_create(args):
    state = load_state(args.state)
    start = parse_dt(args.start)
    end = parse_dt(args.end)
    if comparable_dt(end) <= comparable_dt(start):
        raise SystemExit("--end must be after --start")

    ensure_calendar(state, args.calendar)
    timestamp = now_iso()
    event = {
        "id": next_event_id(state),
        "calendar_id": args.calendar,
        "title": args.title,
        "description": args.description,
        "start": args.start,
        "end": args.end,
        "timezone": args.timezone,
        "location": args.location,
        "attendees": normalize_attendees(args.attendee),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state["events"][event["id"]] = event
    save_state(args.state, state)
    if args.full:
        emit({"ok": True, "action": "create", "event": event})
    else:
        emit({"ok": True, "action": "create", "event": event_summary(event)})


def cmd_delete(args):
    state = load_state(args.state)
    event = require_event(state, args.id)
    del state["events"][args.id]
    save_state(args.state, state)
    emit({"ok": True, "action": "delete", "deleted_event": event_summary(event)})


def add_state_arg(parser):
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="Calendar state path")


def add_range_args(parser):
    parser.add_argument("--from", dest="start", help="Inclusive ISO date or datetime lower bound")
    parser.add_argument("--to", dest="end", help="Inclusive ISO date or datetime upper bound")


def build_parser():
    parser = argparse.ArgumentParser(description="Local virtual calendar")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reset")
    add_state_arg(p)
    p.add_argument("--calendar", action="append", default=[], help="Calendar id to include after reset")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("list")
    add_state_arg(p)
    p.add_argument("--calendar", help="Filter by calendar id")
    add_range_args(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("read")
    add_state_arg(p)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("search")
    add_state_arg(p)
    p.add_argument("--query", required=True)
    p.add_argument("--calendar", help="Filter by calendar id")
    add_range_args(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("create")
    add_state_arg(p)
    p.add_argument("--calendar", default="primary", help="Calendar id")
    p.add_argument("--title", required=True)
    p.add_argument("--start", required=True, help="ISO datetime")
    p.add_argument("--end", required=True, help="ISO datetime")
    p.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    p.add_argument("--location", default="")
    p.add_argument("--description", default="")
    p.add_argument("--attendee", action="append", default=[], help="Attendee email; may be repeated or comma-separated")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("delete")
    add_state_arg(p)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_delete)

    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
