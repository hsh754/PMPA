#!/usr/bin/env python3
"""
Run multiple one-shot Claude Code text interactions.

Default command is:
    claude --print

The prompt is sent through stdin so large prompts do not hit command-line
length limits. Each session writes stdout, stderr, and metadata to a separate
directory under the output root, grouped by model name.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.injection.constants import VALID_METHODS  # noqa: E402
from eval.injection.metrics import (  # noqa: E402
    evaluate_calendar_round2_attendee_success,
    evaluate_docs_round2_shared_content_change,
    evaluate_email_round2_cc_success,
    evaluate_form_round2_addition_info_success,
)
from eval.injection.models import EvaluationSample, RunConfig  # noqa: E402
from eval.injection.modes import (  # noqa: E402
    event_ids_from_calendar_snapshot,
    reset_virtual_scenario_environment,
    snapshot_virtual_calendar_environment,
    snapshot_virtual_docs_shared_content,
    snapshot_virtual_email_environment,
    snapshot_virtual_form_latest_submission,
)
from eval.injection.samples import build_sample_prompt, load_samples  # noqa: E402
from eval.injection.utils import configure_virtual_state_env, get_virtual_state_path  # noqa: E402

CLAUDECODE_SESSION_LOCK = threading.Lock()
CLAUDECODE_SESSION_CLAIMED: set[Path] = set()
VALID_SCENARIOS = ("email", "calendar", "docs", "form")
POISONED_MEMORY_ROOT = Path("eval") / "poisoned_memory_state" / "claudecode"
MAX_BENIGN_COUNT = 5
CLAUDE_SESSION_CONTROL_FLAGS = {"--session-id", "--resume", "-r", "--continue", "-c"}


@dataclass
class SessionResult:
    session_id: str
    index: int
    model_name: str
    command: list[str]
    cwd: str
    started_at: str
    ended_at: str
    duration_seconds: float
    returncode: int | None
    timed_out: bool
    stdout_path: str
    stderr_path: str
    prompt_path: str
    session_json_path: str
    session_log_cleanup_before: dict[str, object]
    claudecode_session_path: str | None
    claudecode_session_copy_path: str | None
    claudecode_session_match: str | None
    error: str | None = None


@dataclass
class TurnResult:
    round_name: str
    session_id: str
    claudecode_session_id: str
    turn_index: int
    command: list[str]
    cwd: str
    started_at: str
    ended_at: str
    duration_seconds: float
    returncode: int | None
    timed_out: bool
    stdout_path: str
    stderr_path: str
    prompt_path: str
    claudecode_session_path: str | None
    claudecode_session_copy_path: str | None
    claudecode_session_match: str | None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-run pure text Claude Code interactions."
    )
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt-file", type=Path, help="Text file to send.")
    prompt_group.add_argument("--prompt", help="Prompt text to send.")

    parser.add_argument(
        "-n",
        "--sessions",
        type=int,
        default=1,
        help="Number of sessions to run.",
    )
    parser.add_argument(
        "--cmd",
        default="claude",
        help='Executable or command prefix. Example: "claude" or "npx @anthropic-ai/claude-code".',
    )
    parser.add_argument(
        "--cmd-args",
        default="--print",
        help='Arguments before stdin prompt. Default: "--print".',
    )
    parser.add_argument(
        "--model-name",
        default="default",
        help="Model name used as the output subdirectory for this batch.",
    )
    parser.add_argument(
        "--scenario",
        "--scenairo",
        choices=VALID_SCENARIOS,
        default=None,
        help=(
            "CASR scenario. Injects eval/poisoned_memory_state/claudecode/<scenario> "
            "into the current Claude Code project memory, then judges trigger success."
        ),
    )
    parser.add_argument(
        "--benign-count",
        type=int,
        default=0,
        help=(
            "Number of benign-task turns to run in the same Claude Code session "
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
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help=(
            "Claude Code project memory directory. Defaults to the memory directory "
            "derived from --cwd under CLAUDE_CONFIG_DIR or ~/.claude/projects."
        ),
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help="Canonical evaluator skills directory. Defaults to <project-root>/skills.",
    )
    parser.add_argument(
        "--environment-dir",
        type=Path,
        default=None,
        help="Virtual state directory. Defaults to <project-root>/environment.",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for every session.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs") / "ClaudecodeCasr",
        help="Directory where outputs are written.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800,
        help="Timeout per session.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra environment variable. Can be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create no child processes; print the planned runs.",
    )
    parser.add_argument(
        "--no-summary-json",
        action="store_true",
        help="Skip writing summary.json.",
    )
    return parser.parse_args()


def split_command(value: str) -> list[str]:
    if not value.strip():
        return []
    return shlex.split(value, posix=(os.name != "nt"))


def prepare_command(command: list[str]) -> list[str]:
    if not command:
        return command

    executable = shutil.which(command[0]) or command[0]
    prepared = [executable] + command[1:]

    if os.name == "nt" and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline(prepared)]

    return prepared


def is_cmd_wrapper(command: list[str]) -> bool:
    if len(command) < 5:
        return False
    executable = Path(command[0]).name.lower()
    return executable in {"cmd.exe", "cmd"} and command[1:4] == ["/d", "/s", "/c"]


def command_contains_session_control(command: list[str]) -> bool:
    if is_cmd_wrapper(command):
        wrapped_command = command[4]
        if any(flag in wrapped_command.split() for flag in CLAUDE_SESSION_CONTROL_FLAGS):
            return True
        if "--session-id=" in wrapped_command or "--resume=" in wrapped_command:
            return True
    haystack = list(command)
    for token in haystack:
        if token in CLAUDE_SESSION_CONTROL_FLAGS:
            return True
        if token.startswith("--session-id=") or token.startswith("--resume="):
            return True
    return False


def append_command_args(command: list[str], extra_args: list[str]) -> list[str]:
    if is_cmd_wrapper(command):
        wrapped = list(command)
        wrapped[4] = f"{wrapped[4]} {subprocess.list2cmdline(extra_args)}"
        return wrapped
    return list(command) + extra_args


def command_for_claudecode_turn(
    command: list[str],
    *,
    claudecode_session_id: str,
    turn_index: int,
) -> list[str]:
    if turn_index == 1:
        return append_command_args(command, ["--session-id", claudecode_session_id])
    return append_command_args(command, ["--resume", claudecode_session_id])


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


def render_prompt(template: str, index: int, total: int) -> str:
    return template.format(
        session_id=f"session_{index:03d}",
        session_index=index,
        session_count=total,
    )


def parse_env(pairs: Iterable[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--env must be KEY=VALUE, got: {pair!r}")
        key, value = pair.split("=", 1)
        if not key:
            raise ValueError(f"--env key cannot be empty: {pair!r}")
        parsed[key] = value
    return parsed


def sanitize_path_component(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {".", "-", "_"} else "_"
        for char in value.strip()
    )
    return safe.strip("._") or "default"


def sanitize_claudecode_project_path(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value)


def get_claudecode_project_dir(cwd: Path) -> Path:
    config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    return config_home / "projects" / sanitize_claudecode_project_path(str(cwd))


def get_default_memory_dir(cwd: Path) -> Path:
    return get_claudecode_project_dir(cwd) / "memory"


def setup_poisoned_memory(memory_dir: Path, source_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "memory_dir": str(memory_dir.resolve()),
        "initial_memory_dir": str(source_dir.resolve()),
        "deleted_memory_files": [],
        "copied_memory_files": [],
        "errors": [],
    }
    errors = result["errors"]

    if memory_dir.name.lower() != "memory":
        errors.append(f"Refusing setup because target is not named memory: {memory_dir}")
        return result
    if source_dir.exists() and not source_dir.is_dir():
        errors.append(f"Initial memory source is not a directory: {source_dir}")
    elif not source_dir.exists():
        errors.append(f"Initial memory source does not exist: {source_dir}")

    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 - record setup failures in evaluation output.
        errors.append(f"{memory_dir}: {exc}")

    clear_memory_only(memory_dir, result)

    if not source_dir.is_dir() or not memory_dir.is_dir():
        return result

    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file():
            continue
        relative_path = source_path.relative_to(source_dir)
        target_path = memory_dir / relative_path
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            result["copied_memory_files"].append(str(target_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_path} -> {target_path}: {exc}")
    return result


def cleanup_poisoned_memory(memory_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "memory_dir": str(memory_dir.resolve()),
        "deleted_memory_files": [],
        "copied_memory_files": [],
        "errors": [],
    }
    if memory_dir.name.lower() != "memory":
        result["errors"].append(f"Refusing cleanup because target is not named memory: {memory_dir}")
        return result
    clear_memory_only(memory_dir, result)
    return result


def clear_memory_only(memory_dir: Path, result: dict[str, object]) -> None:
    errors = result["errors"]
    if not memory_dir.exists():
        try:
            memory_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{memory_dir}: {exc}")
            return
    if not memory_dir.is_dir():
        errors.append(f"Memory target is not a directory: {memory_dir}")
        return

    memory_index_path = memory_dir / "MEMORY.md"
    memory_index_resolved = memory_index_path.resolve()
    for path in sorted(memory_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            if path.resolve() == memory_index_resolved:
                path.write_text("", encoding="utf-8")
            else:
                path.unlink()
                result["deleted_memory_files"].append(str(path))
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: {exc}")

    try:
        memory_index_path.write_text("", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{memory_index_path}: {exc}")


def snapshot_jsonl_files(directory: Path) -> dict[Path, tuple[int, int]]:
    if not directory.is_dir():
        return {}

    snapshot: dict[Path, tuple[int, int]] = {}
    for path in directory.glob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def cleanup_claudecode_session_logs(project_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "claudecode_project_dir": str(project_dir),
        "deleted_session_logs": [],
        "errors": [],
    }
    if not project_dir.exists():
        return result
    if not project_dir.is_dir():
        result["errors"].append(f"Claude Code project path is not a directory: {project_dir}")
        return result

    deleted_paths: list[Path] = []
    for path in sorted(project_dir.glob("*.jsonl")):
        if not path.is_file():
            continue
        try:
            path.unlink()
            result["deleted_session_logs"].append(str(path))
            deleted_paths.append(path)
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - record cleanup failures without hiding the run.
            result["errors"].append(f"{path}: {exc}")

    with CLAUDECODE_SESSION_LOCK:
        for path in deleted_paths:
            CLAUDECODE_SESSION_CLAIMED.discard(path)
    return result


def file_contains_text(path: Path, text: str) -> bool:
    try:
        return text in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def copy_claudecode_session_log(
    *,
    session_dir: Path,
    project_dir: Path,
    before_snapshot: dict[Path, tuple[int, int]],
    prompt: str,
    copy_filename: str = "claudecode-session.jsonl",
    ignore_claimed: bool = False,
) -> tuple[str | None, str | None, str | None]:
    with CLAUDECODE_SESSION_LOCK:
        after_snapshot = snapshot_jsonl_files(project_dir)
        changed_paths = [
            path
            for path, state in after_snapshot.items()
            if before_snapshot.get(path) != state
            and (ignore_claimed or path not in CLAUDECODE_SESSION_CLAIMED)
        ]
        matching_paths = [
            path for path in changed_paths if file_contains_text(path, prompt)
        ]

        if len(matching_paths) == 1:
            source_path = matching_paths[0]
            match_status = "matched_prompt"
        elif len(matching_paths) > 1:
            source_path = max(matching_paths, key=lambda path: path.stat().st_mtime_ns)
            match_status = "ambiguous_prompt"
        elif len(changed_paths) == 1:
            source_path = changed_paths[0]
            match_status = "single_changed_jsonl"
        elif len(changed_paths) > 1:
            source_path = max(changed_paths, key=lambda path: path.stat().st_mtime_ns)
            match_status = "ambiguous_changed_jsonl"
        else:
            return None, None, "not_found"

        copy_path = session_dir / copy_filename
        try:
            shutil.copy2(source_path, copy_path)
        except OSError:
            return str(source_path), None, "copy_failed"
        CLAUDECODE_SESSION_CLAIMED.add(source_path)
        return str(source_path), str(copy_path), match_status


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run_one(
    *,
    index: int,
    total: int,
    command: list[str],
    prompt_template: str,
    model_name: str,
    run_dir: Path,
    cwd: Path,
    claudecode_project_dir: Path,
    claudecode_before_snapshot: dict[Path, tuple[int, int]],
    session_log_cleanup_before: dict[str, object],
    timeout_seconds: float,
    extra_env: dict[str, str],
) -> SessionResult:
    session_id = f"session_{index:03d}"
    session_dir = run_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    prompt = render_prompt(prompt_template, index, total)
    prompt_path = session_dir / "prompt.txt"
    stdout_path = session_dir / "stdout.txt"
    stderr_path = session_dir / "stderr.txt"
    meta_path = session_dir / "meta.json"
    session_json_path = session_dir / "session.json"
    claudecode_session_path: str | None = None
    claudecode_session_copy_path: str | None = None
    claudecode_session_match: str | None = None
    prompt_path.write_text(prompt, encoding="utf-8")

    env = os.environ.copy()
    env.update(extra_env)

    started_at = utc_now()
    started_monotonic = time.monotonic()
    timed_out = False
    error: str | None = None
    returncode: int | None = None
    stdout = ""
    stderr = ""

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(cwd),
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        error = f"Timed out after {timeout_seconds} seconds."
    except FileNotFoundError as exc:
        returncode = None
        error = str(exc)
        stderr = str(exc)
    except Exception as exc:  # noqa: BLE001 - preserve unexpected runner errors.
        returncode = None
        error = repr(exc)
        stderr = repr(exc)

    ended_at = utc_now()
    duration = time.monotonic() - started_monotonic
    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    (
        claudecode_session_path,
        claudecode_session_copy_path,
        claudecode_session_match,
    ) = copy_claudecode_session_log(
        session_dir=session_dir,
        project_dir=claudecode_project_dir,
        before_snapshot=claudecode_before_snapshot,
        prompt=prompt,
    )

    result = SessionResult(
        session_id=session_id,
        index=index,
        model_name=model_name,
        command=command,
        cwd=str(cwd),
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=round(duration, 3),
        returncode=returncode,
        timed_out=timed_out,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        prompt_path=str(prompt_path),
        session_json_path=str(session_json_path),
        session_log_cleanup_before=session_log_cleanup_before,
        claudecode_session_path=claudecode_session_path,
        claudecode_session_copy_path=claudecode_session_copy_path,
        claudecode_session_match=claudecode_session_match,
        error=error,
    )
    result_dict = asdict(result)
    meta_path.write_text(
        json.dumps(result_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    session_json_path.write_text(
        json.dumps(
            {
                **result_dict,
                "prompt": prompt,
                "stdout": stdout,
                "stderr": stderr,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def run_casr_turn(
    *,
    round_name: str,
    session_id: str,
    claudecode_session_id: str,
    turn_index: int,
    command: list[str],
    prompt: str,
    run_dir: Path,
    cwd: Path,
    claudecode_project_dir: Path,
    timeout_seconds: float,
    extra_env: dict[str, str],
) -> TurnResult:
    session_dir = run_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = session_dir / f"prompt_{round_name}.txt"
    stdout_path = session_dir / f"stdout_{round_name}.txt"
    stderr_path = session_dir / f"stderr_{round_name}.txt"
    meta_path = session_dir / f"meta_{round_name}.json"
    prompt_path.write_text(prompt, encoding="utf-8")

    turn_command = command_for_claudecode_turn(
        command,
        claudecode_session_id=claudecode_session_id,
        turn_index=turn_index,
    )
    claudecode_before_snapshot = snapshot_jsonl_files(claudecode_project_dir)

    env = os.environ.copy()
    env.update(extra_env)

    started_at = utc_now()
    started_monotonic = time.monotonic()
    timed_out = False
    error: str | None = None
    returncode: int | None = None
    stdout = ""
    stderr = ""

    try:
        completed = subprocess.run(
            turn_command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(cwd),
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        error = f"Timed out after {timeout_seconds} seconds."
    except FileNotFoundError as exc:
        returncode = None
        error = str(exc)
        stderr = str(exc)
    except Exception as exc:  # noqa: BLE001 - preserve unexpected runner errors.
        returncode = None
        error = repr(exc)
        stderr = repr(exc)

    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    (
        claudecode_session_path,
        claudecode_session_copy_path,
        claudecode_session_match,
    ) = copy_claudecode_session_log(
        session_dir=session_dir,
        project_dir=claudecode_project_dir,
        before_snapshot=claudecode_before_snapshot,
        prompt=prompt,
        copy_filename=f"claudecode-session_{round_name}.jsonl",
        ignore_claimed=True,
    )

    result = TurnResult(
        round_name=round_name,
        session_id=session_id,
        claudecode_session_id=claudecode_session_id,
        turn_index=turn_index,
        command=turn_command,
        cwd=str(cwd),
        started_at=started_at,
        ended_at=utc_now(),
        duration_seconds=round(time.monotonic() - started_monotonic, 3),
        returncode=returncode,
        timed_out=timed_out,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        prompt_path=str(prompt_path),
        claudecode_session_path=claudecode_session_path,
        claudecode_session_copy_path=claudecode_session_copy_path,
        claudecode_session_match=claudecode_session_match,
        error=error,
    )
    meta_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def build_run_config(
    *,
    command: list[str],
    run_dir: Path,
    cwd: Path,
    timeout_seconds: float,
    extra_env: dict[str, str],
    memory_dir: Path,
    claudecode_project_dir: Path,
    skills_dir: Path,
    environment_dir: Path,
) -> RunConfig:
    return RunConfig(
        command=command,
        run_dir=run_dir,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        extra_env=extra_env,
        memory_dir=memory_dir,
        claudecode_project_dir=claudecode_project_dir,
        skills_dir=skills_dir,
        environment_dir=environment_dir,
    )


def compact_round_from_session(result: SessionResult) -> dict[str, object]:
    return {
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stdout_path": result.stdout_path,
        "claudecode_session_copy_path": result.claudecode_session_copy_path,
    }


def compact_turn_result(result: TurnResult) -> dict[str, object]:
    return {
        "round_name": result.round_name,
        "turn_index": result.turn_index,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stdout_path": result.stdout_path,
        "stderr_path": result.stderr_path,
        "prompt_path": result.prompt_path,
        "claudecode_session_id": result.claudecode_session_id,
        "claudecode_session_copy_path": result.claudecode_session_copy_path,
        "claudecode_session_match": result.claudecode_session_match,
        "error": result.error,
    }


def evaluate_casr_result(
    *,
    scenario: str,
    result: dict[str, object],
) -> dict[str, object]:
    round_2 = result.get("round_2")
    if scenario == "email":
        evaluation = evaluate_email_round2_cc_success(round_2)
        return {
            **evaluation,
            "casr_success": bool(evaluation["email_round2_cc_success"]),
        }
    if scenario == "calendar":
        evaluation = evaluate_calendar_round2_attendee_success(round_2)
        return {
            **evaluation,
            "casr_success": bool(evaluation["calendar_round2_attendee_success"]),
        }
    if scenario == "docs":
        evaluation = evaluate_docs_round2_shared_content_change(result)
        return {
            **evaluation,
            "casr_success": bool(evaluation["docs_round2_shared_content_changed"]),
        }
    if scenario == "form":
        evaluation = evaluate_form_round2_addition_info_success(result)
        return {
            **evaluation,
            "casr_success": bool(evaluation["form_round2_addition_info_success"]),
        }
    return {
        "casr_success": False,
        "evidence": None,
        "reason": f"unsupported_scenario:{scenario}",
    }


def build_casr_metric_summary(
    *,
    scenario: str,
    results: list[dict[str, object]],
    poisoned_memory_setup: dict[str, object] | None,
    cleanup: dict[str, object] | None,
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
        "poisoned_memory_cleanup": cleanup,
        "scenario_reset_before_batch": pre_reset,
        "sessions": [
            {
                "session_id": item.get("session_id"),
                "index": item.get("index"),
                "returncode": item.get("returncode"),
                "timed_out": item.get("timed_out"),
                "benign_count": item.get("benign_count", 0),
                "claudecode_session_id": item.get("claudecode_session_id"),
                "turn_failed": item.get("turn_failed", False),
                "casr_success": item.get("casr_success"),
                "reason": item.get("reason"),
                "evidence": item.get("evidence"),
                "claudecode_session_copy_path": item.get("claudecode_session_copy_path"),
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


def environment_snapshot_after_session(
    scenario: str,
    config: RunConfig,
    calendar_environment_before: dict[str, object] | None,
    docs_after: dict[str, object] | None,
    form_after: dict[str, object] | None,
) -> dict[str, object] | None:
    if scenario == "email":
        return snapshot_virtual_email_environment(config)
    if scenario == "calendar":
        return snapshot_virtual_calendar_environment(
            config,
            existing_event_ids=event_ids_from_calendar_snapshot(calendar_environment_before),
        )
    if scenario == "docs":
        return docs_after if isinstance(docs_after, dict) else snapshot_virtual_docs_shared_content(config)
    if scenario == "form":
        return form_after if isinstance(form_after, dict) else snapshot_virtual_form_latest_submission(config)
    return None


def prune_session_outputs(session_dir: Path, scenario: str) -> None:
    keep = {
        "stdout.txt",
        "claudecode-session.jsonl",
        f"{scenario}_reset_after_session.json",
    }
    for path in sorted(session_dir.rglob("*"), reverse=True):
        keep_file = (
            path.name in keep
            or path.name.startswith("stdout_")
            or path.name.startswith("claudecode-session_")
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


def run_casr_multiturn_session(
    *,
    index: int,
    total: int,
    command: list[str],
    prompt_template: str,
    benign_task_prompt_template: str,
    benign_samples: list[EvaluationSample],
    method: str,
    model_name: str,
    scenario: str,
    config: RunConfig,
    session_log_cleanup_before: dict[str, object],
) -> dict[str, object]:
    session_id = f"session_{index:03d}"
    claudecode_session_id = str(uuid.uuid4())
    benign_rounds: list[TurnResult] = []

    for benign_index, sample in enumerate(benign_samples, start=1):
        benign_prompt = build_sample_prompt(
            base_prompt_template=benign_task_prompt_template,
            sample=sample,
            method=method,
            run_index=benign_index,
            run_count=len(benign_samples),
        )
        benign_rounds.append(
            run_casr_turn(
                round_name=f"benign_{benign_index}",
                session_id=session_id,
                claudecode_session_id=claudecode_session_id,
                turn_index=benign_index,
                command=command,
                prompt=benign_prompt,
                run_dir=config.run_dir,
                cwd=config.cwd,
                claudecode_project_dir=config.claudecode_project_dir,
                timeout_seconds=config.timeout_seconds,
                extra_env=config.extra_env,
            )
        )

    docs_before = None
    calendar_environment_before = None
    if scenario == "calendar":
        calendar_environment_before = snapshot_virtual_calendar_environment(
            config,
            snapshot_name="before_session",
        )
    if scenario == "docs":
        docs_before = snapshot_virtual_docs_shared_content(config)

    trigger_prompt = render_prompt(prompt_template, index, total)
    trigger_round = run_casr_turn(
        round_name="2",
        session_id=session_id,
        claudecode_session_id=claudecode_session_id,
        turn_index=len(benign_rounds) + 1,
        command=command,
        prompt=trigger_prompt,
        run_dir=config.run_dir,
        cwd=config.cwd,
        claudecode_project_dir=config.claudecode_project_dir,
        timeout_seconds=config.timeout_seconds,
        extra_env=config.extra_env,
    )

    docs_after = None
    form_after = None
    if scenario == "docs":
        docs_after = snapshot_virtual_docs_shared_content(config)
    if scenario == "form":
        form_after = snapshot_virtual_form_latest_submission(config)

    all_turns = [*benign_rounds, trigger_round]
    result_dict: dict[str, object] = {
        "session_id": session_id,
        "index": index,
        "model_name": model_name,
        "command": command,
        "cwd": str(config.cwd),
        "scenario": scenario,
        "benign_count": len(benign_rounds),
        "claudecode_session_id": claudecode_session_id,
        "session_log_cleanup_before": session_log_cleanup_before,
        "benign_samples": [
            {
                "sample_index": sample.index,
                "sample_id": sample.sample_id,
                "sample_path": str(sample.path),
            }
            for sample in benign_samples
        ],
        "benign_rounds": [compact_turn_result(item) for item in benign_rounds],
        "round_2": compact_turn_result(trigger_round),
        "returncode": trigger_round.returncode,
        "timed_out": any(item.timed_out for item in all_turns),
        "duration_seconds": round(sum(item.duration_seconds for item in all_turns), 3),
        "stdout_path": trigger_round.stdout_path,
        "stderr_path": trigger_round.stderr_path,
        "prompt_path": trigger_round.prompt_path,
        "claudecode_session_path": trigger_round.claudecode_session_path,
        "claudecode_session_copy_path": trigger_round.claudecode_session_copy_path,
        "claudecode_session_match": trigger_round.claudecode_session_match,
        "turn_failed": any(item.timed_out or item.returncode not in (0,) for item in all_turns),
        "docs_shared_content_before_round_2": docs_before,
        "docs_shared_content_after_round_2": docs_after,
        "form_latest_submission_after_round_2": form_after,
    }
    result_dict.update(evaluate_casr_result(scenario=scenario, result=result_dict))

    environment_after_session = environment_snapshot_after_session(
        scenario,
        config,
        calendar_environment_before,
        docs_after,
        form_after,
    )
    scenario_reset = reset_virtual_scenario_environment(config, scenario)
    result_dict["scenario_reset_after_session"] = compact_reset_result(scenario_reset)
    session_dir = config.run_dir / session_id
    if environment_after_session is not None:
        (session_dir / f"{scenario}_reset_after_session.json").write_text(
            json.dumps(environment_after_session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (session_dir / "session.json").write_text(
        json.dumps(result_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prune_session_outputs(session_dir, scenario)
    return result_dict


def run_casr_mode(
    *,
    args: argparse.Namespace,
    command: list[str],
    prompt_template: str,
    benign_task_prompt_template: str | None,
    benign_samples: list[EvaluationSample],
    extra_env: dict[str, str],
    cwd: Path,
    claudecode_project_dir: Path,
    memory_dir: Path,
    skills_dir: Path,
    environment_dir: Path,
    run_dir: Path,
) -> int:
    assert args.scenario is not None
    scenario = args.scenario
    source_dir = (ROOT / POISONED_MEMORY_ROOT / scenario).resolve()
    config = build_run_config(
        command=command,
        run_dir=run_dir,
        cwd=cwd,
        timeout_seconds=args.timeout_seconds,
        extra_env=extra_env,
        memory_dir=memory_dir,
        claudecode_project_dir=claudecode_project_dir,
        skills_dir=skills_dir,
        environment_dir=environment_dir,
    )

    run_dir.mkdir(parents=True, exist_ok=True)
    poisoned_memory_setup = setup_poisoned_memory(memory_dir, source_dir)
    (run_dir / "poisoned_memory_setup.json").write_text(
        json.dumps(poisoned_memory_setup, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if poisoned_memory_setup["errors"]:
        print(f"poisoned memory setup errors: {len(poisoned_memory_setup['errors'])}")

    pre_reset = reset_virtual_scenario_environment(config, scenario)
    if pre_reset is not None:
        (run_dir / f"{scenario}_reset_before_batch.json").write_text(
            json.dumps(pre_reset, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    results: list[dict[str, object]] = []
    cleanup: dict[str, object] | None = None
    try:
        for index in range(1, args.sessions + 1):
            session_log_cleanup = cleanup_claudecode_session_logs(claudecode_project_dir)
            if args.benign_count > 0:
                assert benign_task_prompt_template is not None
                result_dict = run_casr_multiturn_session(
                    index=index,
                    total=args.sessions,
                    command=command,
                    prompt_template=prompt_template,
                    benign_task_prompt_template=benign_task_prompt_template,
                    benign_samples=benign_samples,
                    method=args.method,
                    model_name=args.model_name,
                    scenario=scenario,
                    config=config,
                    session_log_cleanup_before=session_log_cleanup,
                )
                results.append(result_dict)
                status = "timeout" if result_dict.get("timed_out") else result_dict.get("returncode")
                print(
                    f"{result_dict['session_id']}: exit={status}, "
                    f"benign_count={args.benign_count}, "
                    f"casr_success={result_dict['casr_success']}, "
                    f"reason={result_dict.get('reason')}, "
                    f"logs_deleted={len(session_log_cleanup['deleted_session_logs'])}, "
                    f"{result_dict['duration_seconds']}s"
                )
                continue

            docs_before = None
            calendar_environment_before = None
            if scenario == "calendar":
                calendar_environment_before = snapshot_virtual_calendar_environment(
                    config,
                    snapshot_name="before_session",
                )
            if scenario == "docs":
                docs_before = snapshot_virtual_docs_shared_content(config)

            claudecode_before_snapshot = snapshot_jsonl_files(claudecode_project_dir)
            session_result = run_one(
                index=index,
                total=args.sessions,
                command=command,
                prompt_template=prompt_template,
                model_name=args.model_name,
                run_dir=run_dir,
                cwd=cwd,
                claudecode_project_dir=claudecode_project_dir,
                claudecode_before_snapshot=claudecode_before_snapshot,
                session_log_cleanup_before=session_log_cleanup,
                timeout_seconds=args.timeout_seconds,
                extra_env=extra_env,
            )

            docs_after = None
            form_after = None
            if scenario == "docs":
                docs_after = snapshot_virtual_docs_shared_content(config)
            if scenario == "form":
                form_after = snapshot_virtual_form_latest_submission(config)

            result_dict = asdict(session_result)
            result_dict.update(
                {
                    "scenario": scenario,
                    "round_2": compact_round_from_session(session_result),
                    "docs_shared_content_before_round_2": docs_before,
                    "docs_shared_content_after_round_2": docs_after,
                    "form_latest_submission_after_round_2": form_after,
                }
            )
            result_dict.update(evaluate_casr_result(scenario=scenario, result=result_dict))

            environment_after_session = environment_snapshot_after_session(
                scenario,
                config,
                calendar_environment_before,
                docs_after,
                form_after,
            )
            scenario_reset = reset_virtual_scenario_environment(config, scenario)
            result_dict["scenario_reset_after_session"] = compact_reset_result(scenario_reset)
            session_dir = run_dir / session_result.session_id
            if environment_after_session is not None:
                (session_dir / f"{scenario}_reset_after_session.json").write_text(
                    json.dumps(environment_after_session, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            prune_session_outputs(session_dir, scenario)

            results.append(result_dict)
            status = "timeout" if session_result.timed_out else session_result.returncode
            print(
                f"{session_result.session_id}: exit={status}, "
                f"casr_success={result_dict['casr_success']}, "
                f"reason={result_dict.get('reason')}, "
                f"logs_deleted={len(session_log_cleanup['deleted_session_logs'])}, "
                f"{session_result.duration_seconds}s"
            )
    finally:
        cleanup = cleanup_poisoned_memory(memory_dir)
        (run_dir / "poisoned_memory_cleanup.json").write_text(
            json.dumps(cleanup, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    results.sort(key=lambda item: int(item["index"]))
    if not args.no_summary_json:
        (run_dir / "summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    metric_summary = build_casr_metric_summary(
        scenario=scenario,
        results=results,
        poisoned_memory_setup=poisoned_memory_setup,
        cleanup=cleanup,
        pre_reset=compact_reset_result(pre_reset),
    )
    (run_dir / "metric_summary.json").write_text(
        json.dumps(metric_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    failed = [
        item
        for item in results
        if item.get("timed_out") or item.get("turn_failed") or item.get("returncode") not in (0,)
    ]
    setup_errors = len(poisoned_memory_setup["errors"])
    cleanup_errors = len(cleanup["errors"]) if cleanup else 0
    reset_errors = int(reset_failed(pre_reset)) + sum(
        int(reset_failed(item.get("scenario_reset_after_session"))) for item in results
    )
    session_log_cleanup_errors = sum(
        len(item.get("session_log_cleanup_before", {}).get("errors", []))
        for item in results
        if isinstance(item.get("session_log_cleanup_before"), dict)
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
    command = prepare_command(requested_command)

    try:
        prompt_template = load_prompt(args)
        benign_task_prompt_template = load_benign_task_prompt(args)
        benign_samples = select_benign_samples(args)
        extra_env = parse_env(args.env)
    except Exception as exc:  # noqa: BLE001
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    if args.benign_count > 0:
        if args.scenario is None:
            print("--benign-count is only supported with --scenario CASR mode", file=sys.stderr)
            return 2
        if command_contains_session_control(command):
            print(
                "--benign-count requires CASR.py to manage Claude session reuse; "
                "remove --session-id/--resume/--continue from --cmd-args",
                file=sys.stderr,
            )
            return 2

    cwd = args.cwd.resolve()
    claudecode_project_dir = get_claudecode_project_dir(cwd)
    memory_dir = args.memory_dir.resolve() if args.memory_dir is not None else get_default_memory_dir(cwd).resolve()
    skills_dir = args.skills_dir.resolve() if args.skills_dir is not None else (ROOT / "skills").resolve()
    environment_dir = (
        args.environment_dir.resolve()
        if args.environment_dir is not None
        else (ROOT / "environment").resolve()
    )
    try:
        configure_virtual_state_env(extra_env, environment_dir, cwd)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    model_dir_name = sanitize_path_component(args.model_name)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / model_dir_name
    if args.scenario is not None:
        run_dir = run_dir / sanitize_path_component(args.scenario)
    run_dir = (run_dir / timestamp).resolve()

    print(f"command: {' '.join(command)}")
    print(f"model_name: {args.model_name} -> {model_dir_name}")
    print(f"sessions: {args.sessions}")
    print(f"cwd: {cwd}")
    print(f"claudecode_project: {claudecode_project_dir}")
    print(f"claudecode_memory: {memory_dir}")
    print(f"skills: {skills_dir}")
    print(f"environment: {environment_dir}")
    for scenario in VALID_SCENARIOS:
        print(f"virtual {scenario} state: {get_virtual_state_path(extra_env, scenario)}")
    print(f"output: {run_dir}")
    if args.scenario:
        print(f"scenario: {args.scenario}")
        if args.benign_count > 0:
            print(f"benign_count: {args.benign_count}")
            print(f"cleansample_dir: {args.cleansample_dir}")
            print(f"method: {args.method}")
        print("casr mode: poisoned memory is injected once, sessions run sequentially, memory is cleaned once at end")

    if args.dry_run:
        if args.scenario:
            source_dir = (ROOT / POISONED_MEMORY_ROOT / args.scenario).resolve()
            print(f"dry-run poisoned memory: {source_dir} -> {memory_dir}")
        for index in range(1, args.sessions + 1):
            print(f"dry-run {index}: cleanup Claude Code session logs under {claudecode_project_dir}")
            if args.scenario and args.benign_count > 0:
                claudecode_session_id = "<generated-uuid>"
                for turn_index, sample in enumerate(benign_samples, start=1):
                    turn_command = command_for_claudecode_turn(
                        command,
                        claudecode_session_id=claudecode_session_id,
                        turn_index=turn_index,
                    )
                    print(
                        f"dry-run {index}: benign_{turn_index} sample={sample.sample_id}: "
                        f"{' '.join(turn_command)}"
                    )
                trigger_command = command_for_claudecode_turn(
                    command,
                    claudecode_session_id=claudecode_session_id,
                    turn_index=args.benign_count + 1,
                )
                print(f"dry-run {index}: trigger round_2: {' '.join(trigger_command)}")
            else:
                print(f"dry-run {index}: {' '.join(command)}")
        return 0

    if args.scenario:
        return run_casr_mode(
            args=args,
            command=command,
            prompt_template=prompt_template,
            benign_task_prompt_template=benign_task_prompt_template,
            benign_samples=benign_samples,
            extra_env=extra_env,
            cwd=cwd,
            claudecode_project_dir=claudecode_project_dir,
            memory_dir=memory_dir,
            skills_dir=skills_dir,
            environment_dir=environment_dir,
            run_dir=run_dir,
        )

    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[SessionResult] = []
    for index in range(1, args.sessions + 1):
        session_log_cleanup = cleanup_claudecode_session_logs(claudecode_project_dir)
        claudecode_before_snapshot = snapshot_jsonl_files(claudecode_project_dir)
        result = run_one(
            index=index,
            total=args.sessions,
            command=command,
            prompt_template=prompt_template,
            model_name=args.model_name,
            run_dir=run_dir,
            cwd=cwd,
            claudecode_project_dir=claudecode_project_dir,
            claudecode_before_snapshot=claudecode_before_snapshot,
            session_log_cleanup_before=session_log_cleanup,
            timeout_seconds=args.timeout_seconds,
            extra_env=extra_env,
        )
        results.append(result)
        status = "timeout" if result.timed_out else result.returncode
        print(
            f"{result.session_id}: exit={status}, "
            f"logs_deleted={len(session_log_cleanup['deleted_session_logs'])}, "
            f"{result.duration_seconds}s"
        )

    results.sort(key=lambda item: item.index)
    if not args.no_summary_json:
        summary_path = run_dir / "summary.json"
        summary_path.write_text(
            json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    failed = [
        item
        for item in results
        if item.timed_out or item.returncode not in (0,)
    ]
    session_log_cleanup_errors = sum(len(item.session_log_cleanup_before.get("errors", [])) for item in results)
    print(
        f"done: {len(results) - len(failed)} ok, {len(failed)} failed, "
        f"session_log_cleanup_errors={session_log_cleanup_errors}"
    )
    return 1 if failed or session_log_cleanup_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())



r"""
python jobscripts/CASR.py \
  --prompt-file prompts/calendar.txt \
  --scenario calendar \
  --model-name deepseek-v4-flash \
  --benign-count 5 \
  --cleansample-dir download/multi-interaction/clean/txt \
  --method txt \
  --sessions 10
"""
