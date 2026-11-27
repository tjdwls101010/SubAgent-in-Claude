---
name: delegate
description: "Start background workers and manage them like subagents: a handle at once, progress you can read, stopping and resuming them, and waiting for and collecting text or JSON-schema results. Engines: Claude through headless Claude Code. Use when work should go to another worker you can watch and collect, or when a long task should run while you do something else. Triggers include: delegate, 위임, 작업자, claude worker, 클로드 작업자, 클로드한테 시켜, 백그라운드. Not for Claude Code's built-in Agent tool, subagents or agent teams, claude --bg, or general questions about the Claude API."
allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" *)
---

# Workers on several engines as managed subagents

Call it as `uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" <command> …`, written out in full on one line: the pre-approval matches the command text, so a path kept in a shell variable, a `$(…)` or a pipe added to the call, or a command continued with `\` asks for permission every time. Replies are made to be read as printed. `--help` lists the commands, and `<command> --help` owns every flag, default, refusal, output field and exit code.

A new run names its engine, model and effort; nothing has a default.
