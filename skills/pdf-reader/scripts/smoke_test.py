#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


def _call_server(server_py: Path, tool_name: str, arguments: dict) -> dict:
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}},
    ]
    proc = subprocess.run(
        [sys.executable, str(server_py)],
        input="\n".join(json.dumps(r) for r in reqs) + "\n",
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        try:
            msg = json.loads(ln)
        except Exception:
            continue
        if msg.get("id") == 2:
            return msg.get("result", {})
    raise RuntimeError("No tools/call response found.")


def main() -> int:
    here = Path(__file__).resolve().parent
    server_py = here / "server.py"
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {Path(sys.argv[0]).name} PATH_TO_TEXT_PDF")
    pdf_path = Path(sys.argv[1]).expanduser().resolve()

    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    info_result = _call_server(
        server_py,
        "get_pdf_info",
        {"path": str(pdf_path)},
    )
    info_content = info_result.get("content") or []
    info_text = (info_content[0].get("text") if info_content else "") or ""
    info = json.loads(info_text)
    if int(info.get("pages_total", 0)) < 1:
        raise SystemExit("FAIL: expected at least one PDF page.")
    print("PASS: PDF metadata was read")

    read_result = _call_server(
        server_py,
        "read_pdf",
        {
            "path": str(pdf_path),
            "start_page": 1,
            "end_page": 1,
            "max_chars": 2000,
        },
    )
    read_content = read_result.get("content") or []
    read_text = (read_content[0].get("text") if read_content else "") or ""
    data = json.loads(read_text)
    if not data.get("text", "").strip():
        raise SystemExit("FAIL: expected non-empty text-layer content.")
    print("PASS: PDF text layer was read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
