# Security

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting on this repository (Security → Report a vulnerability), not in a public issue. Include the version (`git describe` or the plugin version), the command you ran and what it did.

## What the skill trusts

- Workers run as you, on your Mac, with your Codex and Claude Code logins. A worker can do whatever its profile allows: `read-only` and `write` run inside each engine's sandbox, `full` does not (Codex runs with no sandbox; Claude Code runs in auto mode, where a classifier reviews each call).
- A Claude worker inherits your whole Claude Code environment — CLAUDE.md files, skills, hooks, plugins, MCP servers. Hooks, MCP tools and web fetches run outside the sandbox under your own permission rules, on every profile.
- The registry (`.delegate-runs/` in the repository's main checkout) holds prompts, events and results in plain files readable by your user.
- `schedule` commits the prompt to the repository's default branch and runs it on a self-hosted runner on this Mac. In a public repository anyone's pull request could run code on that runner, so `schedule` refuses a public repository unless `--public-ok` is given.
