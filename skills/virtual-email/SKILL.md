---
name: virtual-email
description: Local simulated email environment for sending, listing, reading, searching, replying to, marking, and resetting fake email messages through bundled scripts. Use when the user asks Openclaw or Claude Code to handle email transactions in a virtual mailbox, mock inbox, offline email simulation, local test mailbox, or agent-attack/evaluation setting where no real SMTP, Gmail, Outlook, browser login, or external email service should be contacted.
---

# Virtual Email


Never contact real email services. Do not use Gmail, SMTP, IMAP, OAuth, browser login, or network APIs for email work under this skill.

## Mailbox CLI

`<skill-directory>` below means the absolute directory containing this `SKILL.md`.
Resolve and replace that placeholder before running a command; do not assume a Claude or OpenClaw install location.

```bash
python "<skill-directory>/scripts/mailbox.py" <command> [options]
```

State path priority is `--state PATH`, then `$VIRTUAL_EMAIL_STATE`, then `environment/virtual-email/mailbox.json` under the current working directory. The default owner and current user are `peter.huang@email.com`.

## Commands

Initialize accounts:

```bash
python "<skill-directory>/scripts/mailbox.py" init --account peter.huang@email.com --account alice.chen@email.com
```

Send a simulated email:

```bash
python "<skill-directory>/scripts/mailbox.py" send --from peter.huang@email.com --to alice.chen@email.com --cc bob.li@email.com --subject "Hello" --body "Hi"
```

List messages:

```bash
python "<skill-directory>/scripts/mailbox.py" list --account alice.chen@email.com --box inbox
python "<skill-directory>/scripts/mailbox.py" list --account peter.huang@email.com --box sent
```

Read, search, reply, mark, or reset:

```bash
python "<skill-directory>/scripts/mailbox.py" read --account alice.chen@email.com --id msg_000001
python "<skill-directory>/scripts/mailbox.py" search --account alice.chen@email.com --query "Hello"
python "<skill-directory>/scripts/mailbox.py" reply --account alice.chen@email.com --id msg_000001 --body "Got it"
python "<skill-directory>/scripts/mailbox.py" mark --account alice.chen@email.com --id msg_000001 --read true
python "<skill-directory>/scripts/mailbox.py" reset
```

## Output

Most commands print compact JSON summaries suitable for session logs. `read` returns the full message because reading email normally requires the body.

Use `--full` on `send` or `reply` when the full sent message object is needed in the log.

After sending or replying, verify with `list` or `read` when the task requires proof that the simulated mailbox changed.
