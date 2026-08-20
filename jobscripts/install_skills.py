#!/usr/bin/env python3
"""Install PMPA skills into Claude Code and/or OpenClaw workspaces."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "skills"
IGNORED_NAMES = ("__pycache__", "*.pyc", "*.pyo", "*.bak", "*.bak-*")


def default_openclaw_state_dir() -> Path:
    configured = os.environ.get("OPENCLAW_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path("D:/openclaw")
    return Path.home() / ".openclaw"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the repository's canonical skills into local agent workspaces."
    )
    parser.add_argument("--target", choices=("claude", "openclaw", "both"), required=True)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--skill", action="append", default=[], help="Install only this skill; repeatable.")
    parser.add_argument("--claude-workspace", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--openclaw-state-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Replace existing installed copies.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def discover_skills(source_dir: Path, selected: list[str]) -> list[Path]:
    if not source_dir.is_dir():
        raise ValueError(f"Skill source directory does not exist: {source_dir}")
    available = {
        path.name: path
        for path in source_dir.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    if not available:
        raise ValueError(f"No skill directories containing SKILL.md found under: {source_dir}")
    requested = selected or sorted(available)
    missing = sorted(set(requested) - set(available))
    if missing:
        raise ValueError(f"Unknown skills: {', '.join(missing)}")
    return [available[name] for name in requested]


def install_one(source: Path, target_root: Path, force: bool, dry_run: bool) -> None:
    source = source.resolve()
    target_root = target_root.resolve()
    destination = (target_root / source.name).resolve()
    if destination.parent != target_root:
        raise ValueError(f"Unsafe skill destination: {destination}")
    if destination == source:
        raise ValueError(f"Source and destination are identical: {source}")

    action = "replace" if destination.exists() else "install"
    prefix = "dry-run: " if dry_run else ""
    print(f"{prefix}{action} {source.name}: {source} -> {destination}")
    if dry_run:
        return
    if destination.exists():
        if not force:
            raise FileExistsError(
                f"Skill already installed: {destination}. Re-run with --force to replace it."
            )
        shutil.rmtree(destination)
    target_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*IGNORED_NAMES))


def write_manifest(target_root: Path, skills: list[Path], dry_run: bool) -> None:
    manifest_path = target_root.resolve() / ".pmpa-skills.json"
    print(f"{'dry-run: ' if dry_run else ''}manifest: {manifest_path}")
    if dry_run:
        return
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "source": str(skills[0].parent.resolve()),
                "skills": [skill.name for skill in skills],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    try:
        source_dir = args.source_dir.expanduser().resolve()
        skills = discover_skills(source_dir, args.skill)
        targets: list[tuple[str, Path]] = []
        if args.target in {"claude", "both"}:
            targets.append(
                ("Claude Code", args.claude_workspace.expanduser().resolve() / ".claude" / "skills")
            )
        if args.target in {"openclaw", "both"}:
            state_dir = (
                args.openclaw_state_dir.expanduser()
                if args.openclaw_state_dir is not None
                else default_openclaw_state_dir()
            ).resolve()
            targets.append(("OpenClaw", state_dir / "workspace" / "skills"))

        for label, target_root in targets:
            print(f"target {label}: {target_root}")
            for skill in skills:
                install_one(skill, target_root, args.force, args.dry_run)
            write_manifest(target_root, skills, args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"install error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
