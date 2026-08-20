from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

try:
    from .models import PromptRunResult, RunConfig
    from .utils import prepare_openclaw_command, safe_path_component, utc_now, write_json
except ImportError:  # pragma: no cover - supports direct script execution.
    from models import PromptRunResult, RunConfig
    from utils import prepare_openclaw_command, safe_path_component, utc_now, write_json


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


def file_contains_text(path: Path, text: str) -> bool:
    try:
        return text in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def build_openclaw_session_id(session_id: str) -> str:
    return safe_path_component(session_id)


def reset_openclaw_session_state(sessions_dir: Path, openclaw_session_id: str) -> None:
    if not sessions_dir.is_dir():
        return

    for name in (
        f"{openclaw_session_id}.jsonl",
        f"{openclaw_session_id}.trajectory.jsonl",
        f"{openclaw_session_id}.trajectory-path.json",
    ):
        path = sessions_dir / name
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

    index_path = sessions_dir / "sessions.json"
    if not index_path.is_file():
        return

    try:
        session_index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(session_index, dict):
        return

    filtered_index = {}
    removed = False
    for key, value in session_index.items():
        if isinstance(value, dict) and value.get("sessionId") == openclaw_session_id:
            removed = True
            continue
        filtered_index[key] = value

    if not removed:
        return
    try:
        index_path.write_text(
            json.dumps(filtered_index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def build_openclaw_round_command(config: RunConfig, openclaw_session_id: str, prompt: str) -> list[str]:
    command = list(config.command)
    if "--session-id" not in command:
        command.extend(["--session-id", openclaw_session_id])
    if "--message" not in command and "-m" not in command:
        command.extend(["--message", prompt])
    return command


def extract_openclaw_payload_text(stdout: str) -> str:
    """Return rendered assistant text from `openclaw agent --json` stdout when possible."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if not isinstance(payload, dict):
        return stdout

    payloads = payload.get("payloads")
    if not isinstance(payloads, list):
        return stdout

    texts = []
    for item in payloads:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return "\n\n".join(texts) if texts else stdout


def copy_round_openclaw_session_log(
    *,
    round_name: str,
    session_dir: Path,
    sessions_dir: Path,
    openclaw_session_id: str,
    before_snapshot: dict[Path, tuple[int, int]],
    prompt: str,
) -> tuple[str | None, str | None, str | None]:
    expected_path = sessions_dir / f"{openclaw_session_id}.jsonl"
    if expected_path.is_file():
        source_path = expected_path
        match_status = "explicit_session_id"
    else:
        after_snapshot = snapshot_jsonl_files(sessions_dir)
        changed_paths = [
            path
            for path, state in after_snapshot.items()
            if before_snapshot.get(path) != state
        ]
        matching_paths = [path for path in changed_paths if file_contains_text(path, prompt)]

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

    copy_path = session_dir / f"openclaw-session_{round_name}.jsonl"
    try:
        shutil.copy2(source_path, copy_path)
    except OSError:
        return str(source_path), None, "copy_failed"
    return str(source_path), str(copy_path), match_status


def run_prompt_round(
    *,
    round_name: str,
    session_id: str,
    session_dir: Path,
    prompt: str,
    config: RunConfig,
) -> PromptRunResult:
    prompt_path = session_dir / f"prompt_{round_name}.txt"
    stdout_path = session_dir / f"stdout_{round_name}.txt"
    stderr_path = session_dir / f"stderr_{round_name}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    openclaw_session_id = build_openclaw_session_id(session_id)
    if round_name == "1":
        reset_openclaw_session_state(config.openclaw_sessions_dir, openclaw_session_id)
    raw_command = build_openclaw_round_command(config, openclaw_session_id, prompt)
    command = prepare_openclaw_command(raw_command)
    openclaw_before_snapshot = snapshot_jsonl_files(config.openclaw_sessions_dir)

    env = os.environ.copy()
    env.update(config.extra_env)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    stdout = stderr = ""
    returncode: int | None = None
    timed_out = False
    error: str | None = None

    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(config.cwd),
            env=env,
            timeout=config.timeout_seconds,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        error = f"Timed out after {config.timeout_seconds} seconds."
    except FileNotFoundError as exc:
        stderr = error = str(exc)
    except Exception as exc:  # noqa: BLE001 - preserve unexpected runner errors.
        stderr = error = repr(exc)

    result = PromptRunResult(
        round_name=round_name,
        session_id=session_id,
        openclaw_session_id=openclaw_session_id,
        command=command,
        cwd=str(config.cwd),
        started_at=started_at,
        ended_at=utc_now(),
        duration_seconds=round(time.monotonic() - started_monotonic, 3),
        returncode=returncode,
        timed_out=timed_out,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        prompt_path=str(prompt_path),
        openclaw_session_path=None,
        openclaw_session_copy_path=None,
        openclaw_session_match=None,
        error=error,
    )
    stdout_path.write_text(extract_openclaw_payload_text(stdout), encoding="utf-8", errors="replace")
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    (
        result.openclaw_session_path,
        result.openclaw_session_copy_path,
        result.openclaw_session_match,
    ) = copy_round_openclaw_session_log(
        round_name=round_name,
        session_dir=session_dir,
        sessions_dir=config.openclaw_sessions_dir,
        openclaw_session_id=openclaw_session_id,
        before_snapshot=openclaw_before_snapshot,
        prompt=prompt,
    )
    write_json(session_dir / f"meta_{round_name}.json", asdict(result))
    return result


def prune_session_outputs(session_dir: Path) -> None:
    keep = {
        "stdout_1.txt",
        "stdout_2.txt",
        "openclaw-session_1.jsonl",
        "openclaw-session_2.jsonl",
        "email_environment_after_round_2.json",
        "docs_environment_after_round_2.json",
        "calendar_environment_after_round_2.json",
        "form_environment_after_round_2.json",
    }
    for path in sorted(session_dir.rglob("*"), reverse=True):
        if path.is_file() and path.name not in keep:
            try:
                path.unlink()
            except (FileNotFoundError, PermissionError):
                pass
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
