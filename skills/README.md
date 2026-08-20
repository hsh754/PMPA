# Skills installation

[`skills/`](./) is the canonical source directory. `.claude/skills/` and OpenClaw's
`workspace/skills/` are local installation targets.

Run these commands from the repository root.

## Install

Claude Code installs the skills into `.claude/skills/` under the current repository root:

```bash
python jobscripts/install_skills.py --target claude
```

For OpenClaw, provide the local OpenClaw state directory. The skills will be installed into its
`workspace/skills/` directory:

```bash
python jobscripts/install_skills.py --target openclaw --openclaw-state-dir "/path/to/openclaw"
```

Use `--dry-run` to inspect destinations and `--skill NAME` to select individual skills.
