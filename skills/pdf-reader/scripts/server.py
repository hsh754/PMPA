#!/usr/bin/env python3
import json
import os
import base64
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader


def _write(msg: Dict[str, Any]) -> None:
    # Keep ASCII-only transport to avoid Windows console encoding failures (e.g. GBK).
    sys.stdout.write(json.dumps(msg, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _expand_roots(raw: str) -> List[Path]:
    roots: List[Path] = []
    for part in (raw or "").split(";"):
        value = part.strip()
        if not value:
            continue
        roots.append(Path(value).resolve())
    return roots


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_pdf_path(path: str, allowed_roots: List[Path]) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise RuntimeError(f"File does not exist: {candidate}")
    if candidate.suffix.lower() != ".pdf":
        raise RuntimeError("Only .pdf files are supported.")

    if allowed_roots:
        for root in allowed_roots:
            if _is_within(candidate, root):
                return candidate
        roots = ", ".join(str(root) for root in allowed_roots)
        raise RuntimeError(f"Path is outside allowed roots. Allowed roots: {roots}")

    return candidate


def _validate_output_dir(out_dir: str, allowed_roots: List[Path]) -> Path:
    candidate = Path(out_dir).expanduser().resolve()
    if allowed_roots:
        for root in allowed_roots:
            if _is_within(candidate, root):
                return candidate
        roots = ", ".join(str(root) for root in allowed_roots)
        raise RuntimeError(f"out_dir is outside allowed roots. Allowed roots: {roots}")
    return candidate


def _safe_filename(value: str, fallback: str) -> str:
    name = (value or "").strip() or fallback
    name = name.replace("\\", "_").replace("/", "_").replace(":", "_").replace("\0", "")
    return (name[:200] or fallback).strip()


def _strip_known_extension(name: str) -> str:
    lower = (name or "").lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".jp2"):
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _guess_image_extension(name: str, data: bytes, fallback: str = "bin") -> str:
    lower = (name or "").lower().strip()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".jp2"):
        if lower.endswith(ext):
            return ext.lstrip(".")

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n"):
        return "jp2"

    return fallback


def _extract_text(path: Path, start_page: int, end_page: int, max_chars: int) -> Dict[str, Any]:
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)

    if total_pages == 0:
        return {
            "path": str(path),
            "pages_total": 0,
            "page_range": {"start": 1, "end": 0},
            "chars": 0,
            "truncated": False,
            "text": "",
        }

    if start_page < 1:
        start_page = 1
    if end_page < 1 or end_page > total_pages:
        end_page = total_pages
    if start_page > end_page:
        raise RuntimeError("start_page cannot be greater than end_page.")
    if max_chars < 1:
        raise RuntimeError("max_chars must be >= 1.")

    out: List[str] = []
    total_len = 0
    truncated = False

    for page_num in range(start_page, end_page + 1):
        text = reader.pages[page_num - 1].extract_text() or ""
        if not text:
            text = ""
        to_add = f"\n\n===== Page {page_num} =====\n{text}"
        if total_len + len(to_add) > max_chars:
            remain = max_chars - total_len
            if remain > 0:
                out.append(to_add[:remain])
            truncated = True
            break
        out.append(to_add)
        total_len += len(to_add)

    joined = "".join(out).lstrip("\n")
    return {
        "path": str(path),
        "pages_total": total_pages,
        "page_range": {"start": start_page, "end": end_page},
        "chars": len(joined),
        "truncated": truncated,
        "text": joined,
    }


def _pdf_info(path: Path) -> Dict[str, Any]:
    reader = PdfReader(str(path))
    meta = reader.metadata or {}
    result = {
        "path": str(path),
        "pages_total": len(reader.pages),
        "metadata": {
            "title": str(meta.get("/Title", "") or ""),
            "author": str(meta.get("/Author", "") or ""),
            "subject": str(meta.get("/Subject", "") or ""),
            "creator": str(meta.get("/Creator", "") or ""),
            "producer": str(meta.get("/Producer", "") or ""),
            "creation_date": str(meta.get("/CreationDate", "") or ""),
            "mod_date": str(meta.get("/ModDate", "") or ""),
        },
    }
    return result


def _normalize_page_range(total_pages: int, start_page: int, end_page: int) -> Tuple[int, int]:
    if total_pages < 1:
        return 1, 0
    if start_page < 1:
        start_page = 1
    if end_page < 1 or end_page > total_pages:
        end_page = total_pages
    if start_page > end_page:
        raise RuntimeError("start_page cannot be greater than end_page.")
    return start_page, end_page


def _tesseract_ocr_file(image_path: Path, lang: str, psm: Optional[int]) -> str:
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError(
            "tesseract executable not found. Install Tesseract OCR and ensure it's on PATH."
        )

    args = [exe, str(image_path), "stdout"]
    if lang:
        args += ["-l", lang]
    if psm is not None:
        args += ["--psm", str(int(psm))]

    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"tesseract failed (code {proc.returncode}): {err or 'unknown error'}")
    return proc.stdout or ""


def _otsu_threshold(hist: List[int]) -> int:
    # hist: list of 256 ints
    total = sum(hist)
    if total <= 0:
        return 128

    sum_total = 0
    for i, h in enumerate(hist):
        sum_total += i * h

    sum_b = 0
    w_b = 0
    max_var = -1.0
    threshold = 128

    for t in range(256):
        h = hist[t]
        w_b += h
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * h
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            threshold = t

    return int(threshold)


def _preprocess_image_for_ocr(
    image_path: Path,
    out_dir_abs: Path,
    base_name: str,
    stamp: int,
    *,
    scale: int,
    autocontrast: bool,
    contrast: float,
    unsharp: bool,
    binarize: bool,
) -> Tuple[Path, Dict[str, Any]]:
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except Exception as err:
        raise RuntimeError(f"Pillow is required for preprocessing (pip install pillow). {err}") from err

    if scale < 1:
        scale = 1
    if contrast <= 0:
        contrast = 1.0

    im = Image.open(image_path)
    im = im.convert("L")

    if scale != 1:
        im = im.resize(
            (int(im.width * scale), int(im.height * scale)),
            resample=Image.Resampling.LANCZOS,
        )

    if autocontrast:
        im = ImageOps.autocontrast(im)

    if contrast != 1.0:
        im = ImageEnhance.Contrast(im).enhance(float(contrast))

    if unsharp:
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3))

    otsu = None
    if binarize:
        hist = im.histogram()
        otsu = _otsu_threshold(hist)
        # mode="1" yields 1-bit image which is good for OCR on UI-like PDFs.
        im = im.point(lambda p: 255 if p > otsu else 0, mode="1")

    out_name = f"{base_name}_{stamp}_pre.png"
    pre_path = (out_dir_abs / out_name).resolve()
    if not (pre_path == out_dir_abs or _is_within(pre_path, out_dir_abs)):
        raise RuntimeError("Refusing to write outside out_dir.")
    im.save(pre_path)

    info: Dict[str, Any] = {
        "scale": scale,
        "autocontrast": autocontrast,
        "contrast": float(contrast),
        "unsharp": unsharp,
        "binarize": binarize,
    }
    if otsu is not None:
        info["otsu_threshold"] = int(otsu)
    return pre_path, info


def _read_pdf_ocr_from_embedded_images(
    path: Path,
    start_page: int,
    end_page: int,
    out_dir: Path,
    lang: str,
    psm: Optional[int],
    preprocess: bool,
    preprocess_scale: int,
    preprocess_autocontrast: bool,
    preprocess_contrast: float,
    preprocess_unsharp: bool,
    preprocess_binarize: bool,
    max_images_per_page: int,
    max_total_bytes: int,
    max_chars: int,
) -> Dict[str, Any]:
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    start_page, end_page = _normalize_page_range(total_pages, start_page, end_page)

    if max_images_per_page < 1:
        raise RuntimeError("max_images_per_page must be >= 1.")
    if max_total_bytes < 1:
        raise RuntimeError("max_total_bytes must be >= 1.")
    if max_chars < 1:
        raise RuntimeError("max_chars must be >= 1.")

    hard_cap = 25 * 1024 * 1024
    if max_total_bytes > hard_cap:
        max_total_bytes = hard_cap

    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_abs = out_dir.resolve()

    extracted: List[Dict[str, Any]] = []
    texts: List[str] = []
    total_bytes = 0
    total_len = 0
    truncated = False
    stamp = int(time.time())

    for page_num in range(start_page, end_page + 1):
        page = reader.pages[page_num - 1]
        try:
            images = list(getattr(page, "images", []) or [])
        except Exception:
            images = []

        if not images:
            continue

        for idx, img in enumerate(images[:max_images_per_page]):
            raw = getattr(img, "data", None)
            data = bytes(raw) if isinstance(raw, (bytes, bytearray)) else b""
            if not data:
                continue

            size = len(data)
            if total_bytes + size > max_total_bytes:
                truncated = True
                break
            total_bytes += size

            name = str(getattr(img, "name", "") or "")
            ext = (getattr(img, "extension", "") or "").strip().lstrip(".")
            if not ext:
                ext = _guess_image_extension(name, data, fallback="png")

            base = _safe_filename(_strip_known_extension(name), f"page_{page_num}_img_{idx}")
            filename = f"{base}_{stamp}.{ext}"
            image_path = (out_dir_abs / filename).resolve()
            if not (image_path == out_dir_abs or _is_within(image_path, out_dir_abs)):
                raise RuntimeError("Refusing to write outside out_dir.")
            with open(image_path, "wb") as f:
                f.write(data)

            ocr_input_path = image_path
            preprocess_info: Optional[Dict[str, Any]] = None
            if preprocess:
                ocr_input_path, preprocess_info = _preprocess_image_for_ocr(
                    image_path,
                    out_dir_abs,
                    base,
                    stamp,
                    scale=preprocess_scale,
                    autocontrast=preprocess_autocontrast,
                    contrast=preprocess_contrast,
                    unsharp=preprocess_unsharp,
                    binarize=preprocess_binarize,
                )

            effective_psm = psm if psm is not None else 6
            ocr_text = _tesseract_ocr_file(ocr_input_path, lang=lang, psm=effective_psm).strip()
            extracted.append(
                {
                    "page": page_num,
                    "index": idx,
                    "name": name,
                    "extension": ext,
                    "size_bytes": size,
                    "saved_path": str(image_path).replace("\\", "/"),
                    "ocr_input_path": str(ocr_input_path).replace("\\", "/"),
                    "preprocess": preprocess_info,
                }
            )

            block = f"\n\n===== Page {page_num} (OCR) =====\n{ocr_text}"
            if total_len + len(block) > max_chars:
                remain = max_chars - total_len
                if remain > 0:
                    texts.append(block[:remain])
                truncated = True
                break
            texts.append(block)
            total_len += len(block)

        if truncated:
            break

    joined = "".join(texts).lstrip("\n")
    return {
        "path": str(path),
        "pages_total": total_pages,
        "page_range": {"start": start_page, "end": end_page},
        "lang": lang,
        "psm": psm,
        "psm_effective_default": 6,
        "preprocess": {
            "enabled": preprocess,
            "scale": preprocess_scale,
            "autocontrast": preprocess_autocontrast,
            "contrast": preprocess_contrast,
            "unsharp": preprocess_unsharp,
            "binarize": preprocess_binarize,
        },
        "out_dir": str(out_dir_abs),
        "extracted_images": extracted,
        "extracted_total_bytes": total_bytes,
        "chars": len(joined),
        "truncated": truncated,
        "text": joined,
    }


def _ext_to_media_type(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    if e in ("jpg", "jpeg"):
        return "image/jpeg"
    if e == "png":
        return "image/png"
    if e == "webp":
        return "image/webp"
    if e == "gif":
        return "image/gif"
    if e == "bmp":
        return "image/bmp"
    if e in ("tif", "tiff"):
        return "image/tiff"
    if e == "jp2":
        return "image/jp2"
    return "application/octet-stream"


def _effective_char_ratio(text: str) -> float:
    # "有效字符占比"：排除空白后，计算字母/数字/常见符号所占比例
    if not text:
        return 0.0
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    effective = 0
    for c in non_ws:
        if c.isalnum() or c in "@._-+:/":
            effective += 1
    return effective / max(1, len(non_ws))


def _extract_embedded_images_for_page(
    page: Any,
    page_num: int,
    out_dir_abs: Path,
    *,
    stamp: int,
    max_images: int,
    max_total_bytes: int,
    total_bytes_state: List[int],
) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []

    try:
        images = list(getattr(page, "images", []) or [])
    except Exception:
        images = []

    for idx, img in enumerate(images[:max_images]):
        raw = getattr(img, "data", None)
        data = bytes(raw) if isinstance(raw, (bytes, bytearray)) else b""
        if not data:
            continue

        size = len(data)
        if total_bytes_state[0] + size > max_total_bytes:
            break
        total_bytes_state[0] += size

        name = str(getattr(img, "name", "") or "")
        ext = (getattr(img, "extension", "") or "").strip().lstrip(".")
        if not ext:
            ext = _guess_image_extension(name, data, fallback="png")

        base = _safe_filename(_strip_known_extension(name), f"page_{page_num}_img_{idx}")
        filename = f"{base}_{stamp}.{ext}"
        image_path = (out_dir_abs / filename).resolve()
        if not (image_path == out_dir_abs or _is_within(image_path, out_dir_abs)):
            raise RuntimeError("Refusing to write outside out_dir.")
        with open(image_path, "wb") as f:
            f.write(data)

        extracted.append(
            {
                "page": page_num,
                "index": idx,
                "name": name,
                "extension": ext,
                "size_bytes": size,
                "saved_path": str(image_path).replace("\\", "/"),
                "media_type": _ext_to_media_type(ext),
                "data_base64": base64.b64encode(data).decode("ascii"),
            }
        )

    return extracted


def _read_pdf_hybrid(
    path: Path,
    start_page: int,
    end_page: int,
    out_dir: Path,
    *,
    min_text_chars: int,
    min_effective_ratio: float,
    inline_images: bool,
    max_images_per_page: int,
    max_total_image_bytes: int,
    max_chars: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Hybrid reader:
      - prefer text layer (pypdf extract_text)
      - if text seems missing/low-quality, fall back to extracting embedded images
        and returning them inline so the main model can do vision transcription.

    Note: This does NOT run local OCR.
    """
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    start_page, end_page = _normalize_page_range(total_pages, start_page, end_page)

    if min_text_chars < 0:
        min_text_chars = 0
    if min_effective_ratio < 0:
        min_effective_ratio = 0.0
    if min_effective_ratio > 1:
        min_effective_ratio = 1.0

    if max_images_per_page < 1:
        raise RuntimeError("max_images_per_page must be >= 1.")
    if max_total_image_bytes < 1:
        raise RuntimeError("max_total_image_bytes must be >= 1.")
    if max_chars < 1:
        raise RuntimeError("max_chars must be >= 1.")

    hard_cap = 25 * 1024 * 1024
    if max_total_image_bytes > hard_cap:
        max_total_image_bytes = hard_cap

    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_abs = out_dir.resolve()

    pages: List[Dict[str, Any]] = []
    content_texts: List[str] = []
    image_blocks: List[Dict[str, Any]] = []

    total_len = 0
    total_image_bytes_state = [0]
    stamp = int(time.time())

    for page_num in range(start_page, end_page + 1):
        page = reader.pages[page_num - 1]
        text = page.extract_text() or ""
        stripped = "".join(text.split())
        ratio = _effective_char_ratio(text)

        has_good_text = len(stripped) >= min_text_chars and ratio >= min_effective_ratio

        if has_good_text:
            block = f"\n\n===== Page {page_num} =====\n{text}"
            if total_len + len(block) > max_chars:
                remain = max_chars - total_len
                if remain > 0:
                    content_texts.append(block[:remain])
                pages.append(
                    {
                        "page": page_num,
                        "mode": "text",
                        "text_chars": len(text),
                        "effective_ratio": ratio,
                        "truncated": True,
                    }
                )
                break
            content_texts.append(block)
            total_len += len(block)
            pages.append(
                {
                    "page": page_num,
                    "mode": "text",
                    "text_chars": len(text),
                    "effective_ratio": ratio,
                    "truncated": False,
                }
            )
            continue

        extracted = _extract_embedded_images_for_page(
            page,
            page_num,
            out_dir_abs,
            stamp=stamp,
            max_images=max_images_per_page,
            max_total_bytes=max_total_image_bytes,
            total_bytes_state=total_image_bytes_state,
        )

        pages.append(
            {
                "page": page_num,
                "mode": "image",
                "text_chars": len(text),
                "effective_ratio": ratio,
                "images": [
                    {
                        "saved_path": e["saved_path"],
                        "media_type": e["media_type"],
                        "size_bytes": e["size_bytes"],
                    }
                    for e in extracted
                ],
            }
        )

        note = (
            f"\n\n===== Page {page_num} (IMAGE) =====\n"
            "Text layer appears empty/low-quality; returning embedded page image(s) for model vision."
        )
        if total_len + len(note) <= max_chars:
            content_texts.append(note)
            total_len += len(note)

        if inline_images:
            for e in extracted:
                image_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": e["media_type"],
                            "data": e["data_base64"],
                        },
                    }
                )

    joined = "".join(content_texts).lstrip("\n")
    meta = {
        "path": str(path),
        "pages_total": total_pages,
        "page_range": {"start": start_page, "end": end_page},
        "thresholds": {"min_text_chars": min_text_chars, "min_effective_ratio": min_effective_ratio},
        "inline_images": inline_images,
        "out_dir": str(out_dir_abs),
        "pages": pages,
        "extracted_total_image_bytes": total_image_bytes_state[0],
        "chars": len(joined),
        "truncated": len(joined) >= max_chars,
    }
    return ({"text": joined, "meta": meta}, image_blocks)


def _tool_list() -> Dict[str, Any]:
    return {
        "tools": [
            {
                "name": "read_pdf",
                "description": "Read text from a local PDF file with optional page range and max length.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to a .pdf file"},
                        "start_page": {"type": "integer", "minimum": 1, "default": 1},
                        "end_page": {"type": "integer", "minimum": 1},
                        "max_chars": {"type": "integer", "minimum": 1, "default": 30000},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "get_pdf_info",
                "description": "Get PDF page count and document metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to a .pdf file"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "read_pdf_ocr_embedded",
                "description": "OCR text from embedded images inside the PDF using Tesseract (no PyMuPDF required).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to a .pdf file"},
                        "start_page": {"type": "integer", "minimum": 1, "default": 1},
                        "end_page": {"type": "integer", "minimum": 1},
                        "lang": {"type": "string", "default": "eng"},
                        "psm": {"type": "integer", "minimum": 0, "maximum": 13, "default": 6},
                        "preprocess": {"type": "boolean", "default": True},
                        "preprocess_scale": {"type": "integer", "minimum": 1, "default": 3},
                        "preprocess_autocontrast": {"type": "boolean", "default": True},
                        "preprocess_contrast": {"type": "number", "minimum": 0.1, "default": 1.8},
                        "preprocess_unsharp": {"type": "boolean", "default": True},
                        "preprocess_binarize": {"type": "boolean", "default": True},
                        "out_dir": {
                            "type": "string",
                            "default": ".tmp/pdf-reader/ocr-embedded",
                        },
                        "max_images_per_page": {"type": "integer", "minimum": 1, "default": 1},
                        "max_total_bytes": {"type": "integer", "minimum": 1, "default": 10485760},
                        "max_chars": {"type": "integer", "minimum": 1, "default": 30000},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "read_pdf_hybrid",
                "description": "Hybrid PDF reader: extract text layer when present; otherwise return embedded images inline for the main model to read (no local OCR).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to a .pdf file"},
                        "start_page": {"type": "integer", "minimum": 1, "default": 1},
                        "end_page": {"type": "integer", "minimum": 1},
                        "out_dir": {
                            "type": "string",
                            "default": ".tmp/pdf-reader/hybrid-output",
                        },
                        "min_text_chars": {"type": "integer", "minimum": 0, "default": 50},
                        "min_effective_ratio": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                        "inline_images": {"type": "boolean", "default": True},
                        "max_images_per_page": {"type": "integer", "minimum": 1, "default": 1},
                        "max_total_image_bytes": {"type": "integer", "minimum": 1, "default": 10485760},
                        "max_chars": {"type": "integer", "minimum": 1, "default": 30000},
                    },
                    "required": ["path"],
                },
            },
        ]
    }


def _handle_call(name: str, args: Dict[str, Any], allowed_roots: List[Path]) -> Dict[str, Any]:
    if name == "read_pdf":
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("path is required")
        start_page = int(args.get("start_page", 1))
        end_page = int(args.get("end_page", 0))
        max_chars = int(args.get("max_chars", 30000))

        safe_path = _validate_pdf_path(path, allowed_roots)
        result = _extract_text(safe_path, start_page=start_page, end_page=end_page, max_chars=max_chars)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

    if name == "read_pdf_ocr_embedded":
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("path is required")
        start_page = int(args.get("start_page", 1))
        end_page = int(args.get("end_page", 0))
        lang = str(args.get("lang", "eng")).strip()
        psm_raw = args.get("psm", None)
        psm = int(psm_raw) if psm_raw is not None else None
        preprocess = bool(args.get("preprocess", True))
        preprocess_scale = int(args.get("preprocess_scale", 3))
        preprocess_autocontrast = bool(args.get("preprocess_autocontrast", True))
        preprocess_contrast = float(args.get("preprocess_contrast", 1.8))
        preprocess_unsharp = bool(args.get("preprocess_unsharp", True))
        preprocess_binarize = bool(args.get("preprocess_binarize", True))
        out_dir_raw = str(
            args.get("out_dir", ".tmp/pdf-reader/ocr-embedded")
        )
        max_images_per_page = int(args.get("max_images_per_page", 1))
        max_total_bytes = int(args.get("max_total_bytes", 10 * 1024 * 1024))
        max_chars = int(args.get("max_chars", 30000))

        safe_path = _validate_pdf_path(path, allowed_roots)
        out_dir = _validate_output_dir(out_dir_raw, allowed_roots)
        result = _read_pdf_ocr_from_embedded_images(
            safe_path,
            start_page=start_page,
            end_page=end_page,
            out_dir=out_dir,
            lang=lang,
            psm=psm,
            preprocess=preprocess,
            preprocess_scale=preprocess_scale,
            preprocess_autocontrast=preprocess_autocontrast,
            preprocess_contrast=preprocess_contrast,
            preprocess_unsharp=preprocess_unsharp,
            preprocess_binarize=preprocess_binarize,
            max_images_per_page=max_images_per_page,
            max_total_bytes=max_total_bytes,
            max_chars=max_chars,
        )
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

    if name == "read_pdf_hybrid":
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("path is required")
        start_page = int(args.get("start_page", 1))
        end_page = int(args.get("end_page", 0))
        out_dir_raw = str(
            args.get("out_dir", ".tmp/pdf-reader/hybrid-output")
        )
        min_text_chars = int(args.get("min_text_chars", 50))
        min_effective_ratio = float(args.get("min_effective_ratio", 0.5))
        inline_images = bool(args.get("inline_images", True))
        max_images_per_page = int(args.get("max_images_per_page", 1))
        max_total_image_bytes = int(args.get("max_total_image_bytes", 10 * 1024 * 1024))
        max_chars = int(args.get("max_chars", 30000))

        safe_path = _validate_pdf_path(path, allowed_roots)
        out_dir = _validate_output_dir(out_dir_raw, allowed_roots)
        (data, image_blocks) = _read_pdf_hybrid(
            safe_path,
            start_page=start_page,
            end_page=end_page,
            out_dir=out_dir,
            min_text_chars=min_text_chars,
            min_effective_ratio=min_effective_ratio,
            inline_images=inline_images,
            max_images_per_page=max_images_per_page,
            max_total_image_bytes=max_total_image_bytes,
            max_chars=max_chars,
        )

        # Return the extracted text as a normal text content block, plus optional inline image blocks.
        content: List[Dict[str, Any]] = [{"type": "text", "text": data["text"]}]
        content.extend(image_blocks)
        # Append a compact JSON meta as an extra text block for debugging/inspection.
        content.append({"type": "text", "text": json.dumps(data["meta"], ensure_ascii=False, indent=2)})
        return {"content": content}

    if name == "get_pdf_info":
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("path is required")
        safe_path = _validate_pdf_path(path, allowed_roots)
        result = _pdf_info(safe_path)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

    raise RuntimeError(f"Unknown tool: {name}")


def main() -> None:
    allowed_roots = _expand_roots(os.environ.get("PDF_ALLOWED_ROOTS", ""))
    server_name = "pdf-reader"
    server_version = "1.7.0"

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})

        try:
            if method == "initialize":
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "serverInfo": {"name": server_name, "version": server_version},
                            "capabilities": {"tools": {}},
                        },
                    }
                )
                continue

            if method == "notifications/initialized":
                continue

            if method == "tools/list":
                _write({"jsonrpc": "2.0", "id": req_id, "result": _tool_list()})
                continue

            if method == "tools/call":
                tool_name = str(params.get("name", ""))
                arguments = params.get("arguments", {}) or {}
                result = _handle_call(tool_name, dict(arguments), allowed_roots)
                _write({"jsonrpc": "2.0", "id": req_id, "result": result})
                continue

            if req_id is not None:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
        except Exception as err:
            if req_id is not None:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32000, "message": str(err)},
                    }
                )


if __name__ == "__main__":
    main()
