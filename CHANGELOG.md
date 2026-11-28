# Changelog

## 0.1.1 — 2026-11-28

Fixes found by reviews after the first release.

- Limits: a reading that cannot be read or does not apply to this run never refuses a start — a Codex allowance the server cannot state, another bucket's or a credit-paid rollout window, another Claude login's rejection event. A run that ends between two polls still records its limit event, so the next start is refused. Each Claude window reports its `resets_at`.
- `send`: `delivered` means the whole message was written to the worker's stdin. A message that was never confirmed is listed `undelivered` and is never sent twice. A `finishing` run's resume refusal points to the wait first; two group waits report one failure once.
- `clean` keeps what a person could still want: a checkout of a newer generation of the group's name, a directory git does not record as a half-made checkout, and submodule commits held only by a local or remote-tracking ref (stores left by `deinit`, paths with spaces, unreadable refs keep the checkout). Its advice saves the commits under a ref every time. A resume a batch refused is not counted as started; doctor shows checkouts of a `--runs-dir` registry.
- Scheduling: a taken-over slot is decided by how the old skill's job ended now; a recovery's retry is kept through pruning; Finish run again leaves a one-off scheduled since alone; `unschedule` of a name differing only by case leaves the old record alone; doctor checks the one-off's own workflow file. A run the usage limit ended is not tried again by any trigger.
- `migrate_schedules/`: resume is done only once every dispatched slot is claimed; rollback checks the file before touching anything, restores against the version it checked, carries the new side's claims and run results back, and never touches another repository's dispatcher.
- `tests/parallel.py` runs the suite class by class in parallel for iterating; the sequential run stays the check before a merge.

## 0.1.0 — 2026-11-28

The first release of `delegate`, which replaces the `codex` skill (Codex in Claude, last 0.11) and the `claude` skill (Claude in Claude, last 0.4.0) with one skill whose engine is an argument.

- One registry, `.delegate-runs/` in the repository's main checkout, for runs of both engines; the old `.codex-runs/` and `.claude-runs/` are not read, and an old thread or session is resumed by its id with `--engine`.
- Every new run names its engine, model and effort; nothing has a default. Permission profiles `read-only`, `write` and `full` share their names across engines.
- Groups of both engines (`batch`), with a checkout of their own for writers (`--worktree`: Claude Code's worktree for a Claude member, a detached checkout for a GPT member) and `clean`.
- `send` into a running Claude worker; attention for both engines; a group wait returns when a member fails.
- `models`: each engine's models, efforts and account limits now; a start refused only on a fresh reading of a used-up limit; the end state `quota_exhausted` with the way to hand the work over.
- Scheduling a Claude worker (`schedule`, `unschedule`), carried over from the `claude` skill with required `--engine`, `--model` and `--effort`, a recovery dispatch input `slot`, takeover of the schedules the `claude` skill wrote, and a run the usage limit ended reported but not run again; `migrate_schedules/` moves a repository's schedules over.
