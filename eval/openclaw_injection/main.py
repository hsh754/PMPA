#!/usr/bin/env python3
from __future__ import annotations

try:
    from .injection_cli import (
        build_config,
        parse_args,
        print_run_header,
        run_dry_run,
        validate_args,
    )
    from .modes import run_isr_mode, run_normal_mode
except ImportError:  # pragma: no cover - supports `python eval/openclaw_injection/main.py`.
    from injection_cli import (
        build_config,
        parse_args,
        print_run_header,
        run_dry_run,
        validate_args,
    )
    from modes import run_isr_mode, run_normal_mode


def main() -> int:
    args = parse_args()
    validation_error = validate_args(args)
    if validation_error is not None:
        return validation_error

    built = build_config(args)
    if built is None:
        return 2
    config, prompt_template, benign_task_prompt_template, samples = built
    print_run_header(args, config, benign_task_prompt_template, samples)

    if args.dry_run:
        return run_dry_run(args, config, samples, benign_task_prompt_template is not None)

    config.run_dir.mkdir(parents=True, exist_ok=True)
    if benign_task_prompt_template is not None:
        return run_isr_mode(
            args.sessions,
            benign_task_prompt_template,
            prompt_template,
            args.snapshot_delay_seconds,
            samples,
            args.method,
            config,
            not args.no_summary_json,
            args.scenario,
            args.utility_clean_run_dir,
        )

    return run_normal_mode(
        args.sessions,
        prompt_template,
        samples,
        args.method,
        config,
        not args.no_summary_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())

"""
python eval/openclaw_injection/main.py \
  --prompt "Please answer the following question" \
  --benign-task-prompt-file ./prompts/benign_task_prompt.txt \
  --samples-path outputs/embedded_samples_1/test/txt \
  --method txt \
  --model-name deepseek-v4-pro \
"""
