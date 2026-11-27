"""`result`: what a run concluded — a JSON header and the final message, or one JSON document for a --schema run — at once, or after `--wait`."""

from __future__ import annotations

import json
from pathlib import Path

from delegate.registry import find_run, registry_args, resolve_runs_dir
from delegate.util import invocation

from .snapshot import snapshot_of
from .wait import wait

SCHEMA_KEYS = ("run_id", "engine", "native_id", "state", "exit_code", "error", "attention", "permission_denials", "files_changed", "cost_usd")


def registry_of(run_dir):
    return str(Path(run_dir).parent)


def run_result(run_dir, *, wait_reason=None, wait_attention=None, wait_timeout=None):
    snap = snapshot_of(run_dir, attention_override=wait_attention)
    meta, header, text = snap["meta"], snap["header"], snap["text"]
    if snap["live"]:
        args = ["result", "--run", meta["run_id"], "--wait", *registry_args(registry_of(run_dir))]
        if wait_timeout:
            args += ["--wait-timeout", f"{wait_timeout:g}"]
        header["next"] = {"command": invocation(*args), "run_in_background": True, "why": "waits again, for the end or for attention that is new"}
    if wait_reason == "timeout":
        header["note"] = "--wait-timeout passed before the run ended; " + header.get("note", "")
    if snap["schema"]:
        doc = {k: header[k] for k in SCHEMA_KEYS if k in header}
        if "note" in header:
            doc["note"] = header["note"]
        if "next" in header:
            doc["next"] = header["next"]
        doc["json"] = snap["json"]
        if not snap["live"] and snap["json"] is None:
            # Read, so not a refusal: the run ended without the structured output its schema asked for, and its text is what there is.
            doc["note"] = "the run ended without structured output; `message` is its final text"
            doc["message"] = text
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    out = json.dumps(header, ensure_ascii=False) + "\n"
    if text:
        out += text + "\n"
    return out


def result(*, cwd, ref=None, wait_for=False, wait_timeout=None, runs_dir=None):
    registry = resolve_runs_dir(cwd, runs_dir)
    run_dir = find_run(registry, ref)
    if not wait_for:
        return run_result(run_dir)
    reason, fresh = wait([run_dir], timeout=wait_timeout)
    return run_result(run_dir, wait_reason=reason, wait_attention=fresh.get(run_dir), wait_timeout=wait_timeout)
