"""`log`: what one run did, as text."""

from __future__ import annotations

from delegate.registry import find_run, resolve_runs_dir

from .snapshot import snapshot_of


def log(*, cwd, ref, runs_dir=None):
    snap = snapshot_of(find_run(resolve_runs_dir(cwd, runs_dir), ref))
    lines = list(snap["lines"])
    lines.append(f"run={snap['meta']['run_id']} state={snap['meta'].get('state')}")
    return "\n".join(lines) + "\n"
