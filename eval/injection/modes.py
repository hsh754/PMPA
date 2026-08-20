from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

try:
    from .metrics import build_metric_summary
    from .memory import (
        cleanup_project_memory,
        diff_memory,
        initialize_project_memory_from_dir,
        snapshot_memory,
    )
    from .models import EvaluationSample, RunConfig
    from .runner import prune_session_outputs, run_prompt_round
    from .samples import build_sample_prompt
    from .utils import (
        get_virtual_state_path,
        hide_deleted_paths,
        render_prompt,
        round_failed,
        status_text,
        write_json,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from metrics import build_metric_summary
    from memory import cleanup_project_memory, diff_memory, initialize_project_memory_from_dir, snapshot_memory
    from models import EvaluationSample, RunConfig
    from runner import prune_session_outputs, run_prompt_round
    from samples import build_sample_prompt
    from utils import (
        get_virtual_state_path,
        hide_deleted_paths,
        render_prompt,
        round_failed,
        status_text,
        write_json,
    )


SCENARIO_RESET_SCRIPTS = {
    "email": ("virtual-email", "mailbox.py"),
    "docs": ("virtual-docs", "docs.py"),
    "calendar": ("virtual-calendar", "calendar.py"),
    "form": ("virtual-form", "form.py"),
}

EVAL_DIR = Path(__file__).resolve().parents[1]
FORM_INITIAL_MEMORY_DIR = EVAL_DIR / "initial_memory_state" / "initial_memory"


def run_normal_session(
    sample: EvaluationSample,
    repeat_index: int,
    repeats: int,
    run_index: int,
    run_count: int,
    prompt_template: str,
    method: str,
    config: RunConfig,
) -> tuple[dict[str, object], dict[str, object]]:
    session_id = f"sample_{sample.index}_run_{repeat_index}"
    session_dir = config.run_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_sample_prompt(
        base_prompt_template=prompt_template,
        sample=sample,
        method=method,
        run_index=run_index,
        run_count=run_count,
    )

    round_1 = run_prompt_round(
        round_name="1",
        session_id=session_id,
        session_dir=session_dir,
        prompt=prompt,
        config=config,
    )
    cleanup = cleanup_project_memory(config.memory_dir)
    write_json(session_dir / "cleanup.json", cleanup)

    result = hide_deleted_paths(asdict(round_1))
    result.pop("round_name", None)
    result.update(sample_metadata(sample, repeat_index, repeats, run_index))
    prune_session_outputs(session_dir)
    return result, cleanup


def run_isr_session(
    sample: EvaluationSample,
    repeat_index: int,
    repeats: int,
    run_index: int,
    run_count: int,
    benign_task_prompt_template: str,
    trigger_prompt_template: str,
    snapshot_delay_seconds: float,
    method: str,
    config: RunConfig,
    scenario: str | None = None,
) -> dict[str, object]:
    session_id = f"sample_{sample.index}_run_{repeat_index}"
    session_dir = config.run_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_sample_prompt(
        base_prompt_template=benign_task_prompt_template,
        sample=sample,
        method=method,
        run_index=run_index,
        run_count=run_count,
    )

    cleanup_before = initialize_session_memory(config, scenario)

    before_snapshot, before_errors = snapshot_memory(config.memory_dir)

    round_1 = run_prompt_round(
        round_name="1",
        session_id=session_id,
        session_dir=session_dir,
        prompt=prompt,
        config=config,
    )

    if snapshot_delay_seconds > 0:
        time.sleep(snapshot_delay_seconds)

    after_snapshot, after_errors = snapshot_memory(config.memory_dir)

    memory_diff = diff_memory(
        config.memory_dir,
        before_snapshot,
        after_snapshot,
        before_errors + after_errors,
    )

    isr_info = {
        "inserted": bool(memory_diff["created_files"] or memory_diff["modified_files"]),
        "errors": memory_diff["errors"],
    }

    round_2 = None
    scenario_reset_after_round_2 = None
    email_environment_before_round_2 = None
    email_environment_after_round_2 = None
    calendar_environment_before_round_2 = None
    calendar_environment_after_round_2 = None
    docs_shared_content_before_round_2 = None
    docs_shared_content_after_round_2 = None
    form_latest_submission_after_round_2 = None
    if isr_info["inserted"]:
        if scenario == "email":
            email_environment_before_round_2 = snapshot_virtual_email_environment(
                config,
                snapshot_name="before_round_2",
            )
        if scenario == "docs":
            docs_shared_content_before_round_2 = snapshot_virtual_docs_shared_content(config)
        if scenario == "calendar":
            calendar_environment_before_round_2 = snapshot_virtual_calendar_environment(
                config,
                snapshot_name="before_round_2",
            )
        round_2 = run_prompt_round(
            round_name="2",
            session_id=session_id,
            session_dir=session_dir,
            prompt=render_prompt(trigger_prompt_template, run_index, run_count),
            config=config,
        )
        if scenario == "docs":
            docs_shared_content_after_round_2 = snapshot_virtual_docs_shared_content(config)
        if scenario == "email":
            email_environment_after_round_2 = snapshot_virtual_email_environment(config)
        if scenario == "calendar":
            calendar_environment_after_round_2 = snapshot_virtual_calendar_environment(
                config,
                existing_event_ids=event_ids_from_calendar_snapshot(calendar_environment_before_round_2),
            )
        if scenario == "form":
            form_latest_submission_after_round_2 = snapshot_virtual_form_latest_submission(config)
        scenario_reset_after_round_2 = reset_virtual_scenario_environment(config, scenario)
        if scenario_reset_after_round_2 is not None and scenario in SCENARIO_RESET_SCRIPTS:
            if scenario == "email":
                write_json(session_dir / "email_environment_after_round_2.json", email_environment_after_round_2)
            elif scenario == "calendar":
                write_json(session_dir / "calendar_environment_after_round_2.json", calendar_environment_after_round_2)
            elif scenario == "docs":
                write_json(session_dir / "docs_environment_after_round_2.json", docs_shared_content_after_round_2)
            elif scenario == "form":
                write_json(session_dir / "form_environment_after_round_2.json", form_latest_submission_after_round_2)
            else:
                write_json(session_dir / f"{scenario}_environment_after_round_2.json", scenario_reset_after_round_2)

    cleanup_final = cleanup_project_memory(config.memory_dir)

    compact_reset = compact_reset_result(scenario_reset_after_round_2)
    result = {
        "session_id": session_id,
        "index": run_index,
        "sample_index": sample.index,
        "sample_id": sample.sample_id,
        "round_1": compact_round_result(round_1),
        "round_2": compact_round_result(round_2) if round_2 else None,
        "isr": isr_info,
        "scenario": scenario,
        "scenario_reset_after_round_2": compact_reset,
        "scenario_reset_after_round_2_name": scenario if compact_reset is not None else None,
        "email_reset_after_round_2": compact_reset if scenario == "email" else None,
        "email_environment_before_round_2": email_environment_before_round_2,
        "email_environment_after_round_2": email_environment_after_round_2,
        "calendar_environment_before_round_2": calendar_environment_before_round_2,
        "calendar_environment_after_round_2": calendar_environment_after_round_2,
        "docs_shared_content_before_round_2": docs_shared_content_before_round_2,
        "docs_shared_content_after_round_2": docs_shared_content_after_round_2,
        "form_latest_submission_after_round_2": form_latest_submission_after_round_2,
        "docs_reset_after_round_2": compact_reset if scenario == "docs" else None,
        "calendar_reset_after_round_2": compact_reset if scenario == "calendar" else None,
        "form_reset_after_round_2": compact_reset if scenario == "form" else None,
        "cleanup_error_count": len(cleanup_before["errors"]) + len(cleanup_final["errors"]),
    }
    prune_session_outputs(session_dir)
    return result


def initialize_session_memory(config: RunConfig, scenario: str | None) -> dict[str, object]:
    if scenario == "form":
        return initialize_project_memory_from_dir(
            config.memory_dir,
            FORM_INITIAL_MEMORY_DIR,
        )
    return cleanup_project_memory(config.memory_dir)


def compact_round_result(round_result: object) -> dict[str, object] | None:
    if round_result is None:
        return None
    return {
        "returncode": round_result.returncode,
        "timed_out": round_result.timed_out,
        "stdout_path": round_result.stdout_path,
        "claudecode_session_copy_path": round_result.claudecode_session_copy_path,
    }


def compact_reset_result(result: dict[str, object] | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "returncode": result.get("returncode"),
        "error": result.get("error"),
    }


def reset_virtual_scenario_environment(config: RunConfig, scenario: str | None) -> dict[str, object] | None:
    if scenario is None:
        return None
    reset_script = SCENARIO_RESET_SCRIPTS.get(scenario)
    if reset_script is None:
        return None
    skill_name, script_name = reset_script
    script_path = config.skills_dir / skill_name / "scripts" / script_name
    command = [sys.executable, str(script_path), "reset"]
    result: dict[str, object] = {
        "attempted": True,
        "scenario": scenario,
        "command": command,
        "cwd": str(config.cwd),
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "error": None,
    }

    if not script_path.is_file():
        result["error"] = f"virtual {scenario} reset script not found: {script_path}"
        return result

    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(config.cwd),
            env={**os.environ, **config.extra_env},
            timeout=30,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - keep evaluation moving while recording cleanup failures.
        result["error"] = repr(exc)
        return result

    result.update(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    return result


def snapshot_virtual_email_environment(
    config: RunConfig,
    snapshot_name: str = "after_round_2_before_reset",
) -> dict[str, object]:
    state_path = get_virtual_state_path(config.extra_env, "email")
    result: dict[str, object] = {
        "ok": False,
        "snapshot": snapshot_name,
        "state": str(state_path),
        "owner": None,
        "current_user": None,
        "account_count": 0,
        "message_count": 0,
        "accounts": {},
        "messages": {},
        "next_message": None,
        "next_thread": None,
        "error": None,
    }

    if not state_path.is_file():
        result["error"] = f"virtual email state not found: {state_path}"
        return result

    try:
        payload = json_loads_path(state_path)
    except Exception as exc:  # noqa: BLE001 - evidence should record malformed state.
        result["error"] = repr(exc)
        return result

    if not isinstance(payload, dict):
        result["error"] = "virtual email state root is not an object"
        return result

    accounts = payload.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
    messages = payload.get("messages")
    if not isinstance(messages, dict):
        messages = {}

    result.update(
        {
            "ok": True,
            "owner": payload.get("owner") if isinstance(payload.get("owner"), str) else None,
            "current_user": (
                payload.get("current_user") if isinstance(payload.get("current_user"), str) else None
            ),
            "account_count": len(accounts),
            "message_count": len(messages),
            "accounts": accounts,
            "messages": messages,
            "next_message": payload.get("next_message") if isinstance(payload.get("next_message"), int) else None,
            "next_thread": payload.get("next_thread") if isinstance(payload.get("next_thread"), int) else None,
        }
    )
    return result


def snapshot_virtual_calendar_environment(
    config: RunConfig,
    existing_event_ids: set[str] | None = None,
    snapshot_name: str = "after_round_2_before_reset",
) -> dict[str, object]:
    state_path = get_virtual_state_path(config.extra_env, "calendar")
    result: dict[str, object] = {
        "ok": False,
        "snapshot": snapshot_name,
        "state": str(state_path),
        "owner": None,
        "current_user": None,
        "calendar_count": 0,
        "event_count": 0,
        "new_event_count": 0,
        "calendars": {},
        "event_ids": [],
        "new_events": {},
        "next_event": None,
        "error": None,
    }

    if not state_path.is_file():
        result["error"] = f"virtual calendar state not found: {state_path}"
        return result

    try:
        payload = json_loads_path(state_path)
    except Exception as exc:  # noqa: BLE001 - evidence should record malformed state.
        result["error"] = repr(exc)
        return result

    if not isinstance(payload, dict):
        result["error"] = "virtual calendar state root is not an object"
        return result

    calendars = payload.get("calendars")
    if not isinstance(calendars, dict):
        calendars = {}
    events = payload.get("events")
    if not isinstance(events, dict):
        events = {}

    existing_event_ids = existing_event_ids or set()
    new_events = {
        event_id: event
        for event_id, event in events.items()
        if isinstance(event_id, str) and event_id not in existing_event_ids
    }

    result.update(
        {
            "ok": True,
            "owner": payload.get("owner") if isinstance(payload.get("owner"), str) else None,
            "current_user": (
                payload.get("current_user") if isinstance(payload.get("current_user"), str) else None
            ),
            "calendar_count": len(calendars),
            "event_count": len(events),
            "new_event_count": len(new_events),
            "calendars": calendars,
            "event_ids": sorted(event_id for event_id in events if isinstance(event_id, str)),
            "new_events": new_events,
            "next_event": payload.get("next_event") if isinstance(payload.get("next_event"), int) else None,
        }
    )
    return result


def event_ids_from_calendar_snapshot(snapshot: object) -> set[str]:
    if not isinstance(snapshot, dict):
        return set()
    event_ids = snapshot.get("event_ids")
    if isinstance(event_ids, list):
        return {event_id for event_id in event_ids if isinstance(event_id, str)}
    events = snapshot.get("events")
    if not isinstance(events, dict):
        return set()
    return {event_id for event_id in events if isinstance(event_id, str)}


def snapshot_virtual_docs_shared_content(config: RunConfig) -> dict[str, object]:
    state_path = get_virtual_state_path(config.extra_env, "docs")
    result: dict[str, object] = {
        "ok": False,
        "state": str(state_path),
        "documents": {},
        "error": None,
    }

    if not state_path.is_file():
        result["error"] = f"virtual docs state not found: {state_path}"
        return result

    try:
        payload = json_loads_path(state_path)
    except Exception as exc:  # noqa: BLE001 - metric evidence should record malformed state.
        result["error"] = repr(exc)
        return result

    if not isinstance(payload, dict):
        result["error"] = "virtual docs state root is not an object"
        return result
    documents = payload.get("documents")
    if not isinstance(documents, dict):
        result["error"] = "virtual docs state documents field is not an object"
        return result

    shared_docs: dict[str, dict[str, object]] = {}
    for doc_id, doc in documents.items():
        if not isinstance(doc_id, str) or not isinstance(doc, dict):
            continue
        if doc.get("kind") != "shared":
            continue
        content = doc.get("content")
        if not isinstance(content, str):
            content = ""
        shared_docs[doc_id] = {
            "id": doc_id,
            "title": doc.get("title") if isinstance(doc.get("title"), str) else None,
            "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
            "content_length": len(content),
            "updated_at": doc.get("updated_at") if isinstance(doc.get("updated_at"), str) else None,
            "updated_by": doc.get("updated_by") if isinstance(doc.get("updated_by"), str) else None,
        }

    result.update({"ok": True, "documents": shared_docs})
    return result


def snapshot_virtual_form_latest_submission(config: RunConfig) -> dict[str, object]:
    state_path = get_virtual_state_path(config.extra_env, "form")
    result: dict[str, object] = {
        "ok": False,
        "state": str(state_path),
        "submission_count": 0,
        "latest_submission": None,
        "error": None,
    }

    if not state_path.is_file():
        result["error"] = f"virtual form state not found: {state_path}"
        return result

    try:
        payload = json_loads_path(state_path)
    except Exception as exc:  # noqa: BLE001 - metric evidence should record malformed state.
        result["error"] = repr(exc)
        return result

    if not isinstance(payload, dict):
        result["error"] = "virtual form state root is not an object"
        return result
    submissions = payload.get("submissions")
    if not isinstance(submissions, dict):
        result["error"] = "virtual form state submissions field is not an object"
        return result

    latest_id = latest_form_submission_id(submissions)
    result["submission_count"] = len(submissions)
    if latest_id is None:
        result.update({"ok": True, "latest_submission": None})
        return result

    latest_submission = submissions.get(latest_id)
    if not isinstance(latest_submission, dict):
        result["error"] = f"latest virtual form submission is not an object: {latest_id}"
        return result

    answers = latest_submission.get("answers")
    if not isinstance(answers, dict):
        answers = {}
    addition_info_value = answers.get("addition_info", answers.get("suggestions"))
    addition_info_text = addition_info_value if isinstance(addition_info_value, str) else ""
    addition_info_stripped = addition_info_text.strip()

    result.update(
        {
            "ok": True,
            "latest_submission": {
                "id": latest_submission.get("id") if isinstance(latest_submission.get("id"), str) else latest_id,
                "form_id": latest_submission.get("form_id") if isinstance(latest_submission.get("form_id"), str) else None,
                "submitted_by": (
                    latest_submission.get("submitted_by")
                    if isinstance(latest_submission.get("submitted_by"), str)
                    else None
                ),
                "submitted_at": (
                    latest_submission.get("submitted_at")
                    if isinstance(latest_submission.get("submitted_at"), str)
                    else None
                ),
                "addition_info_present": bool(addition_info_stripped),
                "addition_info_length": len(addition_info_text),
                "addition_info_sha256": sha256(addition_info_text.encode("utf-8")).hexdigest(),
            },
        }
    )
    return result


def latest_form_submission_id(submissions: dict[str, object]) -> str | None:
    latest_id = None
    latest_key: tuple[int, str, str] | None = None
    for submission_id, submission in submissions.items():
        if not isinstance(submission_id, str) or not isinstance(submission, dict):
            continue
        submitted_at = submission.get("submitted_at")
        if not isinstance(submitted_at, str):
            submitted_at = ""
        key = (submission_id_sequence(submission_id), submitted_at, submission_id)
        if latest_key is None or key > latest_key:
            latest_key = key
            latest_id = submission_id
    return latest_id


def submission_id_sequence(submission_id: str) -> int:
    try:
        return int(submission_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return -1


def json_loads_path(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sample_metadata(
    sample: EvaluationSample,
    repeat_index: int,
    repeats: int,
    run_index: int,
) -> dict[str, object]:
    return {
        "index": run_index,
        "sample_index": sample.index,
        "sample_id": sample.sample_id,
        "sample_path": str(sample.path),
        "repeat_index": repeat_index,
        "repeat_count": repeats,
    }


def run_normal_mode(
    sessions: int,
    prompt_template: str,
    samples: list[EvaluationSample],
    method: str,
    config: RunConfig,
    write_summary: bool,
) -> int:
    results: list[dict[str, object]] = []
    cleanups: dict[str, dict[str, object]] = {}
    run_count = len(samples) * sessions

    for sample in samples:
        for repeat_index in range(1, sessions + 1):
            run_index = (sample.index - 1) * sessions + repeat_index
            result, cleanup = run_normal_session(
                sample,
                repeat_index,
                sessions,
                run_index,
                run_count,
                prompt_template,
                method,
                config,
            )
            results.append(result)
            cleanups[str(result["session_id"])] = cleanup
            deleted_count = len(cleanup["deleted_memory_files"]) + len(cleanup["deleted_jsonl_files"])
            print(
                f"{result['session_id']}: sample={result['sample_id']}, "
                f"exit={status_text(result)}, {result['duration_seconds']}s, "
                f"cleanup_deleted={deleted_count}, cleanup_errors={len(cleanup['errors'])}"
            )

    results.sort(key=lambda item: int(item["index"]))
    if write_summary:
        write_json(
            config.run_dir / "summary.json",
            [{**item, "cleanup": cleanups[str(item["session_id"])]} for item in results],
        )

    failed = [item for item in results if round_failed(item)]
    cleanup_errors = sum(len(cleanups[str(item["session_id"])]["errors"]) for item in results)
    print(f"done: {len(results) - len(failed)} ok, {len(failed)} failed, cleanup_errors={cleanup_errors}")
    return 1 if failed or cleanup_errors else 0


def run_isr_mode(
    sessions: int,
    benign_task_prompt_template: str,
    trigger_prompt_template: str,
    snapshot_delay_seconds: float,
    samples: list[EvaluationSample],
    method: str,
    config: RunConfig,
    write_summary: bool,
    scenario: str | None = None,
    utility_clean_run_dir: Path | None = None,
) -> int:
    results: list[dict[str, object]] = []
    run_count = len(samples) * sessions

    for sample in samples:
        for repeat_index in range(1, sessions + 1):
            run_index = (sample.index - 1) * sessions + repeat_index
            result = run_isr_session(
                sample,
                repeat_index,
                sessions,
                run_index,
                run_count,
                benign_task_prompt_template,
                trigger_prompt_template,
                snapshot_delay_seconds,
                method,
                config,
                scenario,
            )
            results.append(result)
            round_2 = result["round_2"]
            round_2_status = "skipped" if round_2 is None else status_text(round_2)
            scenario_reset = result.get("scenario_reset_after_round_2")
            scenario_reset_name = result.get("scenario_reset_after_round_2_name") or "none"
            scenario_reset_status = (
                "skipped"
                if scenario_reset is None
                else str(scenario_reset.get("returncode")) if isinstance(scenario_reset, dict) else "unknown"
            )
            print(
                f"{result['session_id']}: sample={result['sample_id']}, "
                f"round1={status_text(result['round_1'])}, "
                f"isr_inserted={result['isr']['inserted']}, round2={round_2_status}, "
                f"scenario_reset={scenario_reset_name}:{scenario_reset_status}"
            )

    results.sort(key=lambda item: int(item["index"]))
    if write_summary:
        write_json(config.run_dir / "summary.json", [compact_isr_summary_item(item) for item in results])
    metric_summary = build_metric_summary(
        scenario=scenario,
        results=results,
        utility_clean_run_dir=utility_clean_run_dir,
    )
    if metric_summary is not None:
        write_json(config.run_dir / "metric_summary.json", metric_summary)

    failed = [item for item in results if round_failed(item["round_1"]) or round_failed(item["round_2"])]
    cleanup_errors = sum(int(item.get("cleanup_error_count", 0)) for item in results)
    scenario_reset_errors = sum(1 for item in results if reset_failed(item.get("scenario_reset_after_round_2")))
    print(
        f"done: {len(results) - len(failed)} ok, {len(failed)} failed, "
        f"cleanup_errors={cleanup_errors}, scenario_reset_errors={scenario_reset_errors}"
    )
    return 1 if failed or cleanup_errors or scenario_reset_errors else 0


def reset_failed(result: object) -> bool:
    if result is None:
        return False
    if not isinstance(result, dict):
        return True
    return bool(result.get("error") or result.get("returncode") not in (0,))


def compact_isr_summary_item(result: dict[str, object]) -> dict[str, object]:
    round_1 = result.get("round_1") if isinstance(result.get("round_1"), dict) else {}
    round_2 = result.get("round_2") if isinstance(result.get("round_2"), dict) else None
    isr = result.get("isr") if isinstance(result.get("isr"), dict) else {}
    return {
        "session_id": result.get("session_id"),
        "sample_index": result.get("sample_index"),
        "sample_id": result.get("sample_id"),
        "round1_returncode": round_1.get("returncode"),
        "round2_entered": round_2 is not None,
        "round2_returncode": round_2.get("returncode") if round_2 else None,
        "isr_inserted": bool(isr.get("inserted")),
        "stdout_1_path": round_1.get("stdout_path"),
        "stdout_2_path": round_2.get("stdout_path") if round_2 else None,
        "claudecode_session_1_path": round_1.get("claudecode_session_copy_path"),
        "claudecode_session_2_path": round_2.get("claudecode_session_copy_path") if round_2 else None,
        "scenario_reset_after_round_2_name": result.get("scenario_reset_after_round_2_name"),
        "scenario_reset_after_round_2_returncode": (
            result["scenario_reset_after_round_2"].get("returncode")
            if isinstance(result.get("scenario_reset_after_round_2"), dict)
            else None
        ),
        "docs_reset_after_round_2_returncode": (
            result["docs_reset_after_round_2"].get("returncode")
            if isinstance(result.get("docs_reset_after_round_2"), dict)
            else None
        ),
    }
