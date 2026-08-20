#!/usr/bin/env bash

# Evaluation matrix:
#   Claude Code (ISR.py) + OpenClaw (Openclaw_ISR.py)
#   × calendar/docs/form/email
#   × txt/pdf/png

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Basic configuration. Override these values with environment variables.
PYTHON_BIN="${PYTHON_BIN:-python}"
MODEL_NAME="${MODEL_NAME:-deepseek-v4-pro}"
SESSIONS="${SESSIONS:-1}"
EVALUATOR_DRY_RUN="${EVALUATOR_DRY_RUN:-0}"

CLAUDECODE_ENVIRONMENT_DIR="$PROJECT_ROOT/environment"
: "${OPENCLAW_STATE_DIR:?Set OPENCLAW_STATE_DIR to the local OpenClaw state directory}"
OPENCLAW_ENVIRONMENT_DIR="${OPENCLAW_ENVIRONMENT_DIR:-$OPENCLAW_STATE_DIR/environment}"

CLAUDECODE_PATH_ARGS=(--environment-dir "$CLAUDECODE_ENVIRONMENT_DIR")
OPENCLAW_PATH_ARGS=(
  --openclaw-state-dir "$OPENCLAW_STATE_DIR"
  --openclaw-environment-dir "$OPENCLAW_ENVIRONMENT_DIR"
)

# Claude Code uses the project's samples; OpenClaw uses its isolated workspace copy.
CLAUDECODE_SAMPLES_ROOT="${CLAUDECODE_SAMPLES_ROOT:-$PROJECT_ROOT/download}"
OPENCLAW_SAMPLES_ROOT="${OPENCLAW_SAMPLES_ROOT:-$OPENCLAW_STATE_DIR/download}"

SCENARIOS=(calendar docs form email)
MODALITIES=(txt pdf png)

# Map each trigger scenario to its sample directory.
declare -A SAMPLE_DIR_BY_SCENARIO=(
  [calendar]="c_messages"
  [docs]="d_messages"
  [form]="f_messages"
  [email]="e_messages"
)

# Map each modality to its benign prompt.
declare -A BENIGN_PROMPT_BY_MODALITY=(
  [txt]="prompts/injection_phase_prompts/benign_text.txt"
  [pdf]="prompts/injection_phase_prompts/benign_pdf.txt"
  [png]="prompts/injection_phase_prompts/benign_image.txt"
)

EXTRA_ARGS=()
if [[ "$EVALUATOR_DRY_RUN" == "1" ]]; then
  EXTRA_ARGS+=(--dry-run)
fi

run_one() {
  local runner_name="$1"
  local runner_script="$2"
  local samples_root="$3"
  local scenario="$4"
  local modality="$5"

  local benign_prompt="${BENIGN_PROMPT_BY_MODALITY[$modality]}"
  local trigger_prompt="prompts/trigger_phase_prompts/${scenario}.txt"
  local samples_path="$samples_root/${SAMPLE_DIR_BY_SCENARIO[$scenario]}/$modality"
  local -a evaluator_path_args=()

  if [[ "$runner_script" == *"Openclaw_"* ]]; then
    evaluator_path_args=("${OPENCLAW_PATH_ARGS[@]}")
  else
    evaluator_path_args=("${CLAUDECODE_PATH_ARGS[@]}")
  fi

  printf '\n[%s] scenario=%s modality=%s\n' \
    "$runner_name" "$scenario" "$modality"

  "$PYTHON_BIN" "$runner_script" \
    --benign-task-prompt-file "$benign_prompt" \
    --prompt-file "$trigger_prompt" \
    --scenario "$scenario" \
    --samples-path "$samples_path" \
    --method "$modality" \
    --model-name "$MODEL_NAME" \
    --sessions "$SESSIONS" \
    "${evaluator_path_args[@]}" \
    "${EXTRA_ARGS[@]}"
}

run_all_scenarios() {
  local runner_name="$1"
  local runner_script="$2"
  local samples_root="$3"
  local scenario modality

  for scenario in "${SCENARIOS[@]}"; do
    for modality in "${MODALITIES[@]}"; do
      run_one "$runner_name" "$runner_script" "$samples_root" \
        "$scenario" "$modality"
    done
  done
}

# Claude Code: 4 scenarios x 3 input modalities.
run_all_scenarios \
  "Claude Code" \
  "jobscripts/ISR.py" \
  "$CLAUDECODE_SAMPLES_ROOT"

# OpenClaw: 4 scenarios x 3 input modalities.
run_all_scenarios \
  "OpenClaw" \
  "jobscripts/Openclaw_ISR.py" \
  "$OPENCLAW_SAMPLES_ROOT"

echo
echo "All 24 injection jobs completed successfully."
