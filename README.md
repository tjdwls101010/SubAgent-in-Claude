# Agentic Delegate

**Run background workers on two engines — GPT through the Codex CLI, Claude through headless Claude Code — as managed subagents from inside Claude Code.**

[Overview](#1-overview) · [Features](#2-features) · [Quick Start](#3-quick-start) · [Usage](#4-usage) · [Scheduling](#5-scheduling) · [Project Status](#6-project-status)

## 1. Overview

Claude Code's own subagents run inside the session that started them: the orchestrator cannot look at a worker's progress, follow several separately, or hand work to a model of another family. The `delegate` skill gives an orchestrating Claude a handle on workers of two engines. It starts the installed `codex` CLI (`codex exec`) or `claude` CLI (`claude -p` with stream-json) under a detached supervisor and answers at once with a run id. From then on the orchestrator can read a compact log, wait for the result with one call that also returns early when the worker needs attention, steer a Claude worker with a message while it runs, stop and resume either kind, run several of both engines as one group, see each engine's models and usage limits, and schedule a Claude worker to run unattended.

A Claude worker inherits your whole Claude Code environment — CLAUDE.md files, skills, hooks, plugins, MCP servers, memory — under one of three permission profiles. A GPT worker runs isolated from your Codex config, in Codex's own sandbox under the same three profile names. There is no default engine, model or effort: every new run names all three.

## 2. Features

- **Start, then a handle.** `start --engine codex|claude --model M --effort E` returns a `run_id`, the engine's own id (a Codex thread or Claude session), and the exact `result --wait` command to run in the background.
- **Profiles.** `read-only` reads anything and runs tests and builds in a sandbox that can write only temporary files and caches; `write` changes files in its directory; `full` is no sandbox (Codex) or Claude Code's auto mode (Claude).
- **Waiting that notices trouble.** `result --wait` returns when the run ends, or early on attention: a worker silent for 10 minutes on either engine; for Claude also API retries, a rejected rate limit, or a worker still running after its answer. A group wait also returns when a member fails.
- **Steering.** `send` writes a message into a running Claude worker (`--interrupt` ends its turn); a GPT run is waited for, or stopped and resumed.
- **Resume with the same rules.** `resume` re-sends the profile, model, effort and schema every turn. A conversation does not cross engines; the other engine starts fresh in the same directory.
- **Mixed groups and checkouts.** `batch` starts several workers of either engine as one named group; `--worktree` gives each fresh writer a checkout of your local HEAD; `clean` removes them only when that loses nothing.
- **Models and limits.** `models` lists what each engine offers now, with its efforts and the account's usage limits; a start is refused up front only when a fresh reading shows the limit used up, and a run the limit ends is `quota_exhausted` with when it resets and the other engine's start.
- **Structured results.** `--schema` makes the answer JSON of a given shape.
- **Scheduling.** `schedule --engine claude` runs a Claude worker unattended at a set time, once or on a cron, on this Mac through the repository's self-hosted runner, with GitHub Actions as its record.
- **Diagnosis.** `doctor` checks each engine's CLI, version, login and sandbox, the registry, and whether this project is ready for scheduling, without starting a worker.

## 3. Quick Start

Requirements: macOS (Linux is untested), [`uv`](https://docs.astral.sh/uv/), git, and at least one engine: Claude Code 2.1.314 or newer logged in with your claude.ai subscription, and/or the Codex CLI 0.122.0 or newer logged in with ChatGPT (0.160.0 or newer for a read-only GPT worker that runs tests). Scheduling also needs [`gh`](https://cli.github.com/) and a self-hosted runner (see [Scheduling](#5-scheduling)).

Install as a plugin:

```bash
claude plugin marketplace add tjdwls101010/Agentic-Delegate
claude plugin install delegate@agentic-delegate
```

Or link the skill folder for development:

```bash
git clone https://github.com/tjdwls101010/Agentic-Delegate.git
ln -s "$PWD/Agentic-Delegate/.claude/skills/delegate" ~/.claude/skills/delegate
```

Pre-approve the skill and its one command form in `~/.claude/settings.json` (`permissions.allow`), with the skill's folder written out:

```json
"Skill(delegate)",
"Bash(uv run \"/Users/you/.claude/skills/delegate/scripts/cli.py\" *)"
```

For the plugin, the skill is `Skill(delegate:delegate)` and the folder `~/.claude/plugins/cache/agentic-delegate/delegate/<version>/.claude/skills/delegate`. `doctor` prints the exact command form as `invocation`. First run:

```bash
uv run "$SKILL_DIR/scripts/cli.py" doctor
uv run "$SKILL_DIR/scripts/cli.py" models
```

## 4. Usage

Inside Claude Code, ask in plain words ("have GPT review this read-only", "get a second opinion from another Claude session"); the skill tells Claude how to call it. By hand:

```bash
CLI="$SKILL_DIR/scripts/cli.py"
uv run "$CLI" models
uv run "$CLI" start "Run the test suite and report failures. Change nothing." --engine codex --model <model> --effort medium --profile read-only
uv run "$CLI" result --run <run_id> --wait
uv run "$CLI" send <claude_run_id> "Also say how long the suite took."
uv run "$CLI" batch --group review --tasks-file tasks.jsonl --worktree
uv run "$CLI" result --group review --wait
uv run "$CLI" clean --group review
```

Every command prints one JSON line (or text, where its `--help` says so); `uv run "$CLI" <command> --help` documents its output and exit codes. Runs of both engines are recorded in `.delegate-runs/` of the repository's main checkout, which ignores itself.

## 5. Scheduling

A Claude worker can run unattended at a set time — every weekday, or once on a date — on this Mac, with GitHub Actions as its record and controls.

```
schedule ──▶ GitHub API ──▶ .github/workflows/NAME.yml on the default branch   (the definition; GitHub's cron is a late backup)
         └─▶ launchctl  ──▶ ~/Library/LaunchAgents/delegate-schedule.OWNER-REPO.NAME.plist   (starts the workflow on time)

on time:  LaunchAgent ── gh workflow run ──▶ GitHub ──▶ this repository's self-hosted runner (this Mac)
            Run:    the worker starts as `start` would, under your login, and the job holds it to its end
            Finish: stops whatever is still alive, records what changed, brings your checkout's default
                    branch up to GitHub's, writes the run's summary
```

It needs a **private** repository (`--public-ok` overrides), a **self-hosted runner** of it installed as a service on this Mac that can **read your Claude login** (the `schedule` reply's `runner.problems` names what is missing), the Mac **awake** at the time, and `gh` with the `workflow` scope.

```bash
uv run "$CLI" schedule --name nightly-review "Review yesterday's commits on main and report what looks risky." --cron "0 3 * * *" --engine claude --model <model> --effort high --profile read-only
gh workflow run nightly-review.yml -f mode=check     # proves the runner can start a worker
uv run "$CLI" schedule --list
uv run "$CLI" unschedule --name nightly-review
```

Every run spends the subscription's limits with nobody watching. A run the usage limit ended is reported red in its summary, with when the limit resets, and is not run again; a failed read-only run gets one retry from the next trigger. A slot missed altogether is filled later by dispatching its workflow with `-f source=dispatcher -f slot=<epoch seconds>`. Schedules written by the earlier `claude` skill are listed and can be taken over by calling `schedule` with their `--name`; `migrate_schedules/` moves a whole repository's.

## 6. Project Status

Version 0.1.1. Verified on macOS with Claude Code 2.1.332 and the Codex CLI 0.167.1; scheduling with gh and a self-hosted runner on a private repository. Not included: Linux and Windows verification, scheduled GPT workers, messages into a running GPT worker, workers that message each other directly (run rounds with `batch` and `resume`). See [CHANGELOG.md](CHANGELOG.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the Apache License 2.0.
