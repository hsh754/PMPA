from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunConfig:
    command: list[str]
    run_dir: Path
    cwd: Path
    timeout_seconds: float
    extra_env: dict[str, str]
    memory_dir: Path
    claudecode_project_dir: Path
    skills_dir: Path
    environment_dir: Path


@dataclass
class EvaluationSample:
    index: int
    sample_id: str
    path: Path


@dataclass
class PromptRunResult:
    round_name: str
    session_id: str
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
