---
name: image-reader
description: Local image reader migrated from the Claude Code image-reader MCP server. Use when Claude Code, Openclaw, or this project needs to inspect image metadata, pass a local image as an inline vision block, OCR screenshots/posters/UI images, or answer questions from PNG/JPEG/WebP/GIF/BMP/TIFF images without relying on external image services.
---

# Image Reader

Use this skill for local image reading and OCR tasks in this project.

`<skill-directory>` below means the absolute directory containing this `SKILL.md`.
Resolve and replace that placeholder before running a command; do not assume a Claude or OpenClaw install location.

```bash
python "<skill-directory>/scripts/cli.py" <command> [options]
```

The original MCP server logic is preserved in `scripts/server.py`. It can still run as a JSON-RPC MCP server over stdin/stdout:

```bash
python "<skill-directory>/scripts/server.py"
```

## Commands

Read image metadata only:

```bash
python "<skill-directory>/scripts/cli.py" info path/to/image.png
```

Return metadata plus an inline image content block:

```bash
python "<skill-directory>/scripts/cli.py" read path/to/image.png
```

OCR an image locally with Tesseract:

```bash
python "<skill-directory>/scripts/cli.py" ocr path/to/image.png
python "<skill-directory>/scripts/cli.py" ocr path/to/image.png --lang eng --psm 6 --preprocess true
```

Supported local image extensions are `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, and `.tiff`.

The original MCP logic also supports `http/https` image URLs and base64 image data URLs. Prefer local files unless the user explicitly asks to read a URL.

## Notes

OCR uses `tesseract` on `PATH`; `--preprocess true` also requires Python `Pillow`.

Temporary OCR files default to `.tmp/image-reader-temp`.
Override with `IMAGE_READER_TEMP_DIR`.

Set `IMAGE_ALLOWED_ROOTS` to a semicolon-separated allowlist for local image reads.

For text-only OpenClaw models, prefer `ocr`; use `read` only when the model can consume inline images.
