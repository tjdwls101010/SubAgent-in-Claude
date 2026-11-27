"""One run as structure: the header `result` prints, the final message, the structured output, and what `log` prints — what `result` and `log` render, and what a caller can read without parsing their text."""

from __future__ import annotations

from delegate.engine import adapter
from delegate.registry import TERMINAL, find_run, resolve_runs_dir, worker_alive
from delegate.util import now

from .rows import attention, last_event_at, load


def snapshot_of(run_dir, attention_override=None):
    """The run in `run_dir` now. `attention_override` replaces the attention computed at this moment (a wait passes what ended it)."""
    meta, v = load(run_dir)
    live = meta.get("state") not in TERMINAL
    header = {
        "run_id": meta["run_id"],
        "engine": meta.get("engine"),
        "native_id": meta.get("native_id") or v.get("native_id"),
        "state": meta.get("state"),
        "exit_code": meta.get("exit_code"),
        "error": meta.get("error"),
        "attention": attention_override or attention(meta, v, last_event_at(run_dir, meta), now()) or None,
        "files_changed": v["files_changed"],
        "usage": v["usage"],
        **v["header"],
        "message_bytes": len(v["text"].encode("utf-8")),
    }
    if v["unparsed"]:
        header["unparsed_events"] = v["unparsed"]
    if live:
        header["note"] = "the answer is in, but the worker is still running something" if v["decided"] else "the run is live: this is its latest message, not its answer"
    elif worker_alive(meta):
        header["note"] = "the run is recorded ended, but its worker still runs: this is its latest message, not its answer"
    return {
        "meta": meta,
        "header": header,
        "text": v["text"],
        "schema": v["schema"],
        "json": v["json"],
        "lines": adapter(meta["engine"]).log_lines(v),
        "unparsed": v["unparsed"],
        "rate_limit": v.get("rate_limit"),
        "live": live,
        "decided": v["decided"],
    }


def snapshot(*, cwd, ref, runs_dir=None):
    """The run `ref` names, as structure: `meta` (as recorded), `header` (the fields `result` prints, except `next`), `text` (the final message, or the latest one while live), `schema` (whether it is a --schema run), `json` (the structured output, None while undecided or when there is none — never an error), `lines` (what `log` prints), `unparsed` (event lines that were not JSON), `rate_limit`, `live` and `decided`."""
    return snapshot_of(find_run(resolve_runs_dir(cwd, runs_dir), ref))
