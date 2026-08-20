#!/usr/bin/env python3
"""
Compatibility wrapper for the ISR evaluator.

The implementation lives in eval/injection/main.py.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.injection.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())


"""
python jobscripts/ISR.py \
  --benign-task-prompt-file prompts/benign_task_prompt.txt \
  --prompt-file prompts\\form.txt \
  --scenario form \
  --samples-path download\\c_messages\\0707\\txt \
  --method txt \
  --model-name deepseek-v4-flash  \
  --utility-clean-run-dir runs\\utility_baseline\\deepseek-v4-flash\\txt\\20260706-151002 \
  --sessions 1

python jobscripts/ISR.py \
  --benign-task-prompt-file prompts/benign_task_prompt.txt \
  --prompt-file prompts\\email.txt \
  --scenario calendar \
  --samples-path download\\e_messages\\0707\\pdf \
  --method pdf \
  --model-name deepseek-v4-flash \
  --utility-clean-run-dir runs\\utility_baseline\\deepseek-v4-flash\\txt\\20260706-151002 \
  --sessions 1

python jobscripts/ISR.py \
  --benign-task-prompt-file prompts/benign_task_prompt.txt \
  --prompt-file prompts\\email.txt \
  --scenario email \
  --samples-path download\\e_messages\\0707\\png \
  --method png \
  --model-name qwen3-max \
  --utility-clean-run-dir runs\\utility_baseline\\deepseek-v4-flash\\txt\\20260706-151002 \
  --sessions 1
"""