# Skills installation

The `skills/` directory contains the skills used in our evaluations.
Before running the evaluations, manually copy each skill folder into
the corresponding agent's skill directory.

Copy the complete folders, including `SKILL.md`, `scripts/`, and any
other supporting files. The provided skills are:

- `image-reader`
- `pdf-reader`
- `virtual-calendar`
- `virtual-docs`
- `virtual-email`
- `virtual-form`

## Claude Code

Copy these folders into `.claude/skills/` under the PMPA repository root.
Create the destination directory if it does not exist.

For example, `skills/virtual-email/` should be copied to
`.claude/skills/virtual-email/`, so that its skill file is located at:

```text
<PMPA>/.claude/skills/virtual-email/SKILL.md
```

Run Claude Code from the PMPA repository root when performing evaluations.

## OpenClaw

Copy the same skill folders into the `skills/` directory of the workspace
used by your evaluation agent.

For the default workspace, the destination is:

```text
~/.openclaw/workspace/skills/
```

When using a custom `OPENCLAW_STATE_DIR` with the default workspace layout,
the destination is:

```text
<OPENCLAW_STATE_DIR>/workspace/skills/
```

For example, the installed email skill should be located at:

```text
<OPENCLAW_STATE_DIR>/workspace/skills/virtual-email/SKILL.md
```

If your agent uses a separately configured workspace, use that workspace's
`skills/` directory instead.

Copy the individual skill folders directly into the destination; avoid
creating an extra `skills/skills/` directory.
