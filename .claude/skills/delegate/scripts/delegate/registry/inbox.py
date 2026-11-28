"""The queue between `send` and a run's supervisor. Putting a message in, the supervisor taking messages out to write them to stdin, confirming each once written, and closing stdin all happen under the run's lock, so a message put in is either delivered or still in the inbox when the supervisor dies — never dropped by a close.

The run's meta carries `stdin_open` and the `messages` list (one entry per message: uuid, head, priority, kind, `after_line` once handed to the writer — the number of stdout lines read when its write began — and `delivered_at` once all of it is written to stdin).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from delegate.util import now

from .locks import write_json
from .runs import meta_path, read_meta, run_lock


def _inbox(run_dir):
    return Path(run_dir) / "inbox"


def enqueue(run_dir, uuid, text, *, priority=None, kind="send", limit=None, head=None):
    """Put a message in the inbox while stdin is open. `head` is what listings show of it (default: the text's first 80 characters). Returns None when queued, otherwise why not: `closed`, or `limit` when the run already took `limit` sends."""
    with run_lock(run_dir):
        meta = read_meta(run_dir)
        if not meta.get("stdin_open"):
            return "closed"
        messages = meta.setdefault("messages", [])
        if limit is not None and kind == "send" and sum(1 for m in messages if m.get("kind") == "send") >= limit:
            return "limit"
        _inbox(run_dir).mkdir(exist_ok=True)
        name = f"{time.time_ns():020d}-{uuid}.json"
        write_json(_inbox(run_dir) / name, {"uuid": uuid, "text": text, "priority": priority, "kind": kind})
        messages.append({"uuid": uuid, "head": (head if head is not None else text)[:80], "bytes": len(text.encode("utf-8")), "priority": priority, "kind": kind, "enqueued_at": now(), "delivered_at": None, "after_line": None})
        write_json(meta_path(run_dir), meta)
        return None


def pending(run_dir):
    return sorted(_inbox(run_dir).glob("*.json")) if _inbox(run_dir).is_dir() else []


def take(run_dir, deliver, lines_read):
    """Hand every queued message not yet handed, oldest first — a payload with no entry in meta, left by a sender that died mid-enqueue, is discarded — to `deliver(message)` (which queues it for writing to stdin) and record `after_line`; `lines_read()` is the stdout line count at that moment. A message stays in the inbox until `confirm` says it is written. Returns the messages handed."""
    with run_lock(run_dir):
        files = pending(run_dir)
        if not files:
            return []
        meta = read_meta(run_dir)
        delivered = []
        for f in files:
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                f.unlink(missing_ok=True)
                continue
            entry = next((m for m in meta.setdefault("messages", []) if m.get("uuid") == msg["uuid"]), None)
            if entry is None:
                # Its sender died between writing the payload and recording it, both under this lock: the message was never accepted.
                f.unlink(missing_ok=True)
                continue
            if entry.get("after_line") is not None:
                continue
            at = lines_read()
            deliver(msg)
            entry["after_line"] = at
            delivered.append(msg)
        write_json(meta_path(run_dir), meta)
        return delivered


def confirm(run_dir, uuid):
    """Record that the message `uuid` is written to stdin in full: its `delivered_at`, and its payload gone from the inbox."""
    with run_lock(run_dir):
        for f in _inbox(run_dir).glob(f"*-{uuid}.json") if _inbox(run_dir).is_dir() else []:
            f.unlink(missing_ok=True)
        meta = read_meta(run_dir)
        for m in meta.get("messages") or []:
            if m.get("uuid") == uuid:
                m["delivered_at"] = now()
        write_json(meta_path(run_dir), meta)


def close(run_dir, decide):
    """Close the run's stdin if the inbox is empty and `decide(meta)` agrees, recording it in meta; `decide` runs under the lock, so no message can arrive between the decision and the close. True when closed."""
    with run_lock(run_dir):
        if pending(run_dir):
            return False
        meta = read_meta(run_dir)
        if not meta.get("stdin_open"):
            return True
        if not decide(meta):
            return False
        meta["stdin_open"] = False
        meta["stdin_closed_at"] = now()
        write_json(meta_path(run_dir), meta)
        return True
