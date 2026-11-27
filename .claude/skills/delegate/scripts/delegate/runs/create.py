"""Creating a run in four steps, whatever its engine: refusals that cost nothing (recursion, the engine's install), publishing the run under the registry's publish lock, spawning the detached supervisor, and waiting briefly for the engine to confirm the start so a run that fails at once says so in its reply."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from delegate.engine import adapter, inside_worker
from delegate.errors import Refusal
from delegate.git import toplevel
from delegate.registry import TERMINAL, is_live, live_runs, publish_run, read_meta, record_if_active, registry_args, unreadable_runs, update_meta
from delegate.util import entry_path, invocation, now, timings

WRITERS = ("write", "full")


def refuse_inside_worker():
    """A worker cannot start, steer, stop or clean workers."""
    run = inside_worker()
    if run:
        raise Refusal(f"this command runs inside worker run {run}; a worker cannot start, steer, stop or clean workers", inside_run=run)


def refuse_live(engine, runs_dir, native_id):
    """The publish-time check for a turn on `native_id`: refused while another run of the same engine is live on it — one recorded on it, one whose meta has not caught up with the id its events already name (a Codex thread id arrives after launch), or one whose meta.json does not parse but whose events name it."""

    def check(runs):
        if not native_id:
            return
        for d, meta in runs:
            if meta.get("engine") != engine.NAME or not is_live(meta):
                continue
            # Its record, what it was asked to resume, or what its events already name (a Codex thread id arrives after launch, and a thread asked for by name answers with its id).
            if native_id in (meta.get("native_id"), meta.get("resume_ref")) or engine.discover_native_id(d) == native_id:
                raise Refusal(f"{engine.NAME} {native_id} has a live run {meta.get('run_id')} ({meta.get('state')}{', still writing' if meta.get('state') in TERMINAL else ''}); two processes on one session or thread mix their transcripts", run_id=meta.get("run_id"), state=meta.get("state"), **engine.live_instead(meta.get("run_id"), meta, runs_dir))
        # 성진: an unreadable run whose events do not name its session or thread yet is taken as someone else's; refuse it too if a real case shows such a run was on the same one.
        blind = [d for d in unreadable_runs(runs_dir) if engine.discover_native_id(d) == native_id]
        if blind:
            raise Refusal(f"cannot tell whether {native_id} is free: these runs on it have a meta.json that does not parse; repair or remove them", unreadable_runs=blind)

    return check


def default_cwd(cwd):
    return toplevel(cwd) or str(Path(cwd).resolve())


def spawn_supervisor(run_dir):
    log = open(Path(run_dir) / "supervisor.log", "ab")
    proc = subprocess.Popen([sys.executable, entry_path(), "__supervise", "--run-dir", run_dir], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True, close_fds=True)
    log.close()
    return proc.pid


def wait_for_start(run_dir, engine, seconds):
    """Wait until the engine confirms the start or the run ends, or `seconds` pass. Returns its meta."""
    end = now() + seconds
    meta = read_meta(run_dir)
    while now() < end:
        meta = read_meta(run_dir)
        if engine.started(meta) or meta.get("state") in TERMINAL:
            return meta
        time.sleep(0.05)
    return meta


def concurrent_writers(runs_dir, cwd, run_id):
    """Other live runs, of any engine, that can write in the same directory or one inside or around it."""
    out = []
    real = os.path.realpath(cwd)
    for d, m in live_runs(runs_dir):
        other = os.path.realpath(m.get("cwd") or "")
        if m.get("run_id") == run_id or (m.get("settings") or {}).get("profile") not in WRITERS or not other:
            continue
        if other == real or other.startswith(real.rstrip(os.sep) + os.sep) or real.startswith(other.rstrip(os.sep) + os.sep):
            out.append(m.get("run_id"))
    return out


def create_run(*, runs_dir, engine_name, kind, prompt, settings, cwd, native_id=None, label=None, timeout=None, previous=None, group=None, worktree=None, expect_cwd=None, launch_cwd=None, group_info=None, worktree_info=None, extra_meta=None, precheck=None, on_published=None, origin=None):
    """Publish and start one run. `kind` is `start` (a new session or thread) or `resume` (`native_id` given). `origin` says what started it when that was not a caller in a conversation (a schedule's job), and is in the meta from the moment the run is published. Returns the run's meta after the start wait."""
    refuse_inside_worker()
    engine = adapter(engine_name)
    engine.check_installed()
    if kind == "start":
        native_id = engine.new_native_id()
    launch_cwd = launch_cwd or cwd
    settings = {k: v for k, v in settings.items() if k != "changed"}
    settings["timeout"] = timeout

    first = {}

    def make_meta(run_id, run_dir):
        meta = {
            "run_id": run_id,
            "engine": engine.NAME,
            "native_id": native_id,
            "kind": kind,
            "label": label,
            "group": group,
            "state": "starting",
            "cwd": cwd,
            "launch_cwd": launch_cwd,
            "expect_cwd": expect_cwd,
            "worktree": worktree,
            "settings": settings,
            "previous_run": previous,
            # What the turn was asked to continue, which an engine may report back under another id (a Codex thread name, answered with its id).
            "resume_ref": native_id if kind == "resume" else None,
            "caller_session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        }
        meta.update(engine.extra_meta(cwd, settings))
        if origin:
            meta["origin"] = origin
        meta.update(extra_meta or {})
        argv, text = engine.prepare({**meta, "run_dir": run_dir}, prompt, group_info=group_info, worktree_info=worktree_info, origin=origin)
        meta["argv"] = argv
        first["text"] = text
        return meta

    live_check = refuse_live(engine, runs_dir, native_id)

    def check(runs):
        live_check(runs)
        if precheck:
            precheck()

    run_id, run_dir, meta = publish_run(runs_dir, label, check, make_meta)
    if on_published:
        on_published(meta)
    time.sleep(timings()["pause_after_publish"])
    try:
        engine.after_publish(run_dir, first["text"], prompt)
        pid = spawn_supervisor(run_dir)
    except OSError as e:
        # Published but never handed to a supervisor: record it, so the session is not held by a run nothing drives.
        record_if_active(run_dir, {"state": "failed", "error": f"could not start the supervisor: {e}", "ended_at": now(), "stdin_open": False})
        raise
    update_meta(run_dir, lambda m: (m.setdefault("supervisor_pid", pid), m.setdefault("supervisor_pgid", pid)))
    return wait_for_start(run_dir, engine, timings()["init_wait"])


def start_reply(meta, runs_dir):
    """The JSON a starting command prints for one run."""
    engine = adapter(meta["engine"])
    s = meta["settings"]
    own = engine.reply_fields(meta)
    reply = {
        "run_id": meta["run_id"],
        "engine": meta["engine"],
        "native_id": meta.get("native_id"),
        "state": meta.get("state"),
        "next": {"command": invocation("result", "--run", meta["run_id"], "--wait", *registry_args(runs_dir)), "run_in_background": True, "why": "returns when the run ends or needs attention, and prints its result"},
        "profile": s["profile"],
        "model": own.pop("model", None) or s.get("model"),
        "effort": s.get("effort"),
        "cwd": own.pop("cwd", None) or meta["cwd"],
        "concurrent_writers": concurrent_writers(runs_dir, meta["cwd"], meta["run_id"]) if s["profile"] in WRITERS else [],
        **own,
        "events": str(Path(runs_dir) / meta["run_id"] / "events.jsonl"),
    }
    if not engine.started(meta) and meta.get("state") not in TERMINAL:
        reply["note"] = engine.not_started_note()
    if meta.get("state") in TERMINAL and meta.get("state") != "completed" and not engine.started(meta):
        # Ended before the engine took the run: a start that failed. A run that failed after it started is the result's to report.
        reply["error"] = meta.get("error")
        if meta.get("stderr_tail"):
            reply["stderr_tail"] = meta["stderr_tail"]
        reply.pop("next")
    return reply
