---
name: delegate
description: "Start background workers on two engines — GPT through the local Codex CLI, Claude through headless Claude Code — and manage them like subagents: a handle at once, progress you can read, stopping and resuming them, and waiting for and collecting text or JSON-schema results. Use when work should go to GPT or to another Claude session you can watch and collect, when an independent model should review or verify something, or when a long task should run while you do something else. Triggers include: delegate, 위임, 작업자, codex, 코덱스, 코덱스로, 코덱스한테, GPT한테, GPT에게 시켜, claude worker, 클로드 작업자, 클로드한테 시켜, 백그라운드, 교차 검증. Not for Claude Code's built-in Agent tool, subagents or agent teams, claude --bg, Codex Cloud, codex mcp-server or app-server, or general questions about GPT, OpenAI or the Claude API."
allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" *)
---

# Workers on several engines as managed subagents

Call it as `uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" <command> …`, written out in full on one line: the pre-approval matches the command text, so a path kept in a shell variable, a `$(…)` or a pipe added to the call, or a command continued with `\` asks for permission every time. Replies are made to be read as printed. `--help` lists the commands, and `<command> --help` owns every flag, default, refusal, output field and exit code.

A new run names its engine, model and effort; nothing has a default.
