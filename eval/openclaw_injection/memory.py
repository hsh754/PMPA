from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


TEMP_CLEANUP_DIRS = (
    Path(".openclaw") / "tmp",
    Path("tmp"),
    Path("scratch"),
)
TEMP_CLEANUP_FILE_GLOBS = ("*.png",)


def cleanup_project_memory(workspace_dir: Path) -> dict[str, object]:
    workspace_dir = workspace_dir.resolve()
    result: dict[str, object] = {
        "openclaw_workspace_dir": str(workspace_dir),
        "memory_index_path": str(workspace_dir / "MEMORY.md"),
        "tools_path": str(workspace_dir / "TOOLS.md"),
        "memory_dir": str(workspace_dir / "memory"),
        "deleted_memory_files": [],
        "errors": [],
    }
    errors = result["errors"]

    if not workspace_dir.exists():
        errors.append(f"OpenClaw workspace does not exist: {workspace_dir}")
        return result
    if not workspace_dir.is_dir():
        errors.append(f"OpenClaw workspace target is not a directory: {workspace_dir}")
        return result

    clear_openclaw_memory(workspace_dir, result)
    return result


def cleanup_workspace_temp_files(workspace_dir: Path) -> dict[str, object]:
    workspace_dir = workspace_dir.resolve()
    result: dict[str, object] = {
        "openclaw_workspace_dir": str(workspace_dir),
        "cleanup_dirs": [path.as_posix() for path in TEMP_CLEANUP_DIRS],
        "cleanup_file_globs": list(TEMP_CLEANUP_FILE_GLOBS),
        "deleted_paths": [],
        "errors": [],
    }
    errors = result["errors"]

    if not workspace_dir.exists():
        errors.append(f"OpenClaw workspace does not exist: {workspace_dir}")
        return result
    if not workspace_dir.is_dir():
        errors.append(f"OpenClaw workspace target is not a directory: {workspace_dir}")
        return result

    for relative_dir in TEMP_CLEANUP_DIRS:
        cleanup_dir = workspace_dir / relative_dir
        try:
            cleanup_dir_resolved = cleanup_dir.resolve()
            cleanup_dir_resolved.relative_to(workspace_dir)
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
            errors.append(f"{cleanup_dir}: invalid cleanup target: {exc}")
            continue

        if not cleanup_dir.exists():
            continue
        if not cleanup_dir.is_dir():
            errors.append(f"{cleanup_dir}: cleanup target is not a directory")
            continue

        for child in sorted(cleanup_dir.iterdir()):
            delete_temp_path(child, workspace_dir, result)

    for pattern in TEMP_CLEANUP_FILE_GLOBS:
        for path in sorted(workspace_dir.glob(pattern)):
            delete_temp_path(path, workspace_dir, result)

    return result


def delete_temp_path(path: Path, workspace_dir: Path, result: dict[str, object]) -> None:
    errors = result["errors"]
    try:
        resolved_path = path.resolve()
        resolved_path.relative_to(workspace_dir)
    except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
        errors.append(f"{path}: invalid cleanup target: {exc}")
        return

    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return
        result["deleted_paths"].append(str(path))
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
        errors.append(f"{path}: {exc}")


def initialize_project_memory_from_dir(workspace_dir: Path, source_dir: Path) -> dict[str, object]:
    workspace_dir = workspace_dir.resolve()
    source_dir = source_dir.resolve()
    result: dict[str, object] = {
        "openclaw_workspace_dir": str(workspace_dir),
        "memory_index_path": str(workspace_dir / "MEMORY.md"),
        "tools_path": str(workspace_dir / "TOOLS.md"),
        "memory_dir": str(workspace_dir / "memory"),
        "initial_memory_dir": str(source_dir),
        "deleted_memory_files": [],
        "copied_memory_files": [],
        "errors": [],
    }
    errors = result["errors"]

    if source_dir.exists() and not source_dir.is_dir():
        errors.append(f"Initial memory source is not a directory: {source_dir}")
    elif not source_dir.exists():
        errors.append(f"Initial memory source does not exist: {source_dir}")

    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 - keep evaluation moving while recording setup failures.
        errors.append(f"{workspace_dir}: {exc}")

    if not workspace_dir.exists():
        errors.append(f"OpenClaw workspace does not exist: {workspace_dir}")
    elif not workspace_dir.is_dir():
        errors.append(f"OpenClaw workspace target is not a directory: {workspace_dir}")
    else:
        clear_openclaw_memory(workspace_dir, result)

    if not source_dir.is_dir() or not workspace_dir.is_dir():
        return result

    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file():
            continue
        relative_path = source_path.relative_to(source_dir)
        target_path = workspace_memory_target(workspace_dir, relative_path)
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            result["copied_memory_files"].append(str(target_path))
        except Exception as exc:  # noqa: BLE001 - setup is best effort and should be recorded.
            errors.append(f"{source_path} -> {target_path}: {exc}")

    return result


def workspace_memory_target(workspace_dir: Path, relative_path: Path) -> Path:
    if relative_path.as_posix().lower() == "memory.md":
        return workspace_dir / "MEMORY.md"
    return workspace_dir / "memory" / relative_path


def clear_openclaw_memory(workspace_dir: Path, result: dict[str, object]) -> None:
    errors = result["errors"]
    memory_index_path = workspace_dir / "MEMORY.md"
    tools_path = workspace_dir / "TOOLS.md"
    memory_dir = workspace_dir / "memory"

    for path in (memory_index_path, tools_path):
        try:
            path.write_text("", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
            errors.append(f"{path}: {exc}")

    if not memory_dir.exists():
        try:
            memory_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
            errors.append(f"{memory_dir}: {exc}")
        return
    if not memory_dir.is_dir():
        errors.append(f"OpenClaw memory target is not a directory: {memory_dir}")
        return

    for path in sorted(memory_dir.rglob("*")):
        if not path.is_file():
            continue
        if ".dreams" in path.relative_to(memory_dir).parts:
            continue
        try:
            path.unlink()
            result["deleted_memory_files"].append(str(path))
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
            errors.append(f"{path}: {exc}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_memory(workspace_dir: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    workspace_dir = workspace_dir.resolve()
    if not workspace_dir.exists():
        return {}, [f"OpenClaw workspace does not exist: {workspace_dir}"]
    if not workspace_dir.is_dir():
        return {}, [f"OpenClaw workspace target is not a directory: {workspace_dir}"]

    snapshot: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    for path in memory_snapshot_paths(workspace_dir):
        try:
            stat = path.stat()
            snapshot[path.relative_to(workspace_dir).as_posix()] = {
                "path": str(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": file_sha256(path),
            }
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - snapshot is best effort.
            errors.append(f"{path}: {exc}")
    return snapshot, errors


def memory_snapshot_paths(workspace_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in (workspace_dir / "MEMORY.md", workspace_dir / "TOOLS.md"):
        if path.is_file():
            paths.append(path)

    memory_dir = workspace_dir / "memory"
    if memory_dir.is_dir():
        for path in sorted(memory_dir.rglob("*")):
            if not path.is_file():
                continue
            if ".dreams" in path.relative_to(memory_dir).parts:
                continue
            paths.append(path)
    return paths


def diff_memory(
    workspace_dir: Path,
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
    snapshot_errors: list[str],
) -> dict[str, object]:
    created_files: list[str] = []
    modified_files: list[str] = []
    for relative_path, after_info in sorted(after.items()):
        before_info = before.get(relative_path)
        if before_info is None:
            created_files.append(str(after_info["path"]))
        elif before_info.get("sha256") != after_info.get("sha256"):
            modified_files.append(str(after_info["path"]))

    return {
        "openclaw_workspace_dir": str(workspace_dir.resolve()),
        "created_files": created_files,
        "modified_files": modified_files,
        "errors": snapshot_errors,
    }
