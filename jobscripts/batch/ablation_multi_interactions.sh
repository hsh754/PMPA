#!/usr/bin/env bash

# Trigger-stage multi-interaction ablation matrix:
#   2 evaluators (Claude Code/OpenClaw)
#   x 4 scenarios (calendar/docs/form/email)
#   x 4 normal-task counts before the trigger (0/1/3/5)
#   x 10 sessions per group
#   = 32 jobs

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Basic configuration. Override these values with environment variables.
PYTHON_BIN="${PYTHON_BIN:-python}"
MODEL_NAME="${MODEL_NAME:-deepseek-v4-pro}"
SESSIONS="${SESSIONS:-10}"
EVALUATOR_DRY_RUN="${EVALUATOR_DRY_RUN:-0}"

CLAUDECODE_ENVIRONMENT_DIR="$PROJECT_ROOT/environment"
: "${OPENCLAW_STATE_DIR:?Set OPENCLAW_STATE_DIR to the local OpenClaw state directory}"
OPENCLAW_ENVIRONMENT_DIR="${OPENCLAW_ENVIRONMENT_DIR:-$OPENCLAW_STATE_DIR/environment}"

CLAUDECODE_PATH_ARGS=(--environment-dir "$CLAUDECODE_ENVIRONMENT_DIR")
OPENCLAW_PATH_ARGS=(
  --openclaw-state-dir "$OPENCLAW_STATE_DIR"
  --openclaw-environment-dir "$OPENCLAW_ENVIRONMENT_DIR"
)

SCENARIOS=(calendar docs form email)
NORMAL_TASK_COUNTS=(0 1 3 5)

# Normal tasks use clean text samples before the scenario trigger is sent.
BENIGN_PROMPT="prompts/injection_phase_prompts/benign_text.txt"
CLAUDECODE_SAMPLES_ROOT="${CLAUDECODE_SAMPLES_ROOT:-$PROJECT_ROOT/download}"
OPENCLAW_SAMPLES_ROOT="${OPENCLAW_SAMPLES_ROOT:-$OPENCLAW_STATE_DIR/download}"
CLAUDECODE_CLEAN_SAMPLES="${CLAUDECODE_CLEAN_SAMPLES:-$CLAUDECODE_SAMPLES_ROOT/ablation-multi-interaction/clean/txt}"
OPENCLAW_CLEAN_SAMPLES="${OPENCLAW_CLEAN_SAMPLES:-$OPENCLAW_SAMPLES_ROOT/ablation-multi-interaction/clean/txt}"

EXTRA_ARGS=()
if [[ "$EVALUATOR_DRY_RUN" == "1" ]]; then
  EXTRA_ARGS+=(--dry-run)
fi

run_one() {
  local runner_name="$1"
  local runner_script="$2"
  local clean_samples="$3"
  local output_root="$4"
  local scenario="$5"
  local normal_task_count="$6"

  local trigger_prompt="prompts/trigger_phase_prompts/${scenario}.txt"
  local output_dir="$output_root/normal_tasks_${normal_task_count}"
  local -a benign_args=()
  local -a evaluator_path_args=()

  if [[ "$runner_script" == *"Openclaw_"* ]]; then
    evaluator_path_args=("${OPENCLAW_PATH_ARGS[@]}")
  else
    evaluator_path_args=("${CLAUDECODE_PATH_ARGS[@]}")
  fi

  # The zero-task group is the baseline and needs no clean sample input.
  if (( normal_task_count > 0 )); then
    benign_args=(
      --benign-task-prompt-file "$BENIGN_PROMPT"
      --cleansample-dir "$clean_samples"
      --method txt
    )
  fi

  printf '\n[%s] scenario=%s normal_tasks=%s sessions=%s\n' \
    "$runner_name" "$scenario" "$normal_task_count" "$SESSIONS"

  "$PYTHON_BIN" "$runner_script" \
    --prompt-file "$trigger_prompt" \
    --scenario "$scenario" \
    --model-name "$MODEL_NAME" \
    --benign-count "$normal_task_count" \
    --sessions "$SESSIONS" \
    --output-dir "$output_dir" \
    "${benign_args[@]}" \
    "${evaluator_path_args[@]}" \
    "${EXTRA_ARGS[@]}"
}

run_all_ablations() {
  local runner_name="$1"
  local runner_script="$2"
  local clean_samples="$3"
  local output_root="$4"
  local scenario normal_task_count

  for scenario in "${SCENARIOS[@]}"; do
    for normal_task_count in "${NORMAL_TASK_COUNTS[@]}"; do
      run_one \
        "$runner_name" \
        "$runner_script" \
        "$clean_samples" \
        "$output_root" \
        "$scenario" \
        "$normal_task_count"
    done
  done
}

# Claude Code: 4 scenarios x 4 normal-task counts.
run_all_ablations \
  "Claude Code" \
  "jobscripts/CASR.py" \
  "$CLAUDECODE_CLEAN_SAMPLES" \
  "runs/AblationMultiInteractions/ClaudecodeCasr"

# OpenClaw: 4 scenarios x 4 normal-task counts.
run_all_ablations \
  "OpenClaw" \
  "jobscripts/Openclaw_CASR.py" \
  "$OPENCLAW_CLEAN_SAMPLES" \
  "runs/AblationMultiInteractions/OpenclawCasr"

echo
echo "All 32 multi-interaction ablation jobs completed successfully."
