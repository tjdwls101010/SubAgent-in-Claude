"""Waiting on runs: until each has ended, or until one needs attention that was not there when the wait began."""

from __future__ import annotations

import time

from delegate.registry import TERMINAL, find_run, resolve_runs_dir, update_meta, worker_alive
from delegate.util import now, timings

from .rows import attention, last_event_at, load
from .snapshot import snapshot_of


def failed(meta):
    """Ended without completing, with nothing of it still running — and not because someone stopped it."""
    return meta.get("state") in TERMINAL and meta.get("state") != "completed" and not worker_alive(meta) and not meta.get("stop_requested")


def wait(run_dirs, timeout=None):
    """Block until every run in `run_dirs` has ended, a new attention appears on any of them, one of several ends in failure, or `timeout` seconds pass. Returns (reason, {run_dir: [attention…]}) with reason `ended`, `attention`, `member_failed` or `timeout`.

    Attention already present when the wait begins does not end it, so waiting again waits for something new; a stall counts afresh from the start of the wait. A failure ends a wait on several runs at once, since the others may be worth stopping or redirecting while they still run (a group whose codex member ran out of its limit) — each failure once: a wait that returns for it marks it reported on the run, so a failure that came between two waits, or before the first, is still returned by the next one, and none is returned twice, even by two waits under way at once."""
    t = timings()
    began = now()
    seen, failed_before = {}, set()
    for d in run_dirs:
        meta, v = load(d)
        seen[d] = {a["reason"] for a in attention(meta, v, last_event_at(d, meta), began) if a["reason"] != "stalled"}
        if failed(meta) and meta.get("failure_reported"):
            failed_before.add(d)
    while True:
        at = now()
        ended = True
        fresh, newly_failed = {}, []
        for d in run_dirs:
            meta, v = load(d)
            # A run whose worker still writes after its supervisor died has not ended.
            if meta.get("state") not in TERMINAL or worker_alive(meta):
                ended = False
            elif failed(meta) and d not in failed_before:
                newly_failed.append(d)
            current = attention(meta, v, last_event_at(d, meta), at, since=began)
            reasons = {a["reason"] for a in current}
            seen[d] &= reasons
            new = [a for a in current if a["reason"] not in seen[d]]
            if new and meta.get("state") not in TERMINAL:
                fresh[d] = new
        if ended:
            return "ended", {}
        if fresh:
            return "attention", fresh
        if newly_failed and len(run_dirs) > 1:
            claimed = []
            for d in newly_failed:
                # Checked and marked under the run's lock, so of two waits under way at once only one reports the failure.
                update_meta(d, lambda m, d=d: None if m.get("failure_reported") else (claimed.append(d), m.__setitem__("failure_reported", True)))
            failed_before.update(newly_failed)
            if claimed:
                return "member_failed", {}
        if timeout is not None and at - began >= timeout:
            return "timeout", {}
        time.sleep(max(t["poll"], 0.05))


def wait_ended(*, cwd, ref, runs_dir=None, on_attention=None):
    """Block until the run `ref` names has ended, whatever attention it needs meanwhile. `on_attention(items)` is called with each attention reason as it appears; a reason that lasts — one long stall — is reported once, and again only after it has cleared. Returns the ended run's `snapshot`."""
    run_dir = find_run(resolve_runs_dir(cwd, runs_dir), ref)
    t = timings()
    reported = set()
    while True:
        meta, v = load(run_dir)
        if meta.get("state") in TERMINAL and not worker_alive(meta):
            return snapshot_of(run_dir)
        current = attention(meta, v, last_event_at(run_dir, meta), now()) if meta.get("state") not in TERMINAL else []
        reasons = {a["reason"] for a in current}
        new = [a for a in current if a["reason"] not in reported]
        reported = reasons
        if new and on_attention:
            on_attention(new)
        time.sleep(max(t["poll"], 0.05))
