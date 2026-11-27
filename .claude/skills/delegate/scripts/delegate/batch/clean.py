"""`clean`: remove a group's checkouts and release its name once nothing of it is left.

A checkout is removed only when removing it loses nothing: no change to a tracked file, every untracked or ignored entry either a `.worktreeinclude` copy (a claude member's) or a startup artifact unchanged since the member's first init recorded it, and no commit on a detached HEAD that no branch holds. `git worktree remove` would silently delete ignored files, so this check comes first. --force discards what the check would keep, but never live work, an orphan still writing, a checkout any run of any engine works in, a member whose record cannot be read, or commits no branch holds.

Each member's check and removal happen under the registry's publish lock, the same lock every run is published under, so no run can start working in a checkout between `clean`'s look and its removal.

A caller that can ask GitHub (a schedule's Finish) passes `landed`: a member whose commits are already on GitHub then goes even with something left in it, which is first kept in git under refs/delegate/leftovers/<group>-<index>, and its branches go with it when they point at that commit; a member whose pull request is still open stays.
"""

from __future__ import annotations

import os
from pathlib import Path

from delegate.errors import NotFound, Refusal
from delegate.git import branch_tip, commits_beyond, current_branch, delete_branch, detached_commits, head_commit, last_changed, leftovers, preserve_leftovers, prune_worktree, remove_unfinished, submodule_commits_only_here, unlock_and_remove
from delegate.registry import find_run, group_view, is_live, iter_runs, publish_lock, read_meta, reap, registry_args, release_group, unreadable_runs
from delegate.util import invocation, pid_alive, process_command


def summarize_ignored(paths):
    return sorted({p.split("/", 1)[0] for p in paths})


def spawning(manifest):
    """Whether the batch that wrote the manifest is still starting members: its pid alive and still this skill's batch, so a reused pid does not block clean."""
    pid = manifest.get("spawning_pid")
    if not pid or not pid_alive(pid):
        return False
    command = process_command(pid) or ""
    return "cli.py" in command and " batch" in command


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def member_meta(runs_dir, slot):
    """The meta of the run a slot started; None when it never started; Refusal when it cannot be read."""
    if not slot.get("run_id"):
        return None
    try:
        return reap(find_run(runs_dir, slot["run_id"]))
    except NotFound:
        return None


def checkout_of(slot, meta):
    """Where a member's own checkout is: the slot's plan, completed by what its run recorded (a codex checkout's path is set when it is cut; a claude one's when claude starts in it)."""
    if slot.get("kind", "start") != "start":
        # A resumed member works in a checkout an earlier group made; that group cleans it.
        return None
    wt = dict(slot.get("worktree") or {})
    if meta and meta.get("worktree"):
        wt.update({k: v for k, v in meta["worktree"].items() if v is not None})
    return wt if wt.get("path") else None


def clean(*, runs_dir, name, force=False, landed=None, untouched_since=None):
    manifest = group_view(runs_dir, name)
    slots = manifest.get("members") or []
    if spawning(manifest):
        raise Refusal(f"group {name} is still starting its members", live=[m.get("run_id") for m in slots if m.get("run_id")], instead=invocation("stop", "--group", name, *registry_args(runs_dir)), why="wait for batch to answer, or stop the group, then clean")
    metas, live, unreadable = {}, [], []
    for slot in slots:
        try:
            metas[slot["index"]] = member_meta(runs_dir, slot)
        except Refusal:
            unreadable.append(slot["index"])
            continue
        meta = metas[slot["index"]]
        if meta and is_live(meta):
            live.append(meta["run_id"])
    if live:
        raise Refusal(f"group {name} has live runs; live work is never removed", live=live, instead=invocation("stop", "--group", name, *registry_args(runs_dir)), why="stop them first, then clean")
    removed, kept = [], []
    for slot in slots:
        meta = metas.get(slot["index"])
        wt = checkout_of(slot, meta)
        if slot["index"] in unreadable:
            if (slot.get("worktree") or {}).get("path") or slot.get("worktree"):
                kept.append({"index": slot["index"], "path": (slot.get("worktree") or {}).get("path"), "reason": "the member's record cannot be read, so whether its work is live or kept is not known"})
            continue
        if not wt:
            continue
        with publish_lock(runs_dir):
            clean_member(runs_dir, slot, meta, wt, force, removed, kept, group=name, landed=landed, untouched_since=untouched_since)
    released = not kept and not unreadable
    if released:
        release_group(runs_dir, name, manifest.get("generation"))
    reply = {"group": name, "removed": removed, "kept": kept, "name_released": released}
    if force:
        reply["forced"] = True
    if not released:
        reply["note"] = "the name stays reserved until every checkout is removed"
    return reply


def occupants(runs_dir, path):
    """Every run in this registry, of any engine and of any group or none, that is live and works inside `path`; and the runs whose record cannot be read, which could."""
    found = []
    for d, _ in iter_runs(runs_dir):
        try:
            m = reap(d)
        except Refusal:
            continue
        if is_live(m) and m.get("cwd") and inside(m["cwd"], path):
            found.append(m)
    # A run whose meta.json does not parse cannot say where it works; the one that can be told is the run whose own directory holds the checkout (a codex member's).
    # 성진: unreadable runs elsewhere are not taken as occupants; refuse on them too if a real case shows one working in a checkout it does not hold.
    blind = [d for d in unreadable_runs(runs_dir) if inside(path, d)]
    return found, blind


def clean_member(runs_dir, slot, meta, wt, force, removed, kept, group=None, landed=None, untouched_since=None):
    """Remove one member's checkout (and, for a claude member, its branch) if that loses nothing, appending to `removed` or `kept`."""
    path = Path(wt["path"])
    repo = wt.get("repo") or wt.get("main")
    entry = {"index": slot["index"], "engine": slot.get("engine") or (meta or {}).get("engine"), "path": str(path), "branch": wt.get("branch")}
    live, blind = occupants(runs_dir, path)
    if live:
        kept.append({**entry, "reason": "a live run works in it", "occupied_by": [{"run_id": m["run_id"], "stop": invocation("stop", "--run", m["run_id"], *registry_args(runs_dir))} for m in live]})
        return
    if blind:
        kept.append({**entry, "reason": "a run whose record cannot be read may work in it", "unreadable_runs": blind})
        return
    evidence, head, checked_out, saved = None, None, None, None
    if path.exists() and untouched_since is not None:
        changed = last_changed(str(path))
        if changed is None:
            kept.append({**entry, "reason": "git cannot read all of it, so what it holds is not known"})
            return
        if changed > untouched_since:
            kept.append({**entry, "reason": "it changed after its run ended, so someone may be working in it"})
            return
    if path.exists() and not (path / ".git").exists():
        # A checkout `git worktree add` never finished (killed partway) has no .git of its own — git would answer for the repository around it instead — and git refuses to remove it; only such a one is deleted by hand, and only with --force.
        if not force:
            kept.append({**entry, "reason": "a checkout git never finished making (no .git in it); clean --force removes it", "instead": invocation("clean", "--group", group, "--force", *registry_args(runs_dir))})
            return
        ok, err = remove_unfinished(repo, path)
        if ok:
            removed.append({**entry, "note": "a checkout git never finished making; removed"})
        else:
            kept.append({**entry, "reason": f"it is not a checkout git left unfinished, so it is not deleted by hand: {err}"})
        return
    if path.exists():
        head = head_commit(str(path))
        checked_out = current_branch(str(path))
        if landed and head and head != wt.get("base"):
            evidence = landed(head) or None
        if evidence and evidence.get("open_pr"):
            kept.append({**entry, "reason": f"its work is in pull request #{evidence['open_pr']}, which is still open; it goes once that is merged", "open_pr": evidence["open_pr"]})
            return
        if evidence and not evidence.get("landed"):
            evidence = None
        detached = detached_commits(repo, path, wt.get("branch"))
        if detached and not evidence:
            # Commits no branch holds would be lost with the checkout, so they are kept whatever --force says.
            kept.append({**entry, "reason": f"{detached} commit(s) on a detached HEAD that no branch holds; put them on a branch first", "instead": f"git -C {path} branch <name>"})
            return
        stranded = submodule_commits_only_here(str(path), repo)
        if stranded:
            # A submodule's commits made in this checkout are stored only in it; they are kept whatever --force says, as detached commits are.
            kept.append({**entry, "reason": f"submodule(s) {', '.join(stranded)} hold commits your checkout's copy does not have, which removing it would lose", "instead": f"fetch them into your checkout first: git -C <your submodule> fetch {path}/<submodule> HEAD"})
            return
        artifacts = (meta or {}).get("startup_artifacts") or {}
        left = leftovers(path, repo, artifacts)
        if left is None:
            kept.append({**entry, "reason": "git cannot read all of it (or does not see a work tree there), so what it holds is not known"})
            return
        if evidence and (left["tracked"] or left["untracked"] or left["ignored"]):
            saved = preserve_leftovers(str(path), f"refs/delegate/leftovers/{group}-{slot['index']}", repo, artifacts)
            if saved.get("kept_because"):
                kept.append({**entry, "reason": f"its work is on GitHub ({evidence['label']}), but what is left in it cannot be kept in git: {saved['kept_because']}", "landed": evidence["label"], "why": saved["kept_because"], "instead": "move what you need into your tree, or clean --force to discard it"})
                return
        elif not force and (left["tracked"] or left["untracked"] or left["ignored"]):
            reasons = []
            if left["tracked"]:
                reasons.append(f"{len(left['tracked'])} changed tracked file(s)")
            if left["untracked"]:
                reasons.append(f"{len(left['untracked'])} untracked file(s)")
            if left["ignored"]:
                reasons.append(f"ignored files under {', '.join(summarize_ignored(left['ignored']))}")
            kept.append({**entry, "reason": "it holds work removing it would lose: " + "; ".join(reasons), "tracked": left["tracked"][:20], "untracked": left["untracked"][:20], "ignored_top": summarize_ignored(left["ignored"]), "instead": "move what you need into your tree, or clean --force to discard it"})
            return
        ok, err = unlock_and_remove(repo, path, force=True)
        if not ok:
            kept.append({**entry, "reason": f"git worktree remove failed: {err}"})
            return
    else:
        # The directory was deleted by hand; git still records the checkout and would refuse to delete its branch.
        prune_worktree(repo, path)
    if not wt.get("branch"):
        removed.append(entry)
        return
    if evidence:
        # Its commits are on GitHub: the member's own branch, and a branch the member made and left at that commit, lose nothing. A branch that was there before the group started stays.
        before = wt.get("branches_before")
        deleted = []
        for b in dict.fromkeys(x for x in (wt["branch"], checked_out) if x):
            if b == evidence.get("default"):
                continue
            made_by_run = b == wt["branch"] or (before is not None and b not in before)
            if made_by_run and (branch_tip(repo, b) == head or commits_beyond(repo, b, wt["base"]) == 0):
                if delete_branch(repo, b)[0]:
                    deleted.append(b)
        out = {**entry, "landed": evidence["label"], "head": head, "branch_deleted": wt["branch"] in deleted, "branches_deleted": deleted, **({"pr": evidence["pr"]} if evidence.get("pr") else {})}
        if saved and saved.get("commit"):
            out["leftovers"] = {k: saved[k] for k in ("ref", "commit", "index_commit", "counts")}
        removed.append(out)
        return
    beyond = commits_beyond(repo, wt["branch"], wt["base"])
    if beyond:
        removed.append({**entry, "branch_kept": wt["branch"], "note": f"the branch has {beyond} commit(s) of the member's and is never deleted"})
        return
    deleted, err = delete_branch(repo, wt["branch"])
    removed.append({**entry, "branch_deleted": deleted, **({} if deleted else {"note": err})})
