"""One run as structure: the header `result` prints, the final message, the structured output, and what `log` prints — what `result` and `log` render, and what a caller can read without parsing their text."""

from __future__ import annotations

from pathlib import Path

from delegate.engine import ENGINES, adapter
from delegate.registry import TERMINAL, find_run, registry_args, resolve_runs_dir
from delegate.util import invocation, iso, now

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
    if meta.get("state") == "quota_exhausted":
        # The account's limit ended the turn; the other engine can take it over in the same directory.
        other = next((n for n in ENGINES if n != meta.get("engine")), None)
        header["resets_at"] = iso(meta.get("resets_at"))
        header["instead"] = invocation("start", "--engine", other, "--model", "<model>", "--effort", "<effort>", "--profile", (meta.get("settings") or {}).get("profile") or "<profile>", "--cwd", meta.get("cwd"), "<prompt>", *registry_args(str(Path(run_dir).parent)))
        header["why"] = f"the {meta.get('engine')} limit is used up; {other} draws on another account's limits — another model family, which matters when this was to be an independent check. Tell the new worker where this one stopped (its last message is above) and that any change it left is an unverified draft"
    if live:
        header["note"] = "the answer is in, but the worker is still running something" if v["decided"] else "the run is live: this is its latest message, not its answer"
    elif v.get("writing"):
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
        "writing": v.get("writing"),
        "decided": v["decided"],
    }


def snapshot(*, cwd, ref, runs_dir=None):
    """The run `ref` names, as structure: `meta` (as recorded), `header` (the fields `result` prints, except `next`), `text` (the final message, or the latest one while live), `schema` (whether it is a --schema run), `json` (the structured output, None while undecided or when there is none — never an error), `lines` (what `log` prints), `unparsed` (event lines that were not JSON), `rate_limit`, `live` and `decided`."""
    return snapshot_of(find_run(resolve_runs_dir(cwd, runs_dir), ref))
