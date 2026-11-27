"""A run as a row, whatever its engine: its stored state, the states derived from its events (retrying, stalled), and the attention that ends a wait early."""

from __future__ import annotations

import os
from pathlib import Path

from delegate.engine import adapter
from delegate.registry import ACTIVE, TERMINAL, reap, worker_alive
from delegate.util import TIMINGS, iso, now, timings

STALL_SECONDS = int(TIMINGS["stall"])
LISTING_ROWS = 20
EXCERPT = 300


def last_event_at(run_dir, meta):
    try:
        return os.stat(Path(run_dir) / "events.jsonl").st_mtime
    except OSError:
        return meta.get("started_at") or meta.get("created_at") or now()


def load(run_dir):
    """(meta after reaping, the engine's view of its events) for one run."""
    meta = reap(run_dir)
    return meta, adapter(meta["engine"]).view(run_dir, meta)


def attention(meta, view, event_at, at, since=None):
    """Reasons a caller should look at this run now: no event for the stall time (every engine), then the engine's own."""
    out = []
    if meta.get("state") in ACTIVE:
        idle = at - max(event_at, since or 0)
        if idle >= timings()["stall"]:
            out.append({"reason": "stalled", "idle_seconds": round(at - event_at), "in_progress": view["in_progress"], "note": "no event for this long; nothing is killed for it"})
    return out + adapter(meta["engine"]).attention(meta, view, at)


def display_state(meta, view, event_at, at):
    state = meta.get("state")
    if state in ACTIVE:
        if view.get("retrying"):
            return "retrying"
        if at - event_at >= timings()["stall"]:
            return "stalled"
    return state


def run_row(run_dir, at=None):
    """Everything known about one run, as `status --run` prints it: the common fields, then the engine's own."""
    at = at or now()
    meta, v = load(run_dir)
    event_at = last_event_at(run_dir, meta)
    s = meta.get("settings") or {}
    row = {
        "run_id": meta["run_id"],
        "engine": meta.get("engine"),
        "native_id": meta.get("native_id"),
        "state": display_state(meta, v, event_at, at),
        "stored_state": meta.get("state"),
        "label": meta.get("label"),
        "group": meta.get("group"),
        "kind": meta.get("kind"),
        "profile": s.get("profile"),
        "model": v.get("model") or s.get("model"),
        "effort": s.get("effort"),
        "cwd": v.get("cwd") or meta.get("cwd"),
        "worktree": meta.get("worktree"),
        "created": iso(meta.get("created_at")),
        "ended": iso(meta.get("ended_at")),
        "elapsed_seconds": round((meta.get("ended_at") or at) - (meta.get("created_at") or at)),
        "idle_seconds": round(at - event_at) if meta.get("state") not in TERMINAL else None,
        "exit_code": meta.get("exit_code"),
        "error": meta.get("error"),
        "attention": attention(meta, v, event_at, at) or None,
        "in_progress": v["in_progress"],
        "files_changed": v["files_changed"],
        "usage": v["usage"],
        "last_message": (v["last_message"] or "")[:EXCERPT] or None,
        **v["row"],
        "unparsed_events": v["unparsed"],
        "events": str(Path(run_dir) / "events.jsonl"),
    }
    if meta.get("state") in TERMINAL and worker_alive(meta):
        row["still_writing"] = True
    if meta.get("stderr_tail"):
        row["stderr_tail"] = meta["stderr_tail"]
    elif meta.get("state") == "failed":
        try:
            row["stderr_tail"] = (Path(run_dir) / "stderr.log").read_bytes()[-600:].decode("utf-8", "ignore").strip() or None
        except OSError:
            pass
    return row


def listing_row(row):
    keep = ("run_id", "engine", "native_id", "state", "label", "group", "profile", "model", "idle_seconds", "error", "attention")
    out = {k: row.get(k) for k in keep}
    out["last_message"] = (row["last_message"] or "")[:120] or None
    return out
