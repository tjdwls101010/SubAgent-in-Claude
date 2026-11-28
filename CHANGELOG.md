# Changelog

## 0.1.0 — 2026-11-28

The first release of `delegate`, which replaces the `codex` skill (Codex in Claude, last 0.11) and the `claude` skill (Claude in Claude, last 0.4.0) with one skill whose engine is an argument.

- One registry, `.delegate-runs/` in the repository's main checkout, for runs of both engines; the old `.codex-runs/` and `.claude-runs/` are not read, and an old thread or session is resumed by its id with `--engine`.
- Every new run names its engine, model and effort; nothing has a default. Permission profiles `read-only`, `write` and `full` share their names across engines.
- Groups of both engines (`batch`), with a checkout of their own for writers (`--worktree`: Claude Code's worktree for a Claude member, a detached checkout for a GPT member) and `clean`.
- `send` into a running Claude worker; attention for both engines; a group wait returns when a member fails.
- `models`: each engine's models, efforts and account limits now; a start refused only on a fresh reading of a used-up limit; the end state `quota_exhausted` with the way to hand the work over.
- Scheduling a Claude worker (`schedule`, `unschedule`), carried over from the `claude` skill with required `--engine`, `--model` and `--effort`, a recovery dispatch input `slot`, takeover of the schedules the `claude` skill wrote, and a run the usage limit ended reported but not run again; `migrate_schedules/` moves a repository's schedules over.
