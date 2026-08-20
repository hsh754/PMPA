from __future__ import annotations
from pathlib import Path

try:
    from .models import EvaluationSample
    from .utils import render_prompt, safe_path_component
except ImportError:  # pragma: no cover - supports direct script execution.
    from models import EvaluationSample
    from utils import render_prompt, safe_path_component


PDF_SUFFIXES = {".pdf"}
PNG_SUFFIXES = {".png"}
TXT_SUFFIXES = {".txt"}


def load_samples(samples_path: Path, method: str) -> list[EvaluationSample]:
    paths = list_sample_paths(samples_path, method)
    return [
        EvaluationSample(
            index=index,
            sample_id=build_sample_id(path, samples_path, index),
            path=path.resolve(),
        )
        for index, path in enumerate(paths, start=1)
    ]


def list_sample_paths(samples_path: Path, method: str) -> list[Path]:
    samples_path = samples_path.resolve()
    if samples_path.is_file():
        return [samples_path]
    if not samples_path.exists():
        raise FileNotFoundError(f"Samples path does not exist: {samples_path}")
    if not samples_path.is_dir():
        raise ValueError(f"Samples path is not a file or directory: {samples_path}")

    paths = [path for path in samples_path.rglob("*") if path.is_file()]
    if method == "pdf":
        paths = [path for path in paths if path.suffix.lower() in PDF_SUFFIXES]
    elif method == "png":
        paths = [path for path in paths if path.suffix.lower() in PNG_SUFFIXES]
    elif method == "txt":
        paths = [path for path in paths if path.suffix.lower() in TXT_SUFFIXES]
    else:
        raise ValueError(f"Unsupported method: {method}")

    return sorted(paths, key=sample_sort_key)


def sample_sort_key(path: Path) -> tuple[int, int | str, str]:
    stem = path.stem
    if stem.isdigit():
        return (0, int(stem), path.as_posix().lower())
    return (1, stem.lower(), path.as_posix().lower())


def build_sample_id(path: Path, root: Path, index: int) -> str:
    if root.resolve().is_file():
        relative = path.name
    else:
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError:
            relative = path.name
    safe_name = safe_path_component(str(relative).replace("\\", "_").replace("/", "_"))
    return f"sample_{index:06d}_{safe_name}"


def build_sample_prompt(
    *,
    base_prompt_template: str,
    sample: EvaluationSample,
    method: str,
    run_index: int,
    run_count: int,
) -> str:
    base_prompt = render_prompt(base_prompt_template, run_index, run_count)
    if method == "txt":
        sample_payload = sample.path.read_text(encoding="utf-8-sig", errors="replace")
    elif method in {"png", "pdf"}:
        sample_payload = str(sample.path)
    else:
        raise ValueError(f"Unsupported method: {method}")
    return f"{base_prompt}\n\n{sample_payload}"
