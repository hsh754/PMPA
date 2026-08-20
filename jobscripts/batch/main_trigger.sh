#!/usr/bin/env bash

# Evaluation matrix:
#   Claude Code (CASR.py) + OpenClaw (Openclaw_CASR.py)
#   x calendar/docs/form/email
#   x 30 sessions per scenario

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Basic configuration. Override these values with environment variables.
PYTHON_BIN="${PYTHON_BIN:-python}"
MODEL_NAME="${MODEL_NAME:-deepseek-v4-pro}"
SESSIONS="${SESSIONS:-30}"
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

EXTRA_ARGS=()
if [[ "$EVALUATOR_DRY_RUN" == "1" ]]; then
  EXTRA_ARGS+=(--dry-run)
fi

run_one_scenario() {
  local runner_name="$1"
  local runner_script="$2"
  local scenario="$3"
  local trigger_prompt="prompts/trigger_phase_prompts/${scenario}.txt"
  local -a evaluator_path_args=()

  if [[ "$runner_script" == *"Openclaw_"* ]]; then
    evaluator_path_args=("${OPENCLAW_PATH_ARGS[@]}")
  else
    evaluator_path_args=("${CLAUDECODE_PATH_ARGS[@]}")
  fi

  printf '\n[%s] scenario=%s sessions=%s\n' \
    "$runner_name" "$scenario" "$SESSIONS"

  "$PYTHON_BIN" "$runner_script" \
    --prompt-file "$trigger_prompt" \
    --scenario "$scenario" \
    --model-name "$MODEL_NAME" \
    --sessions "$SESSIONS" \
    "${evaluator_path_args[@]}" \
    "${EXTRA_ARGS[@]}"
}

run_all_scenarios() {
  local runner_name="$1"
  local runner_script="$2"
  local scenario

  for scenario in "${SCENARIOS[@]}"; do
    run_one_scenario "$runner_name" "$runner_script" "$scenario"
  done
}

# Claude Code: 4 scenarios x 30 sessions.
run_all_scenarios "Claude Code" "jobscripts/CASR.py"

# OpenClaw: 4 scenarios x 30 sessions.
run_all_scenarios "OpenClaw" "jobscripts/Openclaw_CASR.py"

echo
echo "All 8 trigger jobs completed successfully."
