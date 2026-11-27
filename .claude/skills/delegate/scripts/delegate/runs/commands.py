"""The run commands: start a fresh session or thread on an engine, resume one, and stop runs through their process groups."""

from __future__ import annotations

import time
from pathlib import Path

from delegate.engine import adapter, record_stopped, signal_ladder
from delegate.errors import Refusal
from delegate.git import is_worktree, uncommitted_count
from delegate.registry import TERMINAL, find_run, reap, registry_args, resolve_runs_dir, update_meta, worker_alive
from delegate.util import invocation, now, process_command, timings

from .create import create_run, default_cwd, refuse_inside_worker, start_reply


def start(*, engine, prompt, cwd, flags, label=None, timeout=None, runs_dir=None, origin=None, on_published=None):
    """Start a fresh worker on `engine` with `prompt`. `cwd` is where the command was run; `flags` holds profile, model, effort, schema and an explicit working directory (`cwd` key) when given; `origin` is recorded in the run's meta (see `create_run`); `on_published(meta)` is called as soon as the run is in the registry, before the start wait."""
    a = adapter(engine)
    settings = a.settings(flags, None, command="start")
    run_cwd = flags.get("cwd") or default_cwd(cwd)
    # The registry belongs to the project the command runs in, so `status` there finds the run whatever --cwd says.
    registry = resolve_runs_dir(cwd, runs_dir)
    meta = create_run(runs_dir=registry, engine_name=engine, kind="start", prompt=prompt, settings=settings, cwd=run_cwd, label=label, timeout=timeout, origin=origin, on_published=on_published)
    return start_reply(meta, registry)


def other_engine_instead(previous, wanted, runs_dir):
    """What to do instead of resuming a run on another engine: a conversation does not cross engines, so the other engine starts fresh in the same directory with what the previous run left."""
    extra = registry_args(runs_dir)
    cwd = previous.get("cwd")
    return {
        "instead": [invocation("result", "--run", previous["run_id"], *extra), invocation("start", "--engine", wanted, "--model", "<model>", "--effort", "<effort>", "--profile", (previous.get("settings") or {}).get("profile") or "<profile>", "--cwd", cwd, "<prompt>", *extra)],
        "why": f"run {previous['run_id']} is a {previous.get('engine')} {('session' if previous.get('engine') == 'claude' else 'thread')}, which {wanted} cannot continue: read what it concluded with `result`, then start a {wanted} worker in the same directory with a brief that says where the previous worker stopped, what its last message said, and that any change it left is an unverified draft",
    }


def resume(*, ref, prompt, cwd, flags, engine=None, label=None, timeout=None, runs_dir=None, group=None, group_info=None, group_index=None, on_published=None):
    """Run another turn on an existing session or thread as a new run, re-sending its settings. `ref` is a run, a native id the registry knows, or the native id of one it never saw — then `engine`, a profile and a working directory must be given (and whatever else that engine cannot recover; see `resume --help`)."""
    registry = resolve_runs_dir(cwd, runs_dir)
    try:
        newest = reap(find_run(registry, ref))
        if newest.get("native_id"):
            newest = reap(find_run(registry, newest["native_id"]))
    except Refusal:
        newest = None
    if newest is not None:
        if engine and engine != newest.get("engine"):
            raise Refusal(f"{ref} is a {newest.get('engine')} run; --engine {engine} cannot continue it", arguments=True, help="resume --help", **other_engine_instead(newest, engine, registry))
        engine = newest["engine"]
        if not newest.get("native_id"):
            raise Refusal(f"run {newest['run_id']} has no {('session' if engine == 'claude' else 'thread')} id, so there is nothing to resume; `status --run` shows why", run_id=newest["run_id"], state=newest.get("state"))
        native_id = newest["native_id"]
    else:
        if not engine:
            raise Refusal(f"{ref!r} is not a run, session or thread in {registry}; to resume one this registry never saw, give its --engine (with --profile and --cwd)", arguments=True, help="resume --help")
        adapter(engine).check_native_id(ref)
        if not flags.get("cwd"):
            raise Refusal("this session or thread is not in the registry, so the directory it ran in is unknown; pass --cwd", arguments=True, help="resume --help")
        native_id = ref
    a = adapter(engine)
    record = dict(newest["settings"]) if newest else None
    settings = a.settings({**flags, "resume": True}, record, command="resume")
    wt = (newest or {}).get("worktree")
    expect_cwd = worktree_info = worktree_check = None
    if wt and engine == "claude":
        # A worktree member resumes from where it was launched and Claude Code re-enters its worktree; a worktree that is gone would silently resume in the launch directory instead.
        def worktree_check():
            if not Path(wt["path"]).is_dir() or not is_worktree(wt["repo"], wt["path"]):
                raise Refusal(f"the worktree {wt['path']} this session ran in is gone or no longer a git worktree; resuming would continue in {newest.get('launch_cwd')} without isolation", run_id=newest["run_id"], instead=invocation("start", "--engine", engine, "<prompt>", *registry_args(registry)), why="start a fresh session; the old one's work is in its branch " + str(wt.get("branch")))

        # Checked now for a quick refusal, and again under the publish lock, where `clean` cannot remove the worktree in between.
        worktree_check()
        run_cwd = expect_cwd = wt["path"]
        launch_cwd = newest.get("launch_cwd")
        worktree_info = {"path": wt["path"], "base": wt["base"], "uncommitted": uncommitted_count(wt["repo"])}
    elif newest:
        run_cwd = flags.get("cwd") or newest.get("cwd")
        launch_cwd = flags.get("cwd") or newest.get("launch_cwd") or run_cwd
    else:
        run_cwd = launch_cwd = flags.get("cwd") or default_cwd(cwd)
    extra = {}
    if group_index is not None:
        extra["group_index"] = group_index
    meta = create_run(runs_dir=registry, engine_name=engine, kind="resume", prompt=prompt, settings=settings, cwd=run_cwd, launch_cwd=launch_cwd, expect_cwd=expect_cwd, native_id=native_id, label=label or (newest or {}).get("label"), timeout=timeout, previous=(newest or {}).get("run_id"), group=group or (newest or {}).get("group"), worktree=wt, group_info=group_info, worktree_info=worktree_info, extra_meta=extra, precheck=worktree_check, on_published=on_published)
    reply = start_reply(meta, registry)
    if settings["changed"].get("profile"):
        reply["profile_changed_from"] = settings["changed"]["profile"]
    if newest is None:
        reply["note"] = a.unregistered_note()
    return reply


def stop_one(run_dir):
    meta = reap(run_dir)
    entry = {"run_id": meta["run_id"], "engine": meta.get("engine"), "native_id": meta.get("native_id"), "signals_sent": []}
    still = worker_alive(meta)
    if meta.get("state") in TERMINAL and not still:
        entry.update({"state": meta["state"], "note": "already ended"})
        return entry
    if meta.get("state") not in TERMINAL:
        time.sleep(timings()["pause_before_stop_mark"])
        # Set under the run's lock and read back with the pid: a supervisor that records the worker's pid after this sees the request and stops the worker itself.
        meta = update_meta(run_dir, lambda m: m.update({"stop_requested": now()}) if m.get("state") not in TERMINAL else None)
        still = worker_alive(meta)
    pgid, pid = meta.get("worker_pgid"), meta.get("worker_pid")
    command = process_command(pid) if pid else None
    if pgid and pid and still and not adapter(meta["engine"]).is_worker_command(command):
        # meta.json lies inside a writer's working directory; never signal a process group that is not the worker this run started.
        entry.update({"state": meta.get("state"), "note": f"the recorded process {pid} is not {meta['engine']} ({(command or '?')[:80]}); not signalled"})
        return entry
    if pgid and pid:
        entry["signals_sent"] = signal_ladder(pgid, pid)
    # The supervisor records the outcome once the worker exits; when it is gone, record it here.
    end = now() + timings()["kill_grace"] + 5
    while now() < end:
        meta = reap(run_dir)
        if meta.get("state") in TERMINAL:
            break
        time.sleep(0.05)
    if meta.get("state") not in TERMINAL:
        meta = update_meta(run_dir, record_stopped)
    entry["state"] = meta.get("state")
    return entry


def stop(*, refs=(), cwd, runs_dir=None, run_dirs=()):
    """Stop each run named in `refs` (and each of `run_dirs`): SIGINT to its process group, then SIGTERM, then SIGKILL, so the engine can end the turn and keep the session or thread resumable."""
    refuse_inside_worker()
    registry = resolve_runs_dir(cwd, runs_dir)
    dirs = [find_run(registry, r) for r in refs] + list(run_dirs)
    if not dirs:
        raise Refusal("nothing to stop", arguments=True, help="stop --help")
    return {"stopped": [stop_one(d) for d in dirs]}
