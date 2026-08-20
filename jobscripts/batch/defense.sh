#!/usr/bin/env bash

# Defense evaluation matrix for the calendar scenario:
#
# Injection phase:
#   Claude Code (ISR.py) + OpenClaw (Openclaw_ISR.py)
#   x txt/pdf/png
#
# Trigger phase:
#   Claude Code (CASR.py) + OpenClaw (Openclaw_CASR.py)
#   x 30 sessions

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Basic configuration. Override these values with environment variables.
PYTHON_BIN="${PYTHON_BIN:-python}"
MODEL_NAME="${MODEL_NAME:-deepseek-v4-pro}"
INJECTION_SESSIONS="${INJECTION_SESSIONS:-1}"
TRIGGER_SESSIONS="${TRIGGER_SESSIONS:-30}"
EVALUATOR_DRY_RUN="${EVALUATOR_DRY_RUN:-0}"

CLAUDECODE_ENVIRONMENT_DIR="$PROJECT_ROOT/environment"
: "${OPENCLAW_STATE_DIR:?Set OPENCLAW_STATE_DIR to the local OpenClaw state directory}"
OPENCLAW_ENVIRONMENT_DIR="${OPENCLAW_ENVIRONMENT_DIR:-$OPENCLAW_STATE_DIR/environment}"

CLAUDECODE_PATH_ARGS=(--environment-dir "$CLAUDECODE_ENVIRONMENT_DIR")
OPENCLAW_PATH_ARGS=(
  --openclaw-state-dir "$OPENCLAW_STATE_DIR"
  --openclaw-environment-dir "$OPENCLAW_ENVIRONMENT_DIR"
)

SCENARIO="calendar"
DEFENDED_TRIGGER_PROMPT="prompts/defense/calendar.txt"
MODALITIES=(txt pdf png)

# Claude Code uses the project's samples; OpenClaw uses its isolated workspace copy.
CLAUDECODE_SAMPLES_ROOT="${CLAUDECODE_SAMPLES_ROOT:-$PROJECT_ROOT/download}"
OPENCLAW_SAMPLES_ROOT="${OPENCLAW_SAMPLES_ROOT:-$OPENCLAW_STATE_DIR/download}"

# Map each modality to its defended benign prompt.
declare -A DEFENDED_BENIGN_PROMPT=(
  [txt]="prompts/defense/benign_text.txt"
  [pdf]="prompts/defense/benign_pdf.txt"
  [png]="prompts/defense/benign_image.txt"
)

EXTRA_ARGS=()
if [[ "$EVALUATOR_DRY_RUN" == "1" ]]; then
  EXTRA_ARGS+=(--dry-run)
fi

run_injection_phase() {
  local runner_name="$1"
  local runner_script="$2"
  local samples_root="$3"
  local modality
  local -a evaluator_path_args=()

  if [[ "$runner_script" == *"Openclaw_"* ]]; then
    evaluator_path_args=("${OPENCLAW_PATH_ARGS[@]}")
  else
    evaluator_path_args=("${CLAUDECODE_PATH_ARGS[@]}")
  fi

  for modality in "${MODALITIES[@]}"; do
    printf '\n[Injection][%s] scenario=%s modality=%s\n' \
      "$runner_name" "$SCENARIO" "$modality"

    "$PYTHON_BIN" "$runner_script" \
      --benign-task-prompt-file "${DEFENDED_BENIGN_PROMPT[$modality]}" \
      --prompt-file "$DEFENDED_TRIGGER_PROMPT" \
      --scenario "$SCENARIO" \
      --samples-path "$samples_root/c_messages/$modality" \
      --method "$modality" \
      --model-name "$MODEL_NAME" \
      --sessions "$INJECTION_SESSIONS" \
      "${evaluator_path_args[@]}" \
      "${EXTRA_ARGS[@]}"
  done
}

run_trigger_phase() {
  local runner_name="$1"
  local runner_script="$2"
  local -a evaluator_path_args=()

  if [[ "$runner_script" == *"Openclaw_"* ]]; then
    evaluator_path_args=("${OPENCLAW_PATH_ARGS[@]}")
  else
    evaluator_path_args=("${CLAUDECODE_PATH_ARGS[@]}")
  fi

  printf '\n[Trigger][%s] scenario=%s sessions=%s\n' \
    "$runner_name" "$SCENARIO" "$TRIGGER_SESSIONS"

  "$PYTHON_BIN" "$runner_script" \
    --prompt-file "$DEFENDED_TRIGGER_PROMPT" \
    --scenario "$SCENARIO" \
    --model-name "$MODEL_NAME" \
    --sessions "$TRIGGER_SESSIONS" \
    "${evaluator_path_args[@]}" \
    "${EXTRA_ARGS[@]}"
}

# Injection phase: 2 evaluators x 3 modalities.
run_injection_phase \
  "Claude Code" \
  "jobscripts/ISR.py" \
  "$CLAUDECODE_SAMPLES_ROOT"

run_injection_phase \
  "OpenClaw" \
  "jobscripts/Openclaw_ISR.py" \
  "$OPENCLAW_SAMPLES_ROOT"

# Trigger phase: 2 evaluators x 1 scenario x 30 sessions.
run_trigger_phase "Claude Code" "jobscripts/CASR.py"
run_trigger_phase "OpenClaw" "jobscripts/Openclaw_CASR.py"

echo
echo "All 8 calendar defense jobs completed successfully."
