from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

try:
    from .constants import VALID_METHODS
    from .models import EvaluationSample, RunConfig
    from .samples import load_samples
    from .utils import (
        configure_virtual_state_env,
        get_claudecode_project_dir,
        get_default_memory_dir,
        get_virtual_state_path,
        parse_env,
        prepare_command,
        safe_path_component,
        split_command,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from constants import VALID_METHODS
    from models import EvaluationSample, RunConfig
    from samples import load_samples
    from utils import (
        configure_virtual_state_env,
        get_claudecode_project_dir,
        get_default_memory_dir,
        get_virtual_state_path,
        parse_env,
        prepare_command,
        safe_path_component,
        split_command,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Batch-run Claude Code interactions, then clear the "
            "configured Claude project memory after every finished session."
        )
    )
    prompts = parser.add_mutually_exclusive_group(required=True)
    prompts.add_argument("--prompt-file", type=Path, help="Text file to send.")
    prompts.add_argument("--prompt", help="Prompt text to send.")

    benign_task_prompts = parser.add_mutually_exclusive_group()
    benign_task_prompts.add_argument(
        "--benign-task-prompt-file",
        "--inserted-prompt-file",
        "--trigger-prompt-file",
        dest="benign_task_prompt_file",
        type=Path,
        help="First-round benign task prompt used with each inserted sample.",
    )
    benign_task_prompts.add_argument(
        "--benign-task-prompt",
        "--inserted-prompt",
        "--trigger-prompt",
        dest="benign_task_prompt",
        help="First-round benign task prompt text used with each inserted sample.",
    )

    parser.add_argument("-n", "--sessions", type=int, default=1)
    parser.add_argument(
        "--samples-path",
        type=Path,
        required=True,
        help="File or directory containing samples to evaluate.",
    )
    parser.add_argument("--cmd", default="claude")
    parser.add_argument("--cmd-args", default="--print")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model-name", default="unknown_model")
    parser.add_argument("--method", choices=sorted(VALID_METHODS), default="txt")
    parser.add_argument(
        "--scenario",
        choices=("email", "docs", "form", "calendar"),
        default=None,
        help=(
            "Scenario-specific metric evaluator. Email computes S-ASR from round2 CC sends; "
            "docs computes S-ASR from round2 shared document content changes; calendar computes "
            "S-ASR from round2 event creates with nonempty attendees."
        ),
    )
    parser.add_argument(
        "--utility-clean-run-dir",
        type=Path,
        default=None,
        help=(
            "Deprecated compatibility option. Utility is now computed from answer "
            "keywords in round1 stdout and does not use a clean baseline directory."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("runs") / "ClaudecodeIsr")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
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
        "--memory-dir",
        type=Path,
        default=None,
        help=(
            "Claude project memory directory. Defaults to the memory directory "
            "derived from --cwd under CLAUDE_CONFIG_DIR or ~/.claude/projects."
        ),
    )
    parser.add_argument("--snapshot-delay-seconds", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-summary-json", action="store_true")
    return parser.parse_args()


def load_prompt(args: argparse.Namespace) -> str:
    return args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.prompt


def load_benign_task_prompt(args: argparse.Namespace) -> str | None:
    if args.benign_task_prompt_file:
        return args.benign_task_prompt_file.read_text(encoding="utf-8")
    return args.benign_task_prompt


def validate_args(args: argparse.Namespace) -> int | None:
    if args.sessions < 1:
        print("--sessions must be >= 1")
        return 2
    return None


def build_config(
    args: argparse.Namespace,
) -> tuple[RunConfig, str, str | None, list[EvaluationSample]] | None:
    requested_command = split_command(args.cmd) + split_command(args.cmd_args)
    if not requested_command:
        print("Command cannot be empty.", file=sys.stderr)
        return None

    try:
        prompt_template = load_prompt(args)
        benign_task_prompt_template = load_benign_task_prompt(args)
        extra_env = parse_env(args.env)
        samples = load_samples(args.samples_path, args.method)
        if not samples:
            raise ValueError(f"No samples found under {args.samples_path} for method {args.method}")
    except Exception as exc:  # noqa: BLE001
        print(f"Input error: {exc}", file=sys.stderr)
        return None

    cwd = args.cwd.resolve()
    project_root = Path(__file__).resolve().parents[2]
    skills_dir = (
        args.skills_dir.resolve()
        if args.skills_dir is not None
        else (project_root / "skills").resolve()
    )
    environment_dir = (
        args.environment_dir.resolve()
        if args.environment_dir is not None
        else (project_root / "environment").resolve()
    )
    try:
        configure_virtual_state_env(extra_env, environment_dir, cwd)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return None
    memory_dir = (
        args.memory_dir.resolve()
        if args.memory_dir is not None
        else get_default_memory_dir(cwd).resolve()
    )
    run_dir = args.output_dir / safe_path_component(args.model_name) / args.method
    if args.scenario is not None:
        run_dir = run_dir / safe_path_component(args.scenario)
    run_dir = (run_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    return (
        RunConfig(
            command=prepare_command(requested_command),
            run_dir=run_dir,
            cwd=cwd,
            timeout_seconds=args.timeout_seconds,
            extra_env=extra_env,
            memory_dir=memory_dir,
            claudecode_project_dir=get_claudecode_project_dir(cwd).resolve(),
            skills_dir=skills_dir,
            environment_dir=environment_dir,
        ),
        prompt_template,
        benign_task_prompt_template,
        samples,
    )


def print_run_header(
    args: argparse.Namespace,
    config: RunConfig,
    benign_task_prompt_template: str | None,
    samples: list[EvaluationSample],
) -> None:
    print(f"command: {' '.join(config.command)}")
    print(f"sessions: {args.sessions}")
    print(f"samples_path: {args.samples_path.resolve()}")
    print(f"samples: {len(samples)}")
    print(f"model_name: {args.model_name}")
    print(f"method: {args.method}")
    print(f"cwd: {config.cwd}")
    print(f"output: {config.run_dir}")
    print(f"cleanup memory: {config.memory_dir}")
    print(f"cleanup jsonl: {config.memory_dir.parent / '*.jsonl'}")
    print(f"skills: {config.skills_dir}")
    print(f"environment: {config.environment_dir}")
    for scenario in ("email", "calendar", "docs", "form"):
        print(f"virtual {scenario} state: {get_virtual_state_path(config.extra_env, scenario)}")
    if args.scenario:
        print(f"scenario: {args.scenario}")
    if args.utility_clean_run_dir:
        print(f"utility clean run: {args.utility_clean_run_dir.resolve()}")
    if benign_task_prompt_template is not None:
        print("isr: enabled")
        print(f"snapshot delay: {args.snapshot_delay_seconds}s")


def run_dry_run(
    args: argparse.Namespace,
    config: RunConfig,
    samples: list[EvaluationSample],
    benign_task_enabled: bool,
) -> int:
    for sample in samples:
        for repeat_index in range(1, args.sessions + 1):
            session_id = f"sample_{sample.index}_run_{repeat_index}"
            print(f"dry-run {session_id}: {sample.path} -> {' '.join(config.command)}")
    print("dry-run cleanup: no files will be deleted")
    if benign_task_enabled:
        print("dry-run isr: round1 uses the benign task prompt with each sample")
        print("dry-run isr: round2 uses --prompt only if memory files are created or modified")
    return 0
