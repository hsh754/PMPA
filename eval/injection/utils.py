from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


VIRTUAL_STATE_FILES = {
    "email": ("VIRTUAL_EMAIL_STATE", Path("virtual-email") / "mailbox.json"),
    "calendar": ("VIRTUAL_CALENDAR_STATE", Path("virtual-calendar") / "calendar.json"),
    "docs": ("VIRTUAL_DOCS_STATE", Path("virtual-docs") / "docs.json"),
    "form": ("VIRTUAL_FORM_STATE", Path("virtual-form") / "forms.json"),
}


def split_command(value: str) -> list[str]:
    return [] if not value.strip() else shlex.split(value, posix=(os.name != "nt"))


def prepare_command(command: list[str]) -> list[str]:
    if not command:
        return command

    executable = shutil.which(command[0]) or command[0]
    prepared = [executable] + command[1:]
    if os.name == "nt" and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline(prepared)]
    return prepared


def render_prompt(template: str, index: int, total: int) -> str:
    return template.format(
        session_id=f"session_{index:03d}",
        session_index=index,
        session_count=total,
    )


def parse_env(pairs: Iterable[str]) -> dict[str, str]:
    parsed = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--env must be KEY=VALUE, got: {pair!r}")
        key, value = pair.split("=", 1)
        if not key:
            raise ValueError(f"--env key cannot be empty: {pair!r}")
        parsed[key] = value
    return parsed


def configure_virtual_state_env(
    extra_env: dict[str, str],
    environment_dir: Path,
    cwd: Path,
) -> None:
    for env_key, relative_path in VIRTUAL_STATE_FILES.values():
        configured = extra_env.get(env_key)
        if configured is None:
            configured = os.environ.get(env_key)
        if configured is not None:
            if not configured.strip():
                raise ValueError(f"{env_key} cannot be empty")
            state_path = Path(configured).expanduser()
            if not state_path.is_absolute():
                state_path = cwd / state_path
        else:
            state_path = environment_dir / relative_path
        extra_env[env_key] = str(state_path.resolve())


def get_virtual_state_path(extra_env: dict[str, str], scenario: str) -> Path:
    try:
        env_key, _ = VIRTUAL_STATE_FILES[scenario]
    except KeyError as exc:
        raise ValueError(f"Unknown virtual scenario: {scenario}") from exc
    configured = extra_env.get(env_key)
    if not configured:
        raise ValueError(f"Missing configured virtual state path: {env_key}")
    return Path(configured)


def safe_path_component(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in value.strip()
    )
    return cleaned.strip("._") or "unknown"


def sanitize_claudecode_project_path(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value)


def get_claudecode_project_dir(cwd: Path) -> Path:
    config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    return config_home / "projects" / sanitize_claudecode_project_path(str(cwd))


def get_default_memory_dir(cwd: Path) -> Path:
    return get_claudecode_project_dir(cwd) / "memory"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def hide_deleted_paths(result: dict[str, object]) -> dict[str, object]:
    result["prompt_path"] = ""
    result["stderr_path"] = ""
    return result


def round_failed(result: dict[str, object] | None) -> bool:
    return bool(result and (result["timed_out"] or result["returncode"] not in (0,)))


def status_text(result: dict[str, object]) -> str:
    return "timeout" if result["timed_out"] else str(result["returncode"])
