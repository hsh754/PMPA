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
    openclaw_state_dir: Path
    openclaw_workspace_dir: Path
    openclaw_sessions_dir: Path
    openclaw_skills_dir: Path
    openclaw_environment_dir: Path
    openclaw_agent_id: str


@dataclass
class EvaluationSample:
    index: int
    sample_id: str
    path: Path


@dataclass
class PromptRunResult:
    round_name: str
    session_id: str
    openclaw_session_id: str
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
    openclaw_session_path: str | None
    openclaw_session_copy_path: str | None
    openclaw_session_match: str | None
    error: str | None = None
