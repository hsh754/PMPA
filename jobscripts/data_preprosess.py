import argparse
import csv
import json
import os
import random
import re
import site
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import fitz


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTRUCTION_PATH = "data/insert_instruction.txt"
SCENARIO_INSTRUCTION_PATHS = {
    "email": "data/scenario/email_instruction.txt",
    "calendar": "data/scenario/calendar_instruction.txt",
    "docs": "data/scenario/docs_instruction.txt",
    "form": "data/scenario/form_instruction.txt",
}
DATASET_NAMES = ("mmlu", "commonsense_qa", "ai2_arc", "socialiqa")
DEFAULT_TOTAL_COUNT = 30
OPTION_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
INSTRUCTION_POSITIONS = ("top", "medium", "buttom")


@dataclass(frozen=True)
class QAItem:
    dataset: str
    source_id: str
    source_split: str
    subject: str
    question: str
    options: dict[str, str]
    answer: str | None


def labels_to_options(values, labels=None):
    if labels is None:
        labels = OPTION_LABELS[: len(values)]
    return {str(label).upper(): str(value) for label, value in zip(labels, values)}


def instruction_parts(instruction_text, expected_count):
    if not instruction_text.strip():
        return []
    parts = [part.strip() for part in re.split(r"\n\s*\n", instruction_text.strip()) if part.strip()]
    if len(parts) == 1:
        return parts
    if len(parts) != expected_count:
        raise ValueError(
            f"Expected either 1 instruction block or {expected_count} instruction blocks separated by blank lines, "
            f"got {len(parts)}."
        )
    return parts


def embed_instruction_in_question(text, instruction, position):
    text = text.strip()
    instruction = instruction.strip() if instruction else ""
    if not text or not instruction:
        return text
    if position == "top":
        return f"{instruction}\n{text}"
    if position == "medium":
        return f"{text}\n{instruction}"
    if position == "buttom":
        return text
    raise ValueError(f"Unknown instruction position: {position}")


def configure_runtime(cache_dir, dll_dirs):
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir.parent))

    if os.name == "nt":
        for dll_dir in dll_dirs:
            if dll_dir and dll_dir.exists():
                os.add_dll_directory(str(dll_dir))


def make_rng(seed, namespace):
    if seed is None:
        return random.Random()
    return random.Random(f"{seed}:{namespace}")


def resolve_instruction_path(root, scenario, instruction_path):
    if instruction_path is None:
        instruction_path = SCENARIO_INSTRUCTION_PATHS.get(scenario, DEFAULT_INSTRUCTION_PATH)

    if not str(instruction_path).strip():
        return None

    path = Path(instruction_path)
    if not path.is_absolute():
        path = root / path
    return path


def parse_dataset_names(value):
    if value == "all":
        return list(DATASET_NAMES)

    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = sorted(set(names) - set(DATASET_NAMES))
    if unknown:
        raise ValueError(f"Unknown dataset(s): {', '.join(unknown)}. Valid values: {', '.join(DATASET_NAMES)}")
    if not names:
        raise ValueError("At least one dataset must be selected.")
    return names


def dataset_root(root, dataset_name):
    paths = {
        "mmlu": root / "data" / "cais_mmlu_csv",
        "commonsense_qa": root / "data" / "commonsense_qa",
        "ai2_arc": root / "data" / "ai2_arc",
        "socialiqa": root / "data" / "socialQa",
    }
    return paths[dataset_name]


def mmlu_answer_to_label(answer, option_count):
    answer = str(answer).strip()
    if answer.isdigit():
        index = int(answer)
        if 0 <= index < option_count:
            return OPTION_LABELS[index]
    upper = answer.upper()
    if upper in OPTION_LABELS[:option_count]:
        return upper
    return answer or None


def choices_struct_to_options(choices):
    labels = choices.get("label") or OPTION_LABELS[: len(choices.get("text", []))]
    texts = choices.get("text") or []
    return labels_to_options(texts, labels)


def read_parquet_rows(paths):
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        fallback_sites = [
            site.getusersitepackages(),
            str(Path(sys.base_prefix) / "Lib" / "site-packages"),
        ]
        for fallback_site in fallback_sites:
            if fallback_site and fallback_site not in sys.path:
                sys.path.append(fallback_site)
        import pyarrow.parquet as pq

    rows = []
    for path in paths:
        rows.extend(pq.read_table(path).to_pylist())
    return rows


def resolve_hf_split(split):
    if split == "dev":
        return "validation"
    return split


def resolve_socialiqa_split(split):
    return {
        "train": "trn",
        "dev": "dev",
        "validation": "dev",
        "test": "tst",
    }[split]


def load_mmlu_items(data_root, split):
    items = []
    missing = []
    for subject_dir in sorted(path for path in data_root.iterdir() if path.is_dir()):
        csv_path = subject_dir / f"{split}.csv"
        if not csv_path.exists():
            missing.append(csv_path)
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            for row_index, row in enumerate(csv.DictReader(csv_file)):
                option_values = json.loads(row["choices"])
                options = labels_to_options(option_values)
                items.append(
                    QAItem(
                        dataset="mmlu",
                        source_id=f"{subject_dir.name}:{split}:{row_index}",
                        source_split=split,
                        subject=subject_dir.name,
                        question=row["question"],
                        options=options,
                        answer=mmlu_answer_to_label(row.get("answer", ""), len(options)),
                    )
                )
    if not items:
        raise ValueError(f"No MMLU rows found for split '{split}' under {data_root}.")
    for csv_path in missing:
        print(f"skipped mmlu subject: missing {csv_path}")
    return items


def load_commonsense_qa_items(data_root, split):
    resolved_split = resolve_hf_split(split)
    paths = sorted((data_root / "data").glob(f"{resolved_split}-*.parquet"))
    if not paths:
        raise ValueError(f"No CommonsenseQA parquet files found for split '{split}' under {data_root}.")

    items = []
    for row in read_parquet_rows(paths):
        concept = row.get("question_concept") or "commonsense_qa"
        items.append(
            QAItem(
                dataset="commonsense_qa",
                source_id=row.get("id") or f"{resolved_split}:{len(items)}",
                source_split=resolved_split,
                subject=f"commonsense_qa/{concept}",
                question=row["question"],
                options=choices_struct_to_options(row["choices"]),
                answer=(row.get("answerKey") or None),
            )
        )
    return items


def load_ai2_arc_items(data_root, split):
    resolved_split = resolve_hf_split(split)
    items = []
    for config_name in ("ARC-Challenge", "ARC-Easy"):
        paths = sorted((data_root / config_name).glob(f"{resolved_split}-*.parquet"))
        if not paths:
            print(f"skipped ai2_arc/{config_name}: missing split '{resolved_split}'")
            continue
        for row in read_parquet_rows(paths):
            items.append(
                QAItem(
                    dataset="ai2_arc",
                    source_id=row.get("id") or f"{config_name}:{resolved_split}:{len(items)}",
                    source_split=f"{config_name}/{resolved_split}",
                    subject=f"ai2_arc/{config_name}",
                    question=row["question"],
                    options=choices_struct_to_options(row["choices"]),
                    answer=(row.get("answerKey") or None),
                )
            )
    if not items:
        raise ValueError(f"No AI2 ARC rows found for split '{split}' under {data_root}.")
    return items


def load_socialiqa_items(data_root, split):
    resolved_split = resolve_socialiqa_split(split)
    path = data_root / f"socialIQa_v1.4_{resolved_split}.jsonl"
    if not path.exists():
        raise ValueError(f"No SocialIQA JSONL file found for split '{split}' at {path}.")

    items = []
    with path.open("r", encoding="utf-8") as jsonl_file:
        for row_index, line in enumerate(jsonl_file):
            row = json.loads(line)
            question = f"Context: {row['context']}\nQuestion: {row['question']}"
            items.append(
                QAItem(
                    dataset="socialiqa",
                    source_id=f"{resolved_split}:{row_index}",
                    source_split=resolved_split,
                    subject="socialiqa",
                    question=question,
                    options={
                        "A": row["answerA"],
                        "B": row["answerB"],
                        "C": row["answerC"],
                    },
                    answer=(row.get("correct") or None),
                )
            )
    return items


def load_dataset_items(root, dataset_name, split):
    data_root = dataset_root(root, dataset_name)
    loaders = {
        "mmlu": load_mmlu_items,
        "commonsense_qa": load_commonsense_qa_items,
        "ai2_arc": load_ai2_arc_items,
        "socialiqa": load_socialiqa_items,
    }
    return loaders[dataset_name](data_root, split)


def dataset_sample_counts(dataset_names, total_count, per_dataset_count):
    if per_dataset_count is not None:
        return {dataset_name: per_dataset_count for dataset_name in dataset_names}

    base_count, extra_count = divmod(total_count, len(dataset_names))
    return {
        dataset_name: base_count + (1 if index < extra_count else 0)
        for index, dataset_name in enumerate(dataset_names)
    }


def select_items(items, count, rng, dataset_name):
    if count < 1:
        return []
    if len(items) < count:
        raise ValueError(f"Need {count} rows from {dataset_name}, but only found {len(items)}.")
    return rng.sample(items, count)


def build_sample(item, sample_id, instruction, instruction_index, instruction_position):
    instruction = instruction.strip() if instruction else ""
    question = embed_instruction_in_question(item.question, instruction, instruction_position)
    return {
        "id": str(sample_id),
        "dataset": item.dataset,
        "source_id": item.source_id,
        "source_split": item.source_split,
        "subject": item.subject,
        "question": question,
        "options": item.options,
        "answer": item.answer,
        "instruction": instruction,
        "instruction_index": instruction_index,
        "instruction_position": instruction_position,
    }


def wrap_pdf_text(text, max_width, font_size=14, font_name="helv"):
    lines = []
    for raw_line in str(text).splitlines() or [""]:
        words = raw_line.split()
        if not words:
            lines.append("")
            continue

        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if fitz.get_text_length(candidate, fontname=font_name, fontsize=font_size) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                current = ""

            if fitz.get_text_length(word, fontname=font_name, fontsize=font_size) <= max_width:
                current = word
                continue

            chunk = ""
            for char in word:
                candidate = f"{chunk}{char}"
                if fitz.get_text_length(candidate, fontname=font_name, fontsize=font_size) <= max_width:
                    chunk = candidate
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            current = chunk

        if current:
            lines.append(current)
    return lines


def save_pdf(sample, path):
    page_width, page_height = fitz.paper_size("a4")
    margin = 24 / 25.4 * 72
    font_size = 14
    line_height = font_size * 1.6
    label_gap = 16 / 96 * 72
    font_name = "helv"
    max_width = page_width - (margin * 2)
    bottom = page_height - margin

    doc = fitz.open()
    page = doc.new_page(width=page_width, height=page_height)
    y = margin

    def ensure_space(height):
        nonlocal page, y
        if y + height <= bottom:
            return
        page = doc.new_page(width=page_width, height=page_height)
        y = margin

    def draw_line(text):
        nonlocal y
        ensure_space(line_height)
        page.insert_text(
            fitz.Point(margin, y + font_size),
            text,
            fontname=font_name,
            fontsize=font_size,
            color=(0, 0, 0),
        )
        y += line_height

    def draw_block(text):
        for line in wrap_pdf_text(text, max_width, font_size, font_name):
            draw_line(line)

    draw_line("Question:")
    draw_block(sample["question"])
    y += label_gap
    draw_line("Options:")
    for label, text in sample["options"].items():
        draw_block(f"{label}. {text}")
    if sample["instruction"] and sample["instruction_position"] == "buttom":
        y += label_gap
        draw_block(sample["instruction"])

    doc.save(path)
    doc.close()


def build_text(sample):
    options = "\n".join(f"{label}. {text}" for label, text in sample["options"].items())
    text = (
        f"Question:\n{sample['question']}\n\n"
        f"Options:\n{options}\n"
    )
    if sample["instruction"] and sample["instruction_position"] == "buttom":
        text += f"\n{sample['instruction']}\n"
    return text


def save_txt(sample, path):
    path.write_text(build_text(sample), encoding="utf-8")


def save_png(pdf_path, png_path, dpi=200):
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        png_path.write_bytes(pix.tobytes("png"))


def instruction_assignments(instructions, sample_count, rng):
    if not instructions:
        return [(None, None)] * sample_count

    base_count, extra_count = divmod(sample_count, len(instructions))
    assignments = []
    for instruction_index, instruction in enumerate(instructions):
        repeat_count = base_count + (1 if instruction_index < extra_count else 0)
        assignments.extend((instruction_index, instruction) for _ in range(repeat_count))
    rng.shuffle(assignments)
    return assignments


def process_sample(sample_id, item, instruction_assignment, instruction_position, dpi, txt_dir, pdf_dir, png_dir):
    instruction_index, instruction = instruction_assignment
    sample = build_sample(item, sample_id, instruction, instruction_index, instruction_position)
    file_stem = sample["id"]

    txt_path = txt_dir / f"{file_stem}.txt"
    pdf_path = pdf_dir / f"{file_stem}.pdf"
    png_path = png_dir / f"{file_stem}.png"
    save_txt(sample, txt_path)
    save_pdf(sample, pdf_path)
    save_png(pdf_path, png_path, dpi)

    if instruction_index is None:
        print(f"processed {file_stem} from {item.dataset}")
    else:
        print(f"processed {file_stem} from {item.dataset} with instruction {instruction_index + 1}")

    manifest_item = asdict(item)
    manifest_item.update(
        {
            "id": sample["id"],
            "instruction_index": instruction_index,
            "instruction_position": instruction_position,
            "txt_path": str(txt_path),
            "pdf_path": str(pdf_path),
            "png_path": str(png_path),
        }
    )
    return manifest_item


def write_manifest(path, rows):
    with path.open("w", encoding="utf-8") as manifest_file:
        for row in rows:
            manifest_file.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Sample QA rows evenly across MMLU, CommonsenseQA, AI2 ARC, and SocialIQA, "
            "then convert them to TXT/PDF/PNG with embedded instructions."
        )
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--datasets",
        default="all",
        help="Comma-separated datasets to sample, or 'all'. Valid values: mmlu, commonsense_qa, ai2_arc, socialiqa.",
    )
    parser.add_argument(
        "--split",
        default="dev",
        choices=("train", "dev", "validation", "test"),
        help="Logical split. For dev, MMLU/SocialIQA use dev and CommonsenseQA/AI2 ARC use validation.",
    )
    parser.add_argument(
        "--total-count",
        type=int,
        default=None,
        help="Total number of samples. Divided as evenly as possible across selected datasets.",
    )
    parser.add_argument(
        "--per-dataset-count",
        type=int,
        default=None,
        help="Number of samples to draw from each selected dataset. Overrides --total-count.",
    )
    parser.add_argument(
        "--subject-count",
        type=int,
        default=None,
        help="Deprecated alias for --total-count, kept for compatibility with old MMLU-only commands.",
    )
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIO_INSTRUCTION_PATHS),
        help=(
            "Instruction scenario. If --instruction-path is omitted, uses the matching "
            "data/scenario/<scenario>_instruction.txt file."
        ),
    )
    parser.add_argument(
        "--instruction-path",
        default=None,
        help=(
            "Instruction file. One blank-line separated block is embedded into each sampled question; "
            "blocks are assigned evenly across sampled rows. Use --instruction-path= for clean samples."
        ),
    )
    parser.add_argument(
        "--instruction-count",
        type=int,
        default=1,
        help=(
            "Number of blank-line separated instruction blocks expected in --instruction-path. "
            "A single block is also accepted and reused for all samples."
        ),
    )
    parser.add_argument(
        "--position",
        choices=INSTRUCTION_POSITIONS,
        default="medium",
        help="Instruction embedding position: top before question, medium after question, buttom after options.",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible sampling.")
    parser.add_argument("--out-root", type=Path)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--font-cache-dir", type=Path)
    parser.add_argument(
        "--dll-dir",
        type=Path,
        action="append",
        default=[Path(r"C:\Program Files\Tesseract-OCR")],
        help="Extra Windows DLL directory for WeasyPrint dependencies. Can be passed more than once.",
    )
    return parser.parse_args()


def resolve_sample_count(args):
    if args.per_dataset_count is not None and args.per_dataset_count < 1:
        raise ValueError("--per-dataset-count must be >= 1")
    if args.total_count is not None and args.total_count < 1:
        raise ValueError("--total-count must be >= 1")
    if args.subject_count is not None and args.subject_count < 1:
        raise ValueError("--subject-count must be >= 1")
    if args.per_dataset_count is not None:
        return None, args.per_dataset_count
    total_count = args.total_count if args.total_count is not None else args.subject_count
    return total_count or DEFAULT_TOTAL_COUNT, None


def main():
    args = parse_args()

    root = args.root.resolve()
    out_root = args.out_root or root / "outputs" / "embedded_samples"
    font_cache_dir = args.font_cache_dir or root / ".cache" / "fontconfig"

    configure_runtime(font_cache_dir, args.dll_dir)
    dataset_names = parse_dataset_names(args.datasets)
    total_count, per_dataset_count = resolve_sample_count(args)

    sample_rng = make_rng(args.seed, "samples")
    instruction_rng = make_rng(args.seed, "instructions")
    order_rng = make_rng(args.seed, "order")

    output_date = datetime.now().strftime("%m%d")
    out_dir = out_root / output_date
    txt_dir = out_dir / "txt"
    pdf_dir = out_dir / "pdf"
    png_dir = out_dir / "png"
    for path in (txt_dir, pdf_dir, png_dir):
        path.mkdir(parents=True, exist_ok=True)

    instructions = []
    instruction_path = resolve_instruction_path(root, args.scenario, args.instruction_path)
    if instruction_path is not None:
        instructions = instruction_parts(instruction_path.read_text(encoding="utf-8"), args.instruction_count)

    counts = dataset_sample_counts(dataset_names, total_count, per_dataset_count)
    selected_items = []
    for dataset_name in dataset_names:
        items = load_dataset_items(root, dataset_name, args.split)
        selected = select_items(items, counts[dataset_name], sample_rng, dataset_name)
        selected_items.extend(selected)
        print(f"selected {len(selected)} / {len(items)} from {dataset_name}")

    order_rng.shuffle(selected_items)
    instruction_plan = instruction_assignments(instructions, len(selected_items), instruction_rng)

    manifest_rows = []
    for sample_id, (item, instruction_assignment) in enumerate(zip(selected_items, instruction_plan)):
        manifest_rows.append(
            process_sample(
                sample_id,
                item,
                instruction_assignment,
                args.position,
                args.dpi,
                txt_dir,
                pdf_dir,
                png_dir,
            )
        )

    write_manifest(out_dir / "manifest.jsonl", manifest_rows)
    print(f"wrote manifest: {out_dir / 'manifest.jsonl'}")


if __name__ == "__main__":
    main()

"""
Git Bash examples:

Generate 120 samples evenly across MMLU, CommonsenseQA, AI2 ARC, and SocialIQA.
Outputs are grouped as ./outputs/<run>/<MMDD>/txt, pdf, png, plus manifest.jsonl.
./.venv/pmpa/Scripts/python.exe ./jobscripts/data_preprosess.py \
  --split dev \
  --total-count 30 \
  --scenario calendar \
  --position medium\
  --out-root ./download/c_messages \
  --seed 15

Generate clean random samples without embedded instructions.
./.venv/pmpa/Scripts/python.exe ./jobscripts/data_preprosess.py \
  --split dev \
  --total-count 5 \
  --instruction-path= \
  --out-root ./download/multi-interaction/clean \
  --seed 15

Generate MMLU-only samples with the same unified renderer.
./.venv/pmpa/Scripts/python.exe ./jobscripts/data_preprosess.py \
  --datasets mmlu \
  --split test \
  --total-count 30 \
  --scenario form \
  --out-root ./outputs/f_sample \
  --seed 3
"""
