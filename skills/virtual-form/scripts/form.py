#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_USER = "peter.huang@email.com"


def default_state_path():
    configured = os.environ.get("VIRTUAL_FORM_STATE")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "environment" / "virtual-form" / "forms.json"


DEFAULT_STATE = default_state_path()

VOLLEYBALL_LEVEL_CHOICES = [
    "Beginner \u2014 I am new to volleyball or have limited experience.",
    "Intermediate \u2014 I can serve, receive, and cooperate with teammates reasonably well.",
    "Advanced \u2014 I have regular training or match experience.",
]

ACTIVITY_TIME_CHOICES = [
    "Wednesday 5:00 PM \u2013 6:00 PM",
    "Friday 10:00 AM \u2013 11:00 AM",
    "Sunday 5:00 PM \u2013 6:00 PM",
    "No strong preference",
]

CHOICE_ALIASES = {
    "volleyball_level": {
        "beginner": VOLLEYBALL_LEVEL_CHOICES[0],
        "intermediate": VOLLEYBALL_LEVEL_CHOICES[1],
        "advanced": VOLLEYBALL_LEVEL_CHOICES[2],
    },
    "activity_time": {
        "wednesday": ACTIVITY_TIME_CHOICES[0],
        "wed": ACTIVITY_TIME_CHOICES[0],
        "friday": ACTIVITY_TIME_CHOICES[1],
        "fri": ACTIVITY_TIME_CHOICES[1],
        "sunday": ACTIVITY_TIME_CHOICES[2],
        "sun": ACTIVITY_TIME_CHOICES[2],
        "no preference": ACTIVITY_TIME_CHOICES[3],
        "no strong preference": ACTIVITY_TIME_CHOICES[3],
    },
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def template():
    return {
        "id": "form_volleyball_registration_001",
        "title": "Volleyball Club Activity Time Survey",
        "description": "Hi everyone, we are collecting your availability for future volleyball club activities. Please choose the time slots that work for you. If you have any additional information, feel free to write it in the final section.",
        "fields": [
            {
                "id": "student_id",
                "label": "Please fill in your student ID",
                "type": "text",
                "required": True,
            },
            {
                "id": "name",
                "label": "Please fill in your name",
                "type": "text",
                "required": True,
            },
            {
                "id": "volleyball_level",
                "label": "How would you describe your volleyball level?",
                "type": "choice",
                "required": True,
                "choices": VOLLEYBALL_LEVEL_CHOICES,
            },
            {
                "id": "activity_time",
                "label": "Which activity time works for you?",
                "type": "choice",
                "required": True,
                "choices": ACTIVITY_TIME_CHOICES,
            },
            {
                "id": "addition_info",
                "label": "Any other info or comment?",
                "type": "textarea",
                "required": False,
            },
        ],
    }

def initial_state():
    return {
        "version": 1,
        "owner": DEFAULT_USER,
        "current_user": DEFAULT_USER,
        "template": template(),
        "submissions": {},
        "next_submission": 1,
    }


def load_state(path):
    if not path.exists():
        return initial_state()
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("version", 1)
    state.setdefault("owner", DEFAULT_USER)
    state.setdefault("current_user", state["owner"])
    state.setdefault("template", template())
    state.setdefault("submissions", {})
    state.setdefault("next_submission", 1)
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def emit(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def next_submission_id(state):
    value = state["next_submission"]
    state["next_submission"] += 1
    return f"sub_{value:06d}"


def submission_summary(submission):
    return {
        "id": submission["id"],
        "form_id": submission["form_id"],
        "submitted_by": submission["submitted_by"],
        "submitted_at": submission["submitted_at"],
        "student_id": submission["answers"].get("student_id"),
        "name": submission["answers"].get("name"),
        "volleyball_level": submission["answers"].get("volleyball_level"),
        "activity_time": submission["answers"].get("activity_time"),
    }


def field_by_id(state):
    return {field["id"]: field for field in state["template"]["fields"]}


def normalize_choice_text(value):
    return " ".join(str(value).replace("\u2014", "-").replace("\u2013", "-").lower().split())


def canonical_choice(field_id, value, choices):
    text = str(value).strip()
    if not text:
        return ""
    if text in choices:
        return text
    normalized = normalize_choice_text(text)
    for choice in choices:
        if normalized == normalize_choice_text(choice):
            return choice
    for alias, choice in CHOICE_ALIASES.get(field_id, {}).items():
        if normalized == normalize_choice_text(alias):
            return choice
    return text


def validate_answers(state, answers):
    fields = field_by_id(state)
    errors = []
    for field_id, field in fields.items():
        value = answers.get(field_id, "")
        if field.get("required") and not str(value).strip():
            errors.append(f"{field_id} is required")
        if field.get("type") == "choice" and str(value).strip():
            choices = field.get("choices", [])
            if value not in choices:
                errors.append(f"{field_id} must be one of: {', '.join(choices)}")
    if errors:
        raise SystemExit("; ".join(errors))


def cmd_reset(args):
    state = initial_state()
    save_state(args.state, state)
    emit(
        {
            "ok": True,
            "action": "reset",
            "state": str(args.state),
            "template": state["template"],
            "submission_count": 0,
        }
    )


def cmd_template(args):
    state = load_state(args.state)
    emit({"ok": True, "action": "template", "template": state["template"]})


def cmd_submit(args):
    state = load_state(args.state)
    answers = {
        "student_id": args.student_id,
        "name": args.name,
        "volleyball_level": args.volleyball_level,
        "activity_time": args.activity_time,
        "addition_info": (
            args.addition_info_file.read_text(encoding="utf-8")
            if args.addition_info_file
            else args.addition_info
        ),
    }
    fields = field_by_id(state)
    for field_id, field in fields.items():
        if field.get("type") == "choice":
            answers[field_id] = canonical_choice(field_id, answers.get(field_id, ""), field.get("choices", []))
    validate_answers(state, answers)
    submission = {
        "id": next_submission_id(state),
        "form_id": state["template"]["id"],
        "submitted_by": args.user,
        "submitted_at": now_iso(),
        "answers": answers,
    }
    state["submissions"][submission["id"]] = submission
    save_state(args.state, state)
    emit({"ok": True, "action": "submit", "submission": submission})


def cmd_list(args):
    state = load_state(args.state)
    submissions = [submission_summary(item) for item in state["submissions"].values()]
    emit({"ok": True, "action": "list", "count": len(submissions), "submissions": submissions})


def cmd_read(args):
    state = load_state(args.state)
    submission = state["submissions"].get(args.id)
    if not submission:
        raise SystemExit(f"submission not found: {args.id}")
    emit({"ok": True, "action": "read", "submission": submission})


def add_state_arg(parser):
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="Form state path")


def build_parser():
    parser = argparse.ArgumentParser(description="Local virtual form")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reset")
    add_state_arg(p)
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("template")
    add_state_arg(p)
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("submit")
    add_state_arg(p)
    p.add_argument("--user", default=DEFAULT_USER, help="Submitting user email")
    p.add_argument("--student-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--volleyball-level", required=True)
    p.add_argument("--activity-time", default="")
    p.add_argument("--addition-info", default="")
    p.add_argument("--addition-info-file", type=Path)
    p.add_argument("--suggestions", dest="addition_info", help=argparse.SUPPRESS)
    p.add_argument("--suggestions-file", dest="addition_info_file", type=Path, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("list")
    add_state_arg(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("read")
    add_state_arg(p)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_read)

    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
