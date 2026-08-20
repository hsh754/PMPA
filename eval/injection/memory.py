from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def cleanup_project_memory(memory_dir: Path) -> dict[str, object]:
    memory_dir = memory_dir.resolve()
    jsonl_dir = memory_dir.parent
    result: dict[str, object] = {
        "memory_dir": str(memory_dir),
        "jsonl_dir": str(jsonl_dir),
        "deleted_memory_files": [],
        "deleted_jsonl_files": [],
        "errors": [],
    }
    errors = result["errors"]

    if memory_dir.name.lower() != "memory":
        errors.append(f"Refusing cleanup because target is not named memory: {memory_dir}")
        return result

    if not memory_dir.exists():
        errors.append(f"Memory directory does not exist: {memory_dir}")
    elif not memory_dir.is_dir():
        errors.append(f"Memory target is not a directory: {memory_dir}")
    else:
        clear_memory_files(memory_dir, result)

    if not jsonl_dir.exists():
        errors.append(f"JSONL parent directory does not exist: {jsonl_dir}")
    elif not jsonl_dir.is_dir():
        errors.append(f"JSONL parent target is not a directory: {jsonl_dir}")
    else:
        delete_jsonl_files(jsonl_dir, result)

    return result


def initialize_project_memory_from_dir(memory_dir: Path, source_dir: Path) -> dict[str, object]:
    memory_dir = memory_dir.resolve()
    source_dir = source_dir.resolve()
    jsonl_dir = memory_dir.parent
    result: dict[str, object] = {
        "memory_dir": str(memory_dir),
        "jsonl_dir": str(jsonl_dir),
        "initial_memory_dir": str(source_dir),
        "deleted_memory_files": [],
        "deleted_jsonl_files": [],
        "copied_memory_files": [],
        "errors": [],
    }
    errors = result["errors"]

    if memory_dir.name.lower() != "memory":
        errors.append(f"Refusing cleanup because target is not named memory: {memory_dir}")
        return result

    if source_dir.exists() and not source_dir.is_dir():
        errors.append(f"Initial memory source is not a directory: {source_dir}")
    elif not source_dir.exists():
        errors.append(f"Initial memory source does not exist: {source_dir}")

    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 - keep evaluation moving while recording setup failures.
        errors.append(f"{memory_dir}: {exc}")

    if not memory_dir.exists():
        errors.append(f"Memory directory does not exist: {memory_dir}")
    elif not memory_dir.is_dir():
        errors.append(f"Memory target is not a directory: {memory_dir}")
    else:
        clear_memory_files(memory_dir, result)

    if not jsonl_dir.exists():
        errors.append(f"JSONL parent directory does not exist: {jsonl_dir}")
    elif not jsonl_dir.is_dir():
        errors.append(f"JSONL parent target is not a directory: {jsonl_dir}")
    else:
        delete_jsonl_files(jsonl_dir, result)

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
        except Exception as exc:  # noqa: BLE001 - setup is best effort and should be recorded.
            errors.append(f"{source_path} -> {target_path}: {exc}")

    return result


def clear_memory_files(memory_dir: Path, result: dict[str, object]) -> None:
    memory_index_path = memory_dir / "MEMORY.md"
    memory_index_resolved = memory_index_path.resolve()
    errors = result["errors"]

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
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
            errors.append(f"{path}: {exc}")

    try:
        memory_index_path.write_text("", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - cleanup is best effort.
        errors.append(f"{memory_index_path}: {exc}")


def delete_jsonl_files(jsonl_dir: Path, result: dict[str, object]) -> None:
    errors = result["errors"]
    for path in sorted(jsonl_dir.glob("*.jsonl")):
        if not path.is_file():
            continue
        try:
            path.unlink()
            result["deleted_jsonl_files"].append(str(path))
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


def snapshot_memory(memory_dir: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    memory_dir = memory_dir.resolve()
    if not memory_dir.exists():
        return {}, [f"Memory directory does not exist: {memory_dir}"]
    if not memory_dir.is_dir():
        return {}, [f"Memory target is not a directory: {memory_dir}"]

    snapshot: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    for path in sorted(memory_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            snapshot[path.relative_to(memory_dir).as_posix()] = {
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


def diff_memory(
    memory_dir: Path,
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
        "memory_dir": str(memory_dir.resolve()),
        "created_files": created_files,
        "modified_files": modified_files,
        "errors": snapshot_errors,
    }
