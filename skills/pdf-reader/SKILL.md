---
name: pdf-reader
description: Local PDF reader for text-layer PDF files. Use when Claude Code or OpenClaw needs to inspect metadata, summarize, answer questions from, or extract text from local PDFs without relying on a browser or external PDF service.
---

# PDF Reader

Use this skill for local text PDF reading tasks in this project.

`<skill-directory>` below means the absolute directory containing this `SKILL.md`.
Resolve and replace that placeholder before running a command; do not assume a Claude or OpenClaw install location.

```bash
python "<skill-directory>/scripts/cli.py" <command> [options]
```

## Commands

Read PDF metadata:

```bash
python "<skill-directory>/scripts/cli.py" info path/to/file.pdf
```

Extract normal PDF text-layer content:

```bash
python "<skill-directory>/scripts/cli.py" read path/to/file.pdf
python "<skill-directory>/scripts/cli.py" read path/to/file.pdf --start-page 1 --end-page 3 --max-chars 30000
```

## Notes

Use `read` for normal text-layer PDFs.

Set `PDF_ALLOWED_ROOTS` to a semicolon-separated allowlist if local PDF reads need to be restricted.

## Dependencies

`read` and `info` require Python package `pypdf`.
