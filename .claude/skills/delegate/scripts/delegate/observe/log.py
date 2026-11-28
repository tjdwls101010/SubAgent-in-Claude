"""`log`: what one run did, as text."""

from __future__ import annotations

from delegate.engine import adapter
from delegate.registry import find_run, resolve_runs_dir

from .rows import load
from .snapshot import snapshot_of


def log(*, cwd, ref, runs_dir=None):
    snap = snapshot_of(find_run(resolve_runs_dir(cwd, runs_dir), ref))
    lines = list(snap["lines"])
    lines.append(f"run={snap['meta']['run_id']} state={snap['meta'].get('state')}")
    return "\n".join(lines) + "\n"


def settled_lines(*, cwd, ref, runs_dir=None):
    """What `log` prints of the run so far, without the calls still running: a follower printing new lines as they come would otherwise print a call twice, once running and once done."""
    meta, v = load(find_run(resolve_runs_dir(cwd, runs_dir), ref))
    if "rows" in v:
        v = {**v, "rows": [r for r in v["rows"] if r.get("kind") != "running"], "unparsed": 0}
    return adapter(meta["engine"]).log_lines(v)
