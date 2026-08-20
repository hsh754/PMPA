#!/usr/bin/env python3
"""
Compatibility wrapper for the OpenClaw ISR evaluator.

The implementation lives in eval/openclaw_injection/main.py.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.openclaw_injection.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())


"""
python jobscripts/Openclaw_ISR.py \
  --benign-task-prompt-file prompts/benign_task_prompt.txt \
  --prompt-file prompts\\email.txt \
  --samples-path D:\\openclaw\\media\\inbound\\download\\e_messages\\0707\\txt \
  --method txt \
  --model-name qwen3-max \
  --scenario email \
  --utility-clean-run-dir runs\\utility_baseline\\deepseek-v4-flash\\txt\\20260630-161038 \
  --sessions 1

python jobscripts/Openclaw_ISR.py \
  --benign-task-prompt-file prompts/benign_task_prompt.txt \
  --prompt-file prompts\\email.txt \
  --scenario email \
  --samples-path D:\\openclaw\\media\\inbound\\download\\e_messages\\0707\\pdf \
  --method pdf \
  --model-name qwen3-max\
  --utility-clean-run-dir runs\\utility_baseline\\deepseek-v4-flash\\txt\\20260706-151002 \
  --sessions 1

python jobscripts/Openclaw_ISR.py \
  --benign-task-prompt-file prompts/benign_task_prompt.txt \
  --prompt-file prompts\\email.txt \
  --scenario email \
  --samples-path D:\\openclaw\\media\\inbound\\download\\e_messages\\0707\\png \
  --method png \
  --model-name qwen3-max \
  --utility-clean-run-dir runs\\utility_baseline\\deepseek-v4-flash\\txt\\20260706-151002 \
  --sessions 1
"""
