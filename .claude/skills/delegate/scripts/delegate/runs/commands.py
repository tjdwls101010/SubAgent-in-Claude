"""The run commands: start a fresh session or thread on an engine, resume one, and stop runs through their process groups."""

from __future__ import annotations

import time
from pathlib import Path

from delegate.engine import adapter, record_stopped, signal_ladder
from delegate.errors import NotFound, Refusal
from delegate.git import is_worktree, uncommitted_count
from delegate.registry import TERMINAL, find_run, newest_on, reap, registry_args, resolve_runs_dir, supervisor_alive, update_meta, worker_alive
from delegate.util import invocation, now, process_command, timings

from .create import create_run, default_cwd, refuse_inside_worker, start_reply


def start(*, engine, prompt, cwd, flags, label=None, timeout=None, runs_dir=None, origin=None, on_published=None):
    """Start a fresh worker on `engine` with `prompt`. `cwd` is where the command was run; `flags` holds profile, model, effort, schema and an explicit working directory (`cwd` key) when given; `origin` is recorded in the run's meta (see `create_run`); `on_published(meta)` is called as soon as the run is in the registry, before the start wait."""
    a = adapter(engine)
    run_cwd = flags.get("cwd") or default_cwd(cwd)
    settings = a.settings(flags, None, command="start", cwd=run_cwd)
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
        newest = reap(find_run(registry, ref, engine=engine))
        if newest.get("native_id"):
            # The session or thread goes on from its newest run, of the same engine, whichever of its runs `ref` named.
            newest = reap(newest_on(registry, newest["engine"], newest["native_id"]))
    except NotFound:
        newest = None
        if engine:
            other = None
            try:
                other = reap(find_run(registry, ref))
            except NotFound:
                pass
            if other is not None:
                raise Refusal(f"{ref} is a {other.get('engine')} run; --engine {engine} cannot continue it", arguments=True, help="resume --help", **other_engine_instead(other, engine, registry))
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
        if not flags.get("cwd") and run_cwd != launch_cwd and not Path(run_cwd).is_dir():
            # The session last worked somewhere else than it was launched from (a worktree it re-entered), and that is gone: resuming would carry on in the launch directory instead.
            raise Refusal(f"the directory this session last worked in, {run_cwd}, is gone; resuming would continue in {launch_cwd}", run_id=newest["run_id"], instead=invocation("resume", newest["run_id"], "<prompt>", "--cwd", "<directory>", *registry_args(registry)), why="name the directory to continue in")
    else:
        run_cwd = launch_cwd = flags.get("cwd") or default_cwd(cwd)
    settings = a.settings({**flags, "resume": True}, record, command="resume", cwd=run_cwd)
    extra = {}
    if group_index is not None:
        extra["group_index"] = group_index

    def precheck():
        # Under the publish lock: the settings were read from `newest`; a turn published since would carry newer ones (a changed profile), and this one would quietly undo them.
        now_newest = newest_on(registry, engine, native_id)
        if now_newest != (str(Path(registry) / newest["run_id"]) if newest is not None else None):
            raise Refusal(f"{engine} {native_id} had another turn after this resume read its settings; resume again to continue from that one", native_id=native_id)
        if worktree_check:
            worktree_check()

    if timeout is None and newest:
        timeout = (newest.get("settings") or {}).get("timeout")
    time.sleep(timings()["pause_before_resume_publish"])
    meta = create_run(runs_dir=registry, engine_name=engine, kind="resume", prompt=prompt, settings=settings, cwd=run_cwd, launch_cwd=launch_cwd, expect_cwd=expect_cwd, native_id=native_id, label=label or (newest or {}).get("label"), timeout=timeout, previous=(newest or {}).get("run_id"), group=group or (newest or {}).get("group"), worktree=wt, group_info=group_info, worktree_info=worktree_info, extra_meta=extra, precheck=precheck, on_published=on_published)
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
    ours = command is not None and adapter(meta["engine"]).is_worker_command(command)
    if pgid and pid and still and command is not None and not ours and not supervisor_alive(meta):
        # meta.json lies inside a writer's working directory; never signal a process group that is not the worker this run started. With its supervisor alive the run is still owned (the worker may just have exited), and the supervisor ends it.
        entry.update({"state": meta.get("state"), "note": f"the recorded process {pid} is not {meta['engine']} ({command[:80]}); not signalled"})
        return entry
    signalled = False
    if pgid and pid and still and ours:
        entry["signals_sent"] = signal_ladder(pgid, pid)
        signalled = bool(entry["signals_sent"])
    elif pgid and pid and still and command is None:
        entry["note"] = "`ps` could not tell whether the recorded process is this run's, so it was not signalled here" + ("; its supervisor ends it" if supervisor_alive(meta) else " and its supervisor is gone: it may still run")
    # The supervisor records the outcome once the worker exits; when it is gone, record it here. A supervisor that saw the request ends its child itself — the one way when this process could not tell the child is the worker (`ps` unreadable) — so it gets its whole ladder.
    t = timings()
    end = now() + t["kill_grace"] + 5 + (0 if signalled or not supervisor_alive(meta) else 2 * t["stop_grace"] + 3 * t["kill_grace"] + 1)
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
