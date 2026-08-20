#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import server


DEFAULT_TMP_ROOT = Path.cwd() / ".tmp" / "pdf-reader"


def _bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def _clean_args(args: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in args.items() if value is not None}


def _print_result(result: Dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Project wrapper for the pdf-reader MCP server logic")
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="Get PDF page count and metadata")
    info.add_argument("path")

    read = sub.add_parser("read", help="Extract text from a PDF text layer")
    read.add_argument("path")
    read.add_argument("--start-page", type=int, default=1)
    read.add_argument("--end-page", type=int)
    read.add_argument("--max-chars", type=int, default=30000)

    hybrid = sub.add_parser("hybrid", help="Extract text; return embedded images when text is weak")
    hybrid.add_argument("path")
    hybrid.add_argument("--start-page", type=int, default=1)
    hybrid.add_argument("--end-page", type=int)
    hybrid.add_argument("--out-dir", default=str(DEFAULT_TMP_ROOT / "hybrid-output"))
    hybrid.add_argument("--min-text-chars", type=int, default=50)
    hybrid.add_argument("--min-effective-ratio", type=float, default=0.5)
    hybrid.add_argument("--inline-images", type=_bool, default=True)
    hybrid.add_argument("--max-images-per-page", type=int, default=1)
    hybrid.add_argument("--max-total-image-bytes", type=int, default=10 * 1024 * 1024)
    hybrid.add_argument("--max-chars", type=int, default=30000)

    ocr = sub.add_parser("ocr-embedded", help="OCR embedded PDF images with local Tesseract")
    ocr.add_argument("path")
    ocr.add_argument("--start-page", type=int, default=1)
    ocr.add_argument("--end-page", type=int)
    ocr.add_argument("--lang", default="eng")
    ocr.add_argument("--psm", type=int)
    ocr.add_argument("--preprocess", type=_bool, default=True)
    ocr.add_argument("--preprocess-scale", type=int, default=3)
    ocr.add_argument("--preprocess-autocontrast", type=_bool, default=True)
    ocr.add_argument("--preprocess-contrast", type=float, default=1.8)
    ocr.add_argument("--preprocess-unsharp", type=_bool, default=True)
    ocr.add_argument("--preprocess-binarize", type=_bool, default=True)
    ocr.add_argument("--out-dir", default=str(DEFAULT_TMP_ROOT / "ocr-embedded"))
    ocr.add_argument("--max-images-per-page", type=int, default=1)
    ocr.add_argument("--max-total-bytes", type=int, default=10 * 1024 * 1024)
    ocr.add_argument("--max-chars", type=int, default=30000)

    ns = parser.parse_args()
    allowed_roots = server._expand_roots(os.environ.get("PDF_ALLOWED_ROOTS", ""))

    if ns.command == "info":
        tool_name = "get_pdf_info"
        arguments = {"path": ns.path}
    elif ns.command == "read":
        tool_name = "read_pdf"
        arguments = _clean_args(
            {
                "path": ns.path,
                "start_page": ns.start_page,
                "end_page": ns.end_page,
                "max_chars": ns.max_chars,
            }
        )
    elif ns.command == "hybrid":
        tool_name = "read_pdf_hybrid"
        arguments = _clean_args(
            {
                "path": ns.path,
                "start_page": ns.start_page,
                "end_page": ns.end_page,
                "out_dir": ns.out_dir,
                "min_text_chars": ns.min_text_chars,
                "min_effective_ratio": ns.min_effective_ratio,
                "inline_images": ns.inline_images,
                "max_images_per_page": ns.max_images_per_page,
                "max_total_image_bytes": ns.max_total_image_bytes,
                "max_chars": ns.max_chars,
            }
        )
    else:
        tool_name = "read_pdf_ocr_embedded"
        arguments = _clean_args(
            {
                "path": ns.path,
                "start_page": ns.start_page,
                "end_page": ns.end_page,
                "lang": ns.lang,
                "psm": ns.psm,
                "preprocess": ns.preprocess,
                "preprocess_scale": ns.preprocess_scale,
                "preprocess_autocontrast": ns.preprocess_autocontrast,
                "preprocess_contrast": ns.preprocess_contrast,
                "preprocess_unsharp": ns.preprocess_unsharp,
                "preprocess_binarize": ns.preprocess_binarize,
                "out_dir": ns.out_dir,
                "max_images_per_page": ns.max_images_per_page,
                "max_total_bytes": ns.max_total_bytes,
                "max_chars": ns.max_chars,
            }
        )

    _print_result(server._handle_call(tool_name, arguments, allowed_roots))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
