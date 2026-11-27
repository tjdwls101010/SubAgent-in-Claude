"""`models`: what can be chosen now, per engine — the models (codex: its catalog; claude: the aliases `/model` offers) with their efforts, and the account's limits as they stand."""

from __future__ import annotations

from delegate.engine import ENGINES, adapter
from delegate.registry import resolve_runs_dir
from delegate.util import iso

NOTE = "a model the catalog lists can still be one your account cannot use: the first turn of a run on it fails with the engine's own error"


def models(*, cwd, engine=None, runs_dir=None):
    registry = resolve_runs_dir(cwd, runs_dir)
    out = {}
    for name in [engine] if engine else list(ENGINES):
        a = adapter(name)
        q = a.quota(None, registry)
        out[name] = {**a.models(), "quota": {**q, "observed_at": iso(q.get("observed_at")), "resets_at": iso(q.get("resets_at"))}}
    return {"engines": out, "note": NOTE}
