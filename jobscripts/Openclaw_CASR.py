#!/usr/bin/env python3
"""
Run OpenClaw C-ASR evaluations with pre-poisoned memory.

The script injects one scenario-specific poisoned memory state into the
OpenClaw workspace, runs the trigger prompt for N sequential sessions, judges
scenario success with the OpenClaw ISR metric logic, and clears memory once at
the end of the batch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.openclaw_injection.constants import VALID_METHODS  # noqa: E402
from eval.openclaw_injection.memory import (  # noqa: E402
    cleanup_project_memory,
    initialize_project_memory_from_dir,
)
from eval.openclaw_injection.metrics import (  # noqa: E402
    evaluate_calendar_round2_attendee_success,
    evaluate_docs_round2_shared_content_change,
    evaluate_email_round2_cc_success,
    evaluate_form_round2_addition_info_success,
)
from eval.openclaw_injection.models import PromptRunResult, RunConfig  # noqa: E402
from eval.openclaw_injection.modes import (  # noqa: E402
    event_ids_from_calendar_snapshot,
    reset_virtual_scenario_environment,
    snapshot_virtual_calendar_events,
    snapshot_virtual_calendar_environment,
    snapshot_virtual_docs_shared_content,
    snapshot_virtual_email_environment,
    snapshot_virtual_email_sent_messages,
    snapshot_virtual_form_latest_submission,
)
from eval.openclaw_injection.models import EvaluationSample  # noqa: E402
from eval.openclaw_injection.runner import run_prompt_round  # noqa: E402
from eval.openclaw_injection.samples import build_sample_prompt, load_samples  # noqa: E402
from eval.openclaw_injection.utils import (  # noqa: E402
    configure_virtual_state_env,
    get_default_openclaw_environment_dir,
    get_default_openclaw_sessions_dir,
    get_default_openclaw_skills_dir,
    get_default_openclaw_state_dir,
    get_default_openclaw_workspace_dir,
    get_virtual_state_path,
    parse_env,
    render_prompt,
    safe_path_component,
    split_command,
)


VALID_SCENARIOS = ("email", "calendar", "docs", "form")
POISONED_MEMORY_ROOT = Path("eval") / "poisoned_memory_state" / "openclaw"
MAX_BENIGN_COUNT = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run OpenClaw C-ASR evaluations with scenario poisoned memory."
    )
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt-file", type=Path, help="Text file to send.")
    prompt_group.add_argument("--prompt", help="Prompt text to send.")

    parser.add_argument("-n", "--sessions", type=int, default=1)
    parser.add_argument("--cmd", default="openclaw")
    parser.add_argument("--cmd-args", default="agent --local --json")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model-name", default="unknown_model")
    parser.add_argument(
        "--scenario",
        "--scenairo",
        choices=VALID_SCENARIOS,
        required=True,
        help="Scenario-specific C-ASR evaluator.",
    )
    parser.add_argument(
        "--benign-count",
        type=int,
        default=0,
        help=(
            "Number of benign-task turns to run in the same OpenClaw session "
            "before the scenario prompt. Must be between 0 and 5."
        ),
    )
    benign_prompt_group = parser.add_mutually_exclusive_group()
    benign_prompt_group.add_argument(
        "--benign-task-prompt-file",
        type=Path,
        help=(
            "Prompt file used for benign QA turns. Defaults to "
            "prompts/benign_task_prompt.txt when --benign-count is greater than 0."
        ),
    )
    benign_prompt_group.add_argument(
        "--benign-task-prompt",
        help="Prompt text used for benign QA turns.",
    )
    parser.add_argument(
        "--cleansample-dir",
        "--clean-sample-dir",
        dest="cleansample_dir",
        type=Path,
        default=None,
        help="Directory or file containing benign clean samples for pre-trigger QA turns.",
    )
    parser.add_argument(
        "--method",
        choices=sorted(VALID_METHODS),
        default="txt",
        help="Clean sample modality used by --cleansample-dir.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("runs") / "OpenclawCasr")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Backward-compatible alias for --openclaw-workspace-dir.",
    )
    parser.add_argument(
        "--openclaw-state-dir",
        type=Path,
        default=None,
        help="OpenClaw state directory. Defaults to OPENCLAW_STATE_DIR or D:/openclaw on Windows.",
    )
    parser.add_argument(
        "--openclaw-workspace-dir",
        type=Path,
        default=None,
        help="OpenClaw workspace directory. Defaults to <state-dir>/workspace.",
    )
    parser.add_argument(
        "--openclaw-sessions-dir",
        type=Path,
        default=None,
        help="OpenClaw session log directory. Defaults to <state-dir>/agents/<agent-id>/sessions.",
    )
    parser.add_argument(
        "--openclaw-skills-dir",
        type=Path,
        default=None,
        help="OpenClaw skills directory. Defaults to <state-dir>/workspace/skills.",
    )
    parser.add_argument(
        "--openclaw-environment-dir",
        type=Path,
        default=None,
        help="OpenClaw virtual environment state directory. Defaults to <state-dir>/environment.",
    )
    parser.add_argument("--openclaw-agent-id", default="main")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-summary-json", action="store_true")
    return parser.parse_args()


def load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return args.prompt_file.read_text(encoding="utf-8")
    return args.prompt


def load_benign_task_prompt(args: argparse.Namespace) -> str | None:
    if args.benign_count == 0:
        return None
    if args.benign_task_prompt_file:
        return args.benign_task_prompt_file.read_text(encoding="utf-8")
    if args.benign_task_prompt:
        return args.benign_task_prompt

    default_path = ROOT / "prompts" / "benign_task_prompt.txt"
    if default_path.is_file():
        return default_path.read_text(encoding="utf-8")
    raise ValueError(
        "--benign-task-prompt-file or --benign-task-prompt is required when "
        "--benign-count is greater than 0"
    )


def select_benign_samples(args: argparse.Namespace) -> list[EvaluationSample]:
    if args.benign_count == 0:
        return []
    if args.benign_count < 0 or args.benign_count > MAX_BENIGN_COUNT:
        raise ValueError(f"--benign-count must be between 0 and {MAX_BENIGN_COUNT}")
    if args.cleansample_dir is None:
        raise ValueError("--cleansample-dir is required when --benign-count is greater than 0")

    samples = load_samples(args.cleansample_dir, args.method)
    if len(samples) < args.benign_count:
        raise ValueError(
            f"--benign-count is {args.benign_count}, but only {len(samples)} "
            f"clean samples were found under {args.cleansample_dir}"
        )
    return samples[: args.benign_count]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_config(
    *,
    args: argparse.Namespace,
    command: list[str],
    extra_env: dict[str, str],
    run_dir: Path,
) -> RunConfig:
    cwd = args.cwd.resolve()
    openclaw_state_dir = (
        args.openclaw_state_dir.resolve()
        if args.openclaw_state_dir is not None
        else get_default_openclaw_state_dir().resolve()
    )
    openclaw_workspace_dir = (
        args.memory_dir.resolve()
        if args.memory_dir is not None
        else args.openclaw_workspace_dir.resolve()
        if args.openclaw_workspace_dir is not None
        else get_default_openclaw_workspace_dir(openclaw_state_dir).resolve()
    )
    openclaw_sessions_dir = (
        args.openclaw_sessions_dir.resolve()
        if args.openclaw_sessions_dir is not None
        else get_default_openclaw_sessions_dir(openclaw_state_dir, args.openclaw_agent_id).resolve()
    )
    openclaw_skills_dir = (
        args.openclaw_skills_dir.resolve()
        if args.openclaw_skills_dir is not None
        else get_default_openclaw_skills_dir(openclaw_state_dir).resolve()
    )
    openclaw_environment_dir = (
        args.openclaw_environment_dir.resolve()
        if args.openclaw_environment_dir is not None
        else get_default_openclaw_environment_dir(openclaw_state_dir).resolve()
    )
    extra_env.setdefault("OPENCLAW_STATE_DIR", str(openclaw_state_dir))
    extra_env.setdefault("OPENCLAW_CONFIG_PATH", str(openclaw_state_dir / "openclaw.json"))
    configure_virtual_state_env(extra_env, openclaw_environment_dir, cwd)
    return RunConfig(
        command=command,
        run_dir=run_dir,
        cwd=cwd,
        timeout_seconds=args.timeout_seconds,
        extra_env=extra_env,
        openclaw_state_dir=openclaw_state_dir,
        openclaw_workspace_dir=openclaw_workspace_dir,
        openclaw_sessions_dir=openclaw_sessions_dir,
        openclaw_skills_dir=openclaw_skills_dir,
        openclaw_environment_dir=openclaw_environment_dir,
        openclaw_agent_id=args.openclaw_agent_id,
    )


def cleanup_openclaw_session_logs(sessions_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "openclaw_sessions_dir": str(sessions_dir),
        "deleted_session_logs": [],
        "errors": [],
    }
    if not sessions_dir.exists():
        return result
    if not sessions_dir.is_dir():
        result["errors"].append(f"OpenClaw sessions path is not a directory: {sessions_dir}")
        return result

    patterns = ("*.jsonl", "*.trajectory-path.json")
    for pattern in patterns:
        for path in sorted(sessions_dir.glob(pattern)):
            if not path.is_file():
                continue
            try:
                path.unlink()
                result["deleted_session_logs"].append(str(path))
            except FileNotFoundError:
                continue
            except Exception as exc:  # noqa: BLE001 - record cleanup failure and continue.
                result["errors"].append(f"{path}: {exc}")
    return result


def compact_round_result(round_result: PromptRunResult) -> dict[str, object]:
    return {
        "returncode": round_result.returncode,
        "timed_out": round_result.timed_out,
        "stdout_path": round_result.stdout_path,
        "stderr_path": round_result.stderr_path,
        "prompt_path": round_result.prompt_path,
        "openclaw_session_id": round_result.openclaw_session_id,
        "openclaw_session_copy_path": round_result.openclaw_session_copy_path,
        "openclaw_session_match": round_result.openclaw_session_match,
        "error": round_result.error,
    }


def compact_reset_result(result: dict[str, object] | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "returncode": result.get("returncode"),
        "error": result.get("error"),
    }


def reset_failed(result: object) -> bool:
    if result is None:
        return False
    if not isinstance(result, dict):
        return True
    return bool(result.get("error") or result.get("returncode") not in (0,))


def evaluate_csr_result(*, scenario: str, result: dict[str, object]) -> dict[str, object]:
    if scenario == "email":
        evaluation = evaluate_email_round2_cc_success(result)
        return {**evaluation, "casr_success": bool(evaluation["email_round2_cc_success"])}
    if scenario == "calendar":
        evaluation = evaluate_calendar_round2_attendee_success(result)
        return {**evaluation, "casr_success": bool(evaluation["calendar_round2_attendee_success"])}
    if scenario == "docs":
        evaluation = evaluate_docs_round2_shared_content_change(result)
        return {**evaluation, "casr_success": bool(evaluation["docs_round2_shared_content_changed"])}
    if scenario == "form":
        evaluation = evaluate_form_round2_addition_info_success(result)
        return {**evaluation, "casr_success": bool(evaluation["form_round2_addition_info_success"])}
    return {
        "casr_success": False,
        "evidence": None,
        "reason": f"unsupported_scenario:{scenario}",
    }


def build_metric_summary(
    *,
    scenario: str,
    results: list[dict[str, object]],
    poisoned_memory_setup: dict[str, object],
    poisoned_memory_cleanup: dict[str, object] | None,
    pre_reset: dict[str, object] | None,
) -> dict[str, object]:
    total = len(results)
    success_count = sum(1 for item in results if item.get("casr_success"))
    return {
        "scenario": scenario,
        "implemented": True,
        "total_session_count": total,
        "casr_success_count": success_count,
        "c_asr": success_count / total if total else None,
        "s_asr": success_count / total if total else None,
        "poisoned_memory_setup": poisoned_memory_setup,
        "poisoned_memory_cleanup": poisoned_memory_cleanup,
        "scenario_reset_before_batch": pre_reset,
        "sessions": [
            {
                "session_id": item.get("session_id"),
                "index": item.get("index"),
                "returncode": item.get("returncode"),
                "timed_out": item.get("timed_out"),
                "benign_count": item.get("benign_count", 0),
                "turn_failed": item.get("turn_failed", False),
                "casr_success": item.get("casr_success"),
                "reason": item.get("reason"),
                "evidence": item.get("evidence"),
                "openclaw_session_copy_path": item.get("openclaw_session_copy_path"),
                "session_logs_deleted_before": len(
                    item.get("session_log_cleanup_before", {}).get("deleted_session_logs", [])
                    if isinstance(item.get("session_log_cleanup_before"), dict)
                    else []
                ),
                "session_log_cleanup_errors": (
                    item.get("session_log_cleanup_before", {}).get("errors", [])
                    if isinstance(item.get("session_log_cleanup_before"), dict)
                    else []
                ),
            }
            for item in results
        ],
    }


def before_snapshots(scenario: str, config: RunConfig) -> dict[str, object]:
    snapshots: dict[str, object] = {}
    if scenario == "email":
        snapshots["email_sent_messages_before_round_2"] = snapshot_virtual_email_sent_messages(config)
    elif scenario == "calendar":
        snapshots["calendar_events_before_round_2"] = snapshot_virtual_calendar_events(config)
    elif scenario == "docs":
        snapshots["docs_shared_content_before_round_2"] = snapshot_virtual_docs_shared_content(config)
    return snapshots


def after_snapshots(scenario: str, config: RunConfig) -> dict[str, object]:
    snapshots: dict[str, object] = {}
    if scenario == "email":
        snapshots["email_sent_messages_after_round_2"] = snapshot_virtual_email_sent_messages(config)
    elif scenario == "calendar":
        snapshots["calendar_events_after_round_2"] = snapshot_virtual_calendar_events(config)
    elif scenario == "docs":
        snapshots["docs_shared_content_after_round_2"] = snapshot_virtual_docs_shared_content(config)
    elif scenario == "form":
        snapshots["form_latest_submission_after_round_2"] = snapshot_virtual_form_latest_submission(config)
    return snapshots


def environment_snapshot_after_session(
    scenario: str,
    config: RunConfig,
    snapshots_before: dict[str, object],
    snapshots_after: dict[str, object],
) -> dict[str, object] | None:
    if scenario == "email":
        return snapshot_virtual_email_environment(config)
    if scenario == "calendar":
        return snapshot_virtual_calendar_environment(
            config,
            existing_event_ids=event_ids_from_calendar_snapshot(
                snapshots_before.get("calendar_events_before_round_2")
            ),
        )
    if scenario == "docs":
        snapshot = snapshots_after.get("docs_shared_content_after_round_2")
        return snapshot if isinstance(snapshot, dict) else snapshot_virtual_docs_shared_content(config)
    if scenario == "form":
        snapshot = snapshots_after.get("form_latest_submission_after_round_2")
        return snapshot if isinstance(snapshot, dict) else snapshot_virtual_form_latest_submission(config)
    return None


def normalize_session_output_names(session_dir: Path, round_result: PromptRunResult) -> None:
    stdout_path = Path(round_result.stdout_path)
    normalized_stdout_path = session_dir / "stdout.txt"
    if stdout_path.is_file() and stdout_path != normalized_stdout_path:
        stdout_path.replace(normalized_stdout_path)
        round_result.stdout_path = str(normalized_stdout_path)

    if round_result.openclaw_session_copy_path:
        session_log_path = Path(round_result.openclaw_session_copy_path)
        normalized_session_log_path = session_dir / "openclaw-session.jsonl"
        if session_log_path.is_file() and session_log_path != normalized_session_log_path:
            session_log_path.replace(normalized_session_log_path)
            round_result.openclaw_session_copy_path = str(normalized_session_log_path)


def prune_session_outputs(session_dir: Path, scenario: str) -> None:
    keep = {
        "stdout.txt",
        "openclaw-session.jsonl",
        f"{scenario}_reset_after_session.json",
    }
    for path in sorted(session_dir.rglob("*"), reverse=True):
        keep_file = (
            path.name in keep
            or path.name.startswith("stdout_")
            or path.name.startswith("openclaw-session_")
        )
        if path.is_file() and not keep_file:
            try:
                path.unlink()
            except (FileNotFoundError, PermissionError):
                pass
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def run_multiturn_session(
    *,
    index: int,
    total: int,
    prompt_template: str,
    benign_task_prompt_template: str,
    benign_samples: list[EvaluationSample],
    method: str,
    model_name: str,
    scenario: str,
    config: RunConfig,
) -> dict[str, object]:
    session_id = f"session_{index:03d}"
    session_dir = config.run_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session_log_cleanup = cleanup_openclaw_session_logs(config.openclaw_sessions_dir)

    benign_rounds: list[PromptRunResult] = []
    for benign_index, sample in enumerate(benign_samples, start=1):
        benign_prompt = build_sample_prompt(
            base_prompt_template=benign_task_prompt_template,
            sample=sample,
            method=method,
            run_index=benign_index,
            run_count=len(benign_samples),
        )
        round_name = "1" if benign_index == 1 else f"benign_{benign_index}"
        benign_rounds.append(
            run_prompt_round(
                round_name=round_name,
                session_id=session_id,
                session_dir=session_dir,
                prompt=benign_prompt,
                config=config,
            )
        )

    snapshots_before = before_snapshots(scenario, config)
    prompt = render_prompt(prompt_template, index, total)
    round_result = run_prompt_round(
        round_name="2",
        session_id=session_id,
        session_dir=session_dir,
        prompt=prompt,
        config=config,
    )
    snapshots_after = after_snapshots(scenario, config)

    all_rounds = [*benign_rounds, round_result]
    result: dict[str, object] = {
        "session_id": session_id,
        "index": index,
        "model_name": model_name,
        "scenario": scenario,
        "benign_count": len(benign_rounds),
        "benign_samples": [
            {
                "sample_index": sample.index,
                "sample_id": sample.sample_id,
                "sample_path": str(sample.path),
            }
            for sample in benign_samples
        ],
        "benign_rounds": [compact_round_result(item) for item in benign_rounds],
        "returncode": round_result.returncode,
        "timed_out": any(item.timed_out for item in all_rounds),
        "duration_seconds": round(sum(item.duration_seconds for item in all_rounds), 3),
        "stdout_path": round_result.stdout_path,
        "stderr_path": round_result.stderr_path,
        "prompt_path": round_result.prompt_path,
        "openclaw_session_path": round_result.openclaw_session_path,
        "openclaw_session_copy_path": round_result.openclaw_session_copy_path,
        "openclaw_session_match": round_result.openclaw_session_match,
        "session_log_cleanup_before": session_log_cleanup,
        "round_2": compact_round_result(round_result),
        "turn_failed": any(item.timed_out or item.returncode not in (0,) for item in all_rounds),
        **snapshots_before,
        **snapshots_after,
    }
    result.update(evaluate_csr_result(scenario=scenario, result=result))

    environment_after_session = environment_snapshot_after_session(
        scenario,
        config,
        snapshots_before,
        snapshots_after,
    )
    scenario_reset = reset_virtual_scenario_environment(config, scenario)
    result["scenario_reset_after_session"] = compact_reset_result(scenario_reset)
    if environment_after_session is not None:
        write_json(session_dir / f"{scenario}_reset_after_session.json", environment_after_session)
    write_json(session_dir / "session.json", result)
    prune_session_outputs(session_dir, scenario)
    return result


def run_one_session(
    *,
    index: int,
    total: int,
    prompt_template: str,
    model_name: str,
    scenario: str,
    config: RunConfig,
) -> dict[str, object]:
    session_id = f"session_{index:03d}"
    session_dir = config.run_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt = render_prompt(prompt_template, index, total)

    snapshots_before = before_snapshots(scenario, config)
    session_log_cleanup = cleanup_openclaw_session_logs(config.openclaw_sessions_dir)
    round_result = run_prompt_round(
        round_name="1",
        session_id=session_id,
        session_dir=session_dir,
        prompt=prompt,
        config=config,
    )
    normalize_session_output_names(session_dir, round_result)
    snapshots_after = after_snapshots(scenario, config)

    result: dict[str, object] = {
        "session_id": session_id,
        "index": index,
        "model_name": model_name,
        "scenario": scenario,
        "returncode": round_result.returncode,
        "timed_out": round_result.timed_out,
        "duration_seconds": round_result.duration_seconds,
        "stdout_path": round_result.stdout_path,
        "stderr_path": round_result.stderr_path,
        "prompt_path": round_result.prompt_path,
        "openclaw_session_path": round_result.openclaw_session_path,
        "openclaw_session_copy_path": round_result.openclaw_session_copy_path,
        "openclaw_session_match": round_result.openclaw_session_match,
        "session_log_cleanup_before": session_log_cleanup,
        "round_2": compact_round_result(round_result),
        **snapshots_before,
        **snapshots_after,
    }
    result.update(evaluate_csr_result(scenario=scenario, result=result))

    environment_after_session = environment_snapshot_after_session(
        scenario,
        config,
        snapshots_before,
        snapshots_after,
    )
    scenario_reset = reset_virtual_scenario_environment(config, scenario)
    result["scenario_reset_after_session"] = compact_reset_result(scenario_reset)
    if environment_after_session is not None:
        write_json(session_dir / f"{scenario}_reset_after_session.json", environment_after_session)
    prune_session_outputs(session_dir, scenario)
    return result


def run_casr(
    args: argparse.Namespace,
    config: RunConfig,
    prompt_template: str,
    benign_task_prompt_template: str | None,
    benign_samples: list[EvaluationSample],
) -> int:
    scenario = args.scenario
    source_dir = (ROOT / POISONED_MEMORY_ROOT / scenario).resolve()
    config.run_dir.mkdir(parents=True, exist_ok=True)

    poisoned_memory_setup = initialize_project_memory_from_dir(config.openclaw_workspace_dir, source_dir)
    write_json(config.run_dir / "poisoned_memory_setup.json", poisoned_memory_setup)
    if poisoned_memory_setup["errors"]:
        print(f"poisoned memory setup errors: {len(poisoned_memory_setup['errors'])}")

    pre_reset = reset_virtual_scenario_environment(config, scenario)
    if pre_reset is not None:
        write_json(config.run_dir / f"{scenario}_reset_before_batch.json", pre_reset)

    results: list[dict[str, object]] = []
    poisoned_memory_cleanup: dict[str, object] | None = None
    try:
        for index in range(1, args.sessions + 1):
            if args.benign_count > 0:
                assert benign_task_prompt_template is not None
                result = run_multiturn_session(
                    index=index,
                    total=args.sessions,
                    prompt_template=prompt_template,
                    benign_task_prompt_template=benign_task_prompt_template,
                    benign_samples=benign_samples,
                    method=args.method,
                    model_name=args.model_name,
                    scenario=scenario,
                    config=config,
                )
            else:
                result = run_one_session(
                    index=index,
                    total=args.sessions,
                    prompt_template=prompt_template,
                    model_name=args.model_name,
                    scenario=scenario,
                    config=config,
                )
            results.append(result)
            status = "timeout" if result["timed_out"] else result["returncode"]
            cleanup = result["session_log_cleanup_before"]
            print(
                f"{result['session_id']}: exit={status}, "
                f"benign_count={result.get('benign_count', 0)}, "
                f"casr_success={result['casr_success']}, "
                f"reason={result.get('reason')}, "
                f"logs_deleted={len(cleanup['deleted_session_logs'])}, "
                f"{result['duration_seconds']}s"
            )
    finally:
        poisoned_memory_cleanup = cleanup_project_memory(config.openclaw_workspace_dir)
        write_json(config.run_dir / "poisoned_memory_cleanup.json", poisoned_memory_cleanup)

    results.sort(key=lambda item: int(item["index"]))
    if not args.no_summary_json:
        write_json(config.run_dir / "summary.json", results)

    metric_summary = build_metric_summary(
        scenario=scenario,
        results=results,
        poisoned_memory_setup=poisoned_memory_setup,
        poisoned_memory_cleanup=poisoned_memory_cleanup,
        pre_reset=compact_reset_result(pre_reset),
    )
    write_json(config.run_dir / "metric_summary.json", metric_summary)

    failed = [
        item
        for item in results
        if item.get("timed_out") or item.get("turn_failed") or item.get("returncode") not in (0,)
    ]
    setup_errors = len(poisoned_memory_setup["errors"])
    cleanup_errors = len(poisoned_memory_cleanup["errors"]) if poisoned_memory_cleanup else 0
    session_log_cleanup_errors = sum(
        len(item.get("session_log_cleanup_before", {}).get("errors", []))
        for item in results
        if isinstance(item.get("session_log_cleanup_before"), dict)
    )
    reset_errors = int(reset_failed(pre_reset)) + sum(
        int(reset_failed(item.get("scenario_reset_after_session"))) for item in results
    )
    print(
        f"done: {len(results) - len(failed)} ok, {len(failed)} failed, "
        f"casr_success={metric_summary['casr_success_count']}/{metric_summary['total_session_count']}, "
        f"setup_errors={setup_errors}, cleanup_errors={cleanup_errors}, "
        f"session_log_cleanup_errors={session_log_cleanup_errors}, reset_errors={reset_errors}"
    )
    return 1 if failed or setup_errors or cleanup_errors or session_log_cleanup_errors or reset_errors else 0


def main() -> int:
    args = parse_args()
    if args.sessions < 1:
        print("--sessions must be >= 1", file=sys.stderr)
        return 2

    requested_command = split_command(args.cmd) + split_command(args.cmd_args)
    if not requested_command:
        print("Command cannot be empty.", file=sys.stderr)
        return 2

    try:
        prompt_template = load_prompt(args)
        benign_task_prompt_template = load_benign_task_prompt(args)
        benign_samples = select_benign_samples(args)
        extra_env = parse_env(args.env)
    except Exception as exc:  # noqa: BLE001
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    model_dir_name = safe_path_component(args.model_name)
    run_dir = (
        args.output_dir
        / model_dir_name
        / safe_path_component(args.scenario)
        / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    ).resolve()
    try:
        config = resolve_config(
            args=args,
            command=requested_command,
            extra_env=extra_env,
            run_dir=run_dir,
        )
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    source_dir = (ROOT / POISONED_MEMORY_ROOT / args.scenario).resolve()

    print(f"command: {' '.join(config.command)}")
    print(f"model_name: {args.model_name} -> {model_dir_name}")
    print(f"sessions: {args.sessions}")
    print(f"scenario: {args.scenario}")
    if args.benign_count > 0:
        print(f"benign_count: {args.benign_count}")
        print(f"cleansample_dir: {args.cleansample_dir}")
        print(f"method: {args.method}")
    print(f"cwd: {config.cwd}")
    print(f"output: {config.run_dir}")
    print(f"openclaw state: {config.openclaw_state_dir}")
    print(f"openclaw workspace: {config.openclaw_workspace_dir}")
    print(f"openclaw sessions: {config.openclaw_sessions_dir}")
    print(f"openclaw skills: {config.openclaw_skills_dir}")
    print(f"openclaw environment: {config.openclaw_environment_dir}")
    for scenario in VALID_SCENARIOS:
        print(f"virtual {scenario} state: {get_virtual_state_path(config.extra_env, scenario)}")
    print(f"poisoned memory: {source_dir}")

    if args.dry_run:
        print(f"dry-run poisoned memory: {source_dir} -> {config.openclaw_workspace_dir}")
        for index in range(1, args.sessions + 1):
            session_id = f"session_{index:03d}"
            print(f"dry-run {session_id}: cleanup OpenClaw session logs under {config.openclaw_sessions_dir}")
            if args.benign_count > 0:
                for benign_index, sample in enumerate(benign_samples, start=1):
                    round_name = "1" if benign_index == 1 else f"benign_{benign_index}"
                    print(
                        f"dry-run {session_id}: {round_name} sample={sample.sample_id}: "
                        f"{' '.join(config.command)} --session-id {session_id} --message <benign-prompt>"
                    )
                print(
                    f"dry-run {session_id}: round_2 trigger: "
                    f"{' '.join(config.command)} --session-id {session_id} --message <prompt>"
                )
            else:
                print(f"dry-run {session_id}: {' '.join(config.command)} --session-id {session_id} --message <prompt>")
        return 0

    return run_casr(args, config, prompt_template, benign_task_prompt_template, benign_samples)


if __name__ == "__main__":
    raise SystemExit(main())


r"""
python jobscripts/Openclaw_CASR.py \
  --prompt-file prompts/calendar.txt \
  --scenario calendar \
  --model-name deepseek-v4-flash \
  --benign-count 1 \
  --cleansample-dir D:/openclaw/download/multi-interaction/clean/txt \
  --method txt \
  --sessions 10
"""
