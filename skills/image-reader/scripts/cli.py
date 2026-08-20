#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict

import server


def _bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def _clean_args(args: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in args.items() if value is not None}


def main() -> int:
    os.environ.setdefault("IMAGE_READER_TEMP_DIR", str(Path.cwd() / ".tmp" / "image-reader-temp"))

    parser = argparse.ArgumentParser(description="Project wrapper for the image-reader MCP server logic")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("info", "Read image metadata only"),
        ("read", "Return image metadata plus inline image content"),
        ("ocr", "OCR the image locally with Tesseract"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("source")
        p.add_argument("--timeout-seconds", type=int, default=20)
        p.add_argument("--max-image-bytes", type=int, default=5_000_000)
        p.add_argument("--user-agent")
        if name == "ocr":
            p.add_argument("--lang", default="eng")
            p.add_argument("--psm", type=int, default=6)
            p.add_argument("--preprocess", type=_bool, default=True)
            p.add_argument("--preprocess-scale", type=int, default=3)
            p.add_argument("--preprocess-autocontrast", type=_bool, default=True)
            p.add_argument("--preprocess-contrast", type=float, default=1.8)
            p.add_argument("--preprocess-unsharp", type=_bool, default=True)
            p.add_argument("--preprocess-binarize", type=_bool, default=False)
            p.add_argument("--max-chars", type=int, default=30000)

    ns = parser.parse_args()
    allowed_roots = server._expand_roots(os.environ.get("IMAGE_ALLOWED_ROOTS", ""))

    arguments = _clean_args(
        {
            "source": ns.source,
            "timeout_seconds": ns.timeout_seconds,
            "max_image_bytes": ns.max_image_bytes,
            "user_agent": ns.user_agent,
        }
    )

    if ns.command == "info":
        tool_name = "get_image_info"
    elif ns.command == "read":
        tool_name = "read_image"
    else:
        tool_name = "read_image_ocr"
        arguments.update(
            _clean_args(
                {
                    "lang": ns.lang,
                    "psm": ns.psm,
                    "preprocess": ns.preprocess,
                    "preprocess_scale": ns.preprocess_scale,
                    "preprocess_autocontrast": ns.preprocess_autocontrast,
                    "preprocess_contrast": ns.preprocess_contrast,
                    "preprocess_unsharp": ns.preprocess_unsharp,
                    "preprocess_binarize": ns.preprocess_binarize,
                    "max_chars": ns.max_chars,
                }
            )
        )

    result = server._handle_call(tool_name, arguments, allowed_roots)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
