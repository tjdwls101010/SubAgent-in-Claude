"""The engine contract: each engine is a module offering the same names, which every other feature reaches through `adapter(name)`, never by importing an engine's CLI unit itself.

An adapter owns, for its engine: `NAME`; `STDIN` ("pipe" when messages go in on stdin, "devnull" otherwise); `settings(flags, record, command=, cwd=)` → engine-tagged settings (`engine`, `profile`, `model`, `effort`, `changed`, and the engine's own); `check_installed()`; `new_native_id()` (an id chosen before launch, or None when the engine names the run after it starts); `check_native_id(ref)`; `discover_native_id(run_dir)` (the native id a run's events name, for a run whose meta does not); `unregistered_note()`; `prepare(meta, prompt, group_info=, worktree_info=, origin=)` → (argv, the first message's text), called under the publish lock with the run's meta, which it may complete with where the run works; `worktree_plan(repo, name, base, main)` and `worktree_taken(plan)` (a group member's own checkout: where it will be and whether it can be made fresh); `extra_meta(cwd, settings)`; `after_publish(run_dir, text, prompt)`; `started(meta)` and `not_started_note()`; `reply_fields(meta)`; `live_instead(run_id, meta, runs_dir)`; `is_worker_command(command)`; `worker_env(base_env, markers)`; `driver(run_dir, meta, proc)` → an object with `bind(pump)`, `feed(event)`, `after_feed(event)`, `tick()`, `outcome(meta, returncode, stderr_tail)`; `view(run_dir, meta)` (see `claude.view` for its keys); `attention(meta, view, at)`; `log_lines(view)`; `diagnose(cwd)` → `{report, blockers, warnings}`.

The three things the shared layer keeps apart come from here: whether the run has ended (its recorded state), whether its process is alive (its recorded worker pid), and whether its answer is ready (`view(...)["decided"]`)."""

__all__ = ["ENGINES", "boot_time", "MARKER", "PROFILES", "adapter", "inside_worker", "record_stopped", "signal_ladder", "supervise", "sweep_group"]

from delegate.errors import Refusal

from . import claude, codex
from .process import MARKER, boot_time, inside_worker, record_stopped, run_supervisor, signal_ladder, sweep_group

# Permission profiles share their names across engines so a mixed group's default profile means something to every member; each engine's --help says what a name allows there.
PROFILES = ("read-only", "write", "full")
ENGINES = {"claude": claude, "codex": codex}


def adapter(name):
    """The adapter for engine `name`; Refusal (exit 2) for an engine this skill does not drive."""
    if name not in ENGINES:
        raise Refusal(f"unknown engine {name!r}: one of {', '.join(ENGINES)}", arguments=True, help="start --help")
    return ENGINES[name]


def supervise(run_dir):
    """The detached supervisor's entry: drive the run in `run_dir` with its engine's adapter until it ends."""
    from delegate.registry import read_meta

    return run_supervisor(run_dir, adapter(read_meta(run_dir)["engine"]))
