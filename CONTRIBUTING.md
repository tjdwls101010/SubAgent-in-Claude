# Contributing to Agentic Delegate

This project is a Claude Code plugin (`delegate`) holding one skill, `delegate`, backed by a Python standard-library CLI run with `uv run`. Bug fixes, tests and documentation are welcome; please open an issue before adding a runtime dependency or a new command, since the CLI is deliberately stdlib-only and its surface small.

## 1. Layout

```
.claude/skills/delegate/
├── SKILL.md          judgment the help cannot carry
└── scripts/
    ├── cli.py        the only entry point: parser, help, dispatch, exit codes
    └── delegate/     the only package; its __init__.py docstring is the map
        ├── engine/ runs/ batch/ observe/ schedule/ doctor.py models.py   features
        ├── claude_cli/ codex_cli/ git/ github/ launchd/                  formats someone else owns
        ├── registry/                                                     .delegate-runs/ state
        └── errors.py util.py                                             shared
migrate_schedules/     a maintenance job: moves the old `claude` skill's schedules over (not imported by the skill)
tests/                 suite, fake claude, codex, gh and launchctl, fixtures, S5 smokes, S6 e2e
```

Imports run feature → store → system → shared, with the edges between features named in the map; only `engine/` imports an engine's CLI unit; every unit is used through its interface (`__all__`). `tests/test_structure.py` enforces these, and that nothing edits `sys.path`. No model name (slug or alias) appears in SKILL.md, `--help` or the skill's code; `tests/test_install.py` checks it.

## 2. Development

Requirements: Python 3.11+ on PATH for the tests, `uv`, git, and for the real tiers Claude Code and the Codex CLI logged in. There is no build step. To use a checkout as your live skill:

```bash
ln -s "$PWD/.claude/skills/delegate" ~/.claude/skills/delegate
```

## 3. Tests

- **Suite** — `python3 -m unittest discover -s tests` from the repository root. It drives the real CLI as a subprocess with `tests/support/fake_claude/claude`, `fake_codex/codex`, `fake_gh/gh` and `fake_launchctl/launchctl` first on PATH, so nothing reaches either engine, GitHub or launchd; the fakes' answers come from real captures in `tests/support/fixtures/`, and each fake's docstring lists what selects a scenario. About 17 minutes; it is the check before a merge.
- **Suite, in parallel while you work** — `python3 tests/parallel.py [-j N] [test_module …]` runs the suite class by class, half the CPUs at once (about 5 minutes for the whole suite), and runs a class that failed again alone to tell a failure from contention. It is for iterating; the sequential run above is what a pull request reports.
- **S5, real engines** — from `tests/`: `DELEGATE_REAL=1 DELEGATE_SMOKE_CLAUDE_MODEL=<model> DELEGATE_SMOKE_CODEX_MODEL=<model> python3 -m unittest smoke_real_delegate -v`, each a cheap model your account can use (`models` lists them). It spends both subscriptions' limits; projects go under `.tmp/`.
- **S5, real schedules** — from `tests/`: `DELEGATE_REAL=1 DELEGATE_REAL_GITHUB=1 DELEGATE_REAL_SCHEDULE_PROJECT=<checkout> DELEGATE_SMOKE_CLAUDE_MODEL=<model> python3 -m unittest smoke_real_schedule -v`, with a checkout of a private repository of your own whose self-hosted runner runs as a service on this Mac and can read the Claude login. It commits `smoke-*.yml` and `Smoke_*.yml` workflows to that repository's default branch, installs `delegate-schedule.*` LaunchAgents, runs real jobs and workers, opens and squash-merges a pull request, and removes its schedules at the end, also when it fails. The checkout must be on its default branch with nothing in the way. About 15 minutes.
- **S6, headless contrast of SKILL.md** — `python3 tests/e2e/run_e2e.py --scenario judge|1|2|3 --variant draft|control --out .tmp/e2e` (see `tests/e2e/scenarios.md`). Use it before changing SKILL.md: a sentence that changes no behaviour is a candidate to cut.

Expected values come from captures, documentation or hand-worked examples, never from the code under test; a new behaviour gets a test that fails without it.

## 4. Pull requests

One logical change per commit; behaviour and structure in separate commits. Say in the PR what you ran and what it printed. `--help` must match what the code does — `tests/test_cli_contract.py` and `tests/test_install.py` check the parts they can.
