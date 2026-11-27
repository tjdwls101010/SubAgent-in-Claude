"""A group, of any engines, as `status --group` and `result --group` show it: each slot is the run that slot started — a member resumed later is a new run in another batch, and this group goes on showing the run it started — read with the same rows a run has, and the paths more than one member changed.

A group is read from its manifest once; a wait keeps the members it read, so a name released and taken again meanwhile never turns one group's wait into another's answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from delegate.errors import NotFound, Refusal
from delegate.git import repo_identity
from delegate.registry import TERMINAL, find_run, group_view, is_live, list_groups, reap, registry_args
from delegate.util import invocation, nfc, now, trim_bytes

from .rows import attention, last_event_at, load, run_row

GROUP_MESSAGE_CAP = 4000


def member_dirs(runs_dir, manifest):
    """(slot, run_dir of the run the slot started, or None) for each slot of a group."""
    out = []
    for slot in manifest.get("members") or []:
        d = None
        if slot.get("run_id"):
            try:
                d = find_run(runs_dir, slot["run_id"])
            except (NotFound, Refusal):
                d = None
        out.append((slot, d))
    return out


def group_state(states):
    """`running` while any member is live, `completed` when every member completed, `partial` otherwise — a failure, a stop, a timeout or a member that never started."""
    if any(s not in TERMINAL for s in states):
        return "running"
    if states and all(s == "completed" for s in states):
        return "completed"
    return "partial"


def live_state(meta):
    return "running" if is_live(meta) else meta.get("state")


def group_status(registry, name):
    manifest = group_view(registry, name)
    members = [(slot, run_row(d) if d else None) for slot, d in member_dirs(registry, manifest)]
    rows = [r for _, r in members if r]
    states = [("running" if r.get("still_writing") else r["stored_state"]) for r in rows] + ["failed"] * sum(1 for _, r in members if r is None)
    return {
        "group": name,
        "generation": manifest.get("generation"),
        "group_state": group_state(states),
        "running": [r["run_id"] for r in rows if r["stored_state"] not in TERMINAL or r.get("still_writing")],
        "done": [r["run_id"] for r in rows if r["stored_state"] == "completed"],
        "failed": [r["run_id"] for r in rows if r["stored_state"] in TERMINAL and r["stored_state"] != "completed" and not r.get("still_writing")],
        "unstarted": [{"index": s["index"], "label": s.get("label"), "engine": s.get("engine"), "error": s.get("error")} for s, r in members if r is None],
        "members": [{"index": s["index"], "label": s.get("label"), **(r or {"run_id": None, "engine": s.get("engine"), "state": None, "error": s.get("error")})} for s, r in members],
    }


def groups_summary(registry):
    out = []
    for manifest in list_groups(registry):
        states, engines = [], []
        for slot, d in member_dirs(registry, manifest):
            try:
                meta = reap(d) if d else None
            except Refusal:
                meta = None
            states.append(live_state(meta) if meta else "failed")
            engines.append(slot.get("engine"))
        out.append({"group": manifest["name"], "group_state": group_state(states), "members": len(manifest.get("members") or []), "engines": sorted({e for e in engines if e}), "worktree": bool(manifest.get("worktree")), "created": manifest.get("created")})
    return out


def overlap_key(path, cwd):
    """A changed path as (repository, path relative to its checkout's top level): every checkout of one repository has its own prefix, and two repositories' `README.md` are two files."""
    p = Path(nfc(str(path)))
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    repo, top = repo_identity(str(p.parent) if p.parent.exists() else cwd or str(p.parent))
    if top:
        try:
            return repo, str(p.relative_to(top))
        except ValueError:
            pass
    return None, str(p)


def group_result(registry, name, *, manifest=None, wait_reason=None, fresh=None, wait_timeout=None):
    """A group's header, then each member's final message capped at GROUP_MESSAGE_CAP bytes behind a separator line."""
    manifest = manifest or group_view(registry, name)
    members = member_dirs(registry, manifest)
    rows, sections, edited, cost = [], [], {}, 0.0
    states, attention_members = [], []
    for slot, d in members:
        label = " ".join(slot["label"].split()) if slot.get("label") else None
        tag = f"{slot['index']}:{label}" if label else str(slot["index"])
        if d is None:
            rows.append({"index": slot["index"], "label": slot.get("label"), "engine": slot.get("engine"), "run_id": None, "state": None, "error": slot.get("error")})
            states.append("failed")
            continue
        meta, v = load(d)
        text = v["text"]
        shown = trim_bytes(text, GROUP_MESSAGE_CAP)
        att = (fresh or {}).get(d) or attention(meta, v, last_event_at(d, meta), now()) or None
        if att:
            attention_members.append({"index": slot["index"], "run_id": meta["run_id"], "attention": att})
        cwd = v.get("cwd") or meta.get("cwd")
        for path in v["files_changed"]:
            edited.setdefault(overlap_key(path, cwd), []).append(slot["index"])
        cost += (v["header"].get("cost_usd") or 0)
        states.append(live_state(meta))
        rows.append({"index": slot["index"], "label": slot.get("label"), "engine": meta.get("engine"), "run_id": meta["run_id"], "native_id": meta.get("native_id"), "state": meta.get("state"), "error": meta.get("error"), "bytes": len(text.encode("utf-8")), "shown_bytes": len(shown.encode("utf-8")), "files_changed": len(v["files_changed"]), "permission_denials": len(v["header"].get("permission_denials") or []), "worktree": (meta.get("worktree") or {}).get("path")})
        sections.append(f"--- [{tag}] engine={meta.get('engine')} run={meta['run_id']} state={meta.get('state')} bytes={len(text.encode('utf-8'))}\n" + (shown + "\n" if shown else ""))
    overlaps = {}
    shared = [(repo, rel, sorted(set(indexes))) for (repo, rel), indexes in edited.items() if len(set(indexes)) > 1]
    for repo, rel, indexes in shared:
        # A path is named relative to its checkout; one that two repositories both have is named with its repository too.
        key = rel if sum(1 for _, r, _ in shared if r == rel) == 1 else f"{repo}: {rel}"
        overlaps[key] = indexes
    header = {
        "group": name,
        "generation": manifest.get("generation"),
        "group_state": group_state(states),
        "overlaps": overlaps,
        "totals": {"members": len(members), "completed": states.count("completed"), "failed": sum(1 for s in states if s in TERMINAL and s != "completed"), "running": sum(1 for s in states if s not in TERMINAL), "cost_usd": round(cost, 6)},
        "attention": attention_members or None,
        "members": rows,
    }
    if overlaps:
        header["overlaps_note"] = "paths (relative to each member's checkout) that more than one member changed; with checkouts of their own this is a merge ahead, not damage done"
    if header["group_state"] == "running":
        args = ["result", "--group", name, "--wait", *registry_args(registry)]
        if wait_timeout:
            args += ["--wait-timeout", f"{wait_timeout:g}"]
        header["next"] = {"command": invocation(*args), "run_in_background": True, "why": "waits again, for every member to end or for attention that is new"}
        header["note"] = "members are still live: their messages are partial"
    if wait_reason:
        header["returned_because"] = wait_reason
    if wait_reason == "member_failed":
        header["note"] = "a member ended in failure while others still run; " + header.get("note", "")
    if wait_reason == "timeout":
        header["note"] = "--wait-timeout passed before every member ended; " + header.get("note", "")
    return json.dumps(header, ensure_ascii=False) + "\n" + "".join(sections)
