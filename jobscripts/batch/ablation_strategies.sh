#!/usr/bin/env bash

# Injection-stage strategy ablation matrix for the calendar scenario:
#   2 evaluators (Claude Code/OpenClaw)
#   x 2 ablation dimensions (position/style)
#   x 2 sample groups per dimension
#   x 3 modalities (txt/pdf/png)
#   = 24 jobs

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

SCENARIO="calendar"
TRIGGER_PROMPT="prompts/trigger_phase_prompts/calendar.txt"
MODALITIES=(txt pdf png)

# Claude Code uses the project's samples; OpenClaw uses its isolated workspace copy.
CLAUDECODE_SAMPLES_ROOT="${CLAUDECODE_SAMPLES_ROOT:-$PROJECT_ROOT/download}"
OPENCLAW_SAMPLES_ROOT="${OPENCLAW_SAMPLES_ROOT:-$OPENCLAW_STATE_DIR/download}"

# Each entry is: ablation dimension | sample group.
ABLATION_GROUPS=(
  "position|cb_messages"
  "position|ct_messages"
  "style|ca_messages"
  "style|ci_messages"
)

# Map each modality to its standard injection-stage benign prompt.
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
  local output_root="$4"
  local dimension="$5"
  local sample_group="$6"
  local modality="$7"

  local samples_path="$samples_root/ablation_${dimension}/${sample_group}/${modality}"
  local output_dir="$output_root/${dimension}/${sample_group}"
  local -a evaluator_path_args=()

  if [[ "$runner_script" == *"Openclaw_"* ]]; then
    evaluator_path_args=("${OPENCLAW_PATH_ARGS[@]}")
  else
    evaluator_path_args=("${CLAUDECODE_PATH_ARGS[@]}")
  fi

  printf '\n[%s] dimension=%s group=%s modality=%s\n' \
    "$runner_name" "$dimension" "$sample_group" "$modality"

  "$PYTHON_BIN" "$runner_script" \
    --benign-task-prompt-file "${BENIGN_PROMPT_BY_MODALITY[$modality]}" \
    --prompt-file "$TRIGGER_PROMPT" \
    --scenario "$SCENARIO" \
    --samples-path "$samples_path" \
    --method "$modality" \
    --model-name "$MODEL_NAME" \
    --sessions "$SESSIONS" \
    --output-dir "$output_dir" \
    "${evaluator_path_args[@]}" \
    "${EXTRA_ARGS[@]}"
}

run_all_ablations() {
  local runner_name="$1"
  local runner_script="$2"
  local samples_root="$3"
  local output_root="$4"
  local entry dimension sample_group modality

  for entry in "${ABLATION_GROUPS[@]}"; do
    IFS='|' read -r dimension sample_group <<< "$entry"
    for modality in "${MODALITIES[@]}"; do
      run_one \
        "$runner_name" \
        "$runner_script" \
        "$samples_root" \
        "$output_root" \
        "$dimension" \
        "$sample_group" \
        "$modality"
    done
  done
}

# Claude Code: 2 dimensions x 2 sample groups x 3 modalities.
run_all_ablations \
  "Claude Code" \
  "jobscripts/ISR.py" \
  "$CLAUDECODE_SAMPLES_ROOT" \
  "runs/AblationStrategies/ClaudecodeIsr"

# OpenClaw: 2 dimensions x 2 sample groups x 3 modalities.
run_all_ablations \
  "OpenClaw" \
  "jobscripts/Openclaw_ISR.py" \
  "$OPENCLAW_SAMPLES_ROOT" \
  "runs/AblationStrategies/OpenclawIsr"

echo
echo "All 24 strategy ablation jobs completed successfully."
