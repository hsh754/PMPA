#!/usr/bin/env python3
import base64
import json
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


_DEFAULT_USER_AGENT = "claude-image-reader/1.0"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _write(msg: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _expand_roots(raw: str) -> List[Path]:
    roots: List[Path] = []
    for part in (raw or "").split(";"):
        value = part.strip()
        if value:
            roots.append(Path(value).expanduser().resolve())
    return roots


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_local_image_path(path: str, allowed_roots: List[Path]) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise RuntimeError(f"Image file does not exist: {candidate}")
    if candidate.is_dir():
        raise RuntimeError(f"Image path is a directory: {candidate}")
    if candidate.suffix.lower() not in _IMAGE_EXTS:
        raise RuntimeError(f"Unsupported image extension: {candidate.suffix}")

    if allowed_roots:
        for root in allowed_roots:
            if _is_within(candidate, root):
                return candidate
        roots = ", ".join(str(root) for root in allowed_roots)
        raise RuntimeError(f"Path is outside allowed roots. Allowed roots: {roots}")

    return candidate


def _read_limited(resp: Any, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"Image exceeds max_image_bytes ({max_bytes})")
    return b"".join(chunks)


def _guess_mime_type(source: str, header_content_type: str = "") -> str:
    if header_content_type:
        return header_content_type.split(";")[0].strip().lower()
    guessed, _ = mimetypes.guess_type(source)
    return (guessed or "application/octet-stream").lower()


def _is_image_mime_type(mime_type: str) -> bool:
    return mime_type.startswith("image/")


def _image_size_from_bytes(data: bytes, mime_type: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)

        if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)

        if data.startswith(b"BM") and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(int(width)), abs(int(height))

        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8 " and len(data) >= 30:
                width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
                height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
                return int(width), int(height)
            if data[12:16] == b"VP8L" and len(data) >= 25:
                b0, b1, b2, b3 = data[21:25]
                width = 1 + (((b1 & 0x3F) << 8) | b0)
                height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
                return int(width), int(height)
            if data[12:16] == b"VP8X" and len(data) >= 30:
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return int(width), int(height)

        if data.startswith(b"\xff\xd8"):
            idx = 2
            while idx + 9 < len(data):
                if data[idx] != 0xFF:
                    idx += 1
                    continue
                marker = data[idx + 1]
                idx += 2
                if marker in {0xD8, 0xD9}:
                    continue
                if idx + 2 > len(data):
                    break
                seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
                if seg_len < 2 or idx + seg_len > len(data):
                    break
                if marker in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    height, width = struct.unpack(">HH", data[idx + 3 : idx + 7])
                    return int(width), int(height)
                idx += seg_len
    except Exception:
        return None, None

    try:
        from PIL import Image
        from io import BytesIO

        with Image.open(BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def _fetch_image_url(
    url: str,
    timeout_seconds: int,
    max_image_bytes: int,
    user_agent: str,
) -> Tuple[bytes, str, Dict[str, Any]]:
    req = Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "image/*,*/*;q=0.1"},
    )
    with urlopen(req, timeout=timeout_seconds) as resp:
        content_type = (resp.headers.get("Content-Type") or "").strip()
        data = _read_limited(resp, max_image_bytes)
        mime_type = _guess_mime_type(resp.geturl() or url, content_type)
        if not _is_image_mime_type(mime_type):
            raise RuntimeError(f"Not an image (Content-Type: {mime_type})")
        return data, mime_type, {
            "kind": "url",
            "source": url,
            "final_url": resp.geturl(),
            "status_code": int(getattr(resp, "status", 200)),
        }


def _fetch_image_file(path_str: str, max_image_bytes: int, allowed_roots: List[Path]) -> Tuple[bytes, str, Dict[str, Any]]:
    path = _validate_local_image_path(path_str, allowed_roots)
    size = path.stat().st_size
    if size > max_image_bytes:
        raise RuntimeError(f"Image exceeds max_image_bytes ({max_image_bytes})")
    data = path.read_bytes()
    mime_type = _guess_mime_type(str(path))
    if not _is_image_mime_type(mime_type):
        mime_type = "image/png"
    return data, mime_type, {"kind": "file", "source": str(path)}


def _fetch_image_data_url(data_url: str, max_image_bytes: int) -> Tuple[bytes, str, Dict[str, Any]]:
    match = re.match(
        r"^data:([^;,]+)(?:;charset=[^;,]+)?;base64,(.*)$",
        data_url,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise RuntimeError("Unsupported data URL (only base64-encoded data URLs are supported)")
    mime_type = (match.group(1) or "").strip().lower()
    if not _is_image_mime_type(mime_type):
        raise RuntimeError(f"Not an image data URL (mimeType: {mime_type})")
    data = base64.b64decode(match.group(2).strip(), validate=False)
    if len(data) > max_image_bytes:
        raise RuntimeError(f"Image exceeds max_image_bytes ({max_image_bytes})")
    return data, mime_type, {"kind": "data", "source": "data-url"}


def _load_image(args: Dict[str, Any], allowed_roots: List[Path]) -> Tuple[bytes, str, Dict[str, Any]]:
    source = str(args.get("source", "")).strip()
    if not source:
        raise RuntimeError("source is required")

    timeout_seconds = int(args.get("timeout_seconds", 20))
    max_image_bytes = int(args.get("max_image_bytes", 5_000_000))
    user_agent = str(args.get("user_agent") or _DEFAULT_USER_AGENT)

    if timeout_seconds < 1 or timeout_seconds > 120:
        raise RuntimeError("timeout_seconds must be between 1 and 120")
    if max_image_bytes < 1 or max_image_bytes > 25_000_000:
        raise RuntimeError("max_image_bytes must be between 1 and 25000000")

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return _fetch_image_url(source, timeout_seconds, max_image_bytes, user_agent)
    if parsed.scheme == "data":
        return _fetch_image_data_url(source, max_image_bytes)
    return _fetch_image_file(source, max_image_bytes, allowed_roots)


def _image_metadata(data: bytes, mime_type: str, source_meta: Dict[str, Any]) -> Dict[str, Any]:
    width, height = _image_size_from_bytes(data, mime_type)
    return {
        **source_meta,
        "mimeType": mime_type,
        "bytes": len(data),
        "width": width,
        "height": height,
    }


def _otsu_threshold(hist: List[int]) -> int:
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
    input_path: Path,
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

    image = Image.open(input_path).convert("L")
    if scale != 1:
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            resample=Image.Resampling.LANCZOS,
        )
    if autocontrast:
        image = ImageOps.autocontrast(image)
    if contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(float(contrast))
    if unsharp:
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=3))

    otsu = None
    if binarize:
        otsu = _otsu_threshold(image.histogram())
        image = image.point(lambda p: 255 if p > otsu else 0, mode="1")

    output_path = input_path.with_name(f"{input_path.stem}_pre.png")
    image.save(output_path)

    info: Dict[str, Any] = {
        "scale": scale,
        "autocontrast": autocontrast,
        "contrast": float(contrast),
        "unsharp": unsharp,
        "binarize": binarize,
        "ocr_input_path": str(output_path),
    }
    if otsu is not None:
        info["otsu_threshold"] = int(otsu)
    return output_path, info


def _tesseract_ocr_file(image_path: Path, lang: str, psm: Optional[int]) -> str:
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError("tesseract executable not found. Install Tesseract OCR and ensure it is on PATH.")

    args = [exe, str(image_path), "stdout"]
    if lang:
        args += ["-l", lang]
    if psm is not None:
        args += ["--psm", str(int(psm))]

    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"tesseract failed (code {proc.returncode}): {err or 'unknown error'}")
    return proc.stdout or ""


def _ocr_image(args: Dict[str, Any], allowed_roots: List[Path]) -> Dict[str, Any]:
    data, mime_type, source_meta = _load_image(args, allowed_roots)
    meta = _image_metadata(data, mime_type, source_meta)

    lang = str(args.get("lang", "eng")).strip()
    psm_raw = args.get("psm", 6)
    psm = int(psm_raw) if psm_raw is not None else None
    preprocess = bool(args.get("preprocess", True))
    preprocess_scale = int(args.get("preprocess_scale", 3))
    preprocess_autocontrast = bool(args.get("preprocess_autocontrast", True))
    preprocess_contrast = float(args.get("preprocess_contrast", 1.8))
    preprocess_unsharp = bool(args.get("preprocess_unsharp", True))
    preprocess_binarize = bool(args.get("preprocess_binarize", False))
    max_chars = int(args.get("max_chars", 30000))

    if max_chars < 1:
        raise RuntimeError("max_chars must be >= 1")
    if psm is not None and (psm < 0 or psm > 13):
        raise RuntimeError("psm must be between 0 and 13")

    ext = (mime_type.split("/", 1)[1] if "/" in mime_type else "png").split("+", 1)[0]
    if ext == "jpeg":
        ext = "jpg"

    temp_root = Path(os.environ.get("IMAGE_READER_TEMP_DIR") or (Path.cwd() / ".tmp" / "image-reader-temp"))
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dir_path = temp_root / f"image-reader-ocr-{os.getpid()}-{time.time_ns()}"
    temp_dir_path.mkdir(parents=True, exist_ok=False)
    temp_dir = str(temp_dir_path)
    try:
        input_path = Path(temp_dir) / f"input.{ext}"
        input_path.write_bytes(data)
        ocr_input = input_path
        preprocess_info: Optional[Dict[str, Any]] = None
        if preprocess:
            ocr_input, preprocess_info = _preprocess_image_for_ocr(
                input_path,
                scale=preprocess_scale,
                autocontrast=preprocess_autocontrast,
                contrast=preprocess_contrast,
                unsharp=preprocess_unsharp,
                binarize=preprocess_binarize,
            )

        text = _tesseract_ocr_file(ocr_input, lang=lang, psm=psm).strip()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    return {
        **meta,
        "ocr": {
            "engine": "tesseract",
            "lang": lang,
            "psm": psm,
            "preprocess": {
                "enabled": preprocess,
                "scale": preprocess_scale,
                "autocontrast": preprocess_autocontrast,
                "contrast": preprocess_contrast,
                "unsharp": preprocess_unsharp,
                "binarize": preprocess_binarize,
                **(preprocess_info or {}),
            },
            "chars": len(text),
            "truncated": truncated,
            "text": text,
        },
    }


def _tool_list() -> Dict[str, Any]:
    return {
        "tools": [
            {
                "name": "read_image",
                "description": "Read a local image, URL image, or image data URL and return it as an inline image block for model vision, plus compact metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Local image path, http/https image URL, or base64 data URL",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                            "default": 20,
                        },
                        "max_image_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 25000000,
                            "default": 5000000,
                        },
                        "user_agent": {
                            "type": "string",
                            "description": "Override HTTP User-Agent for URL images",
                        },
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "read_image_ocr",
                "description": "Avoid proxy/vision instability by OCRing an image locally with Tesseract and returning text to the model.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Local image path, http/https image URL, or base64 data URL",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                            "default": 20,
                        },
                        "max_image_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 25000000,
                            "default": 5000000,
                        },
                        "lang": {"type": "string", "default": "eng"},
                        "psm": {"type": "integer", "minimum": 0, "maximum": 13, "default": 6},
                        "preprocess": {"type": "boolean", "default": True},
                        "preprocess_scale": {"type": "integer", "minimum": 1, "default": 3},
                        "preprocess_autocontrast": {"type": "boolean", "default": True},
                        "preprocess_contrast": {"type": "number", "minimum": 0.1, "default": 1.8},
                        "preprocess_unsharp": {"type": "boolean", "default": True},
                        "preprocess_binarize": {"type": "boolean", "default": False},
                        "max_chars": {"type": "integer", "minimum": 1, "default": 30000},
                        "user_agent": {
                            "type": "string",
                            "description": "Override HTTP User-Agent for URL images",
                        },
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "get_image_info",
                "description": "Read image metadata only. Does not return the inline image block.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Local image path, http/https image URL, or base64 data URL",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                            "default": 20,
                        },
                        "max_image_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 25000000,
                            "default": 5000000,
                        },
                        "user_agent": {
                            "type": "string",
                            "description": "Override HTTP User-Agent for URL images",
                        },
                    },
                    "required": ["source"],
                },
            },
        ]
    }


def _handle_call(name: str, args: Dict[str, Any], allowed_roots: List[Path]) -> Dict[str, Any]:
    if name == "read_image":
        data, mime_type, source_meta = _load_image(args, allowed_roots)
        meta = _image_metadata(data, mime_type, source_meta)
        return {
            "content": [
                {"type": "text", "text": json.dumps(meta, ensure_ascii=False, indent=2)},
                {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime_type},
            ]
        }

    if name == "read_image_ocr":
        result = _ocr_image(args, allowed_roots)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

    if name == "get_image_info":
        data, mime_type, source_meta = _load_image(args, allowed_roots)
        meta = _image_metadata(data, mime_type, source_meta)
        return {"content": [{"type": "text", "text": json.dumps(meta, ensure_ascii=False, indent=2)}]}

    raise RuntimeError(f"Unknown tool: {name}")


def main() -> None:
    allowed_roots = _expand_roots(os.environ.get("IMAGE_ALLOWED_ROOTS", ""))
    server_name = "image-reader"
    server_version = "1.0.0"

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
        except HTTPError as err:
            if req_id is not None:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32010, "message": f"HTTP error {err.code}: {err.reason}"},
                    }
                )
        except URLError as err:
            if req_id is not None:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32011, "message": f"Network error: {err.reason}"},
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
