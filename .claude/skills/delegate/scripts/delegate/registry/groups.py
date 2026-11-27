"""Group manifests under .delegate-runs/.groups/: a name reserved by `batch` until `clean` releases it, and its members — one slot each, pinned to the run that slot started, of whichever engine.

A name can be used again once released; each use has its own `generation`, counted in `.groups/<name>.gen`, which outlives the manifest, so a wait that began on one use of a name is never answered with another's members.
"""

from __future__ import annotations

import re
from pathlib import Path

from delegate.errors import NotFound, Refusal
from delegate.util import iso, now

from .locks import locked, read_json, write_json
from .runs import ensure_runs_dir

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def valid_group_name(name):
    return bool(NAME.match(name or ""))


def _path(runs_dir, name):
    return Path(runs_dir) / ".groups" / f"{name}.json"


def _gen_path(runs_dir, name):
    return Path(runs_dir) / ".groups" / f"{name}.gen"


def _lock(runs_dir, name):
    return locked(Path(runs_dir) / ".locks" / f"group-{name}.lock")


def claim_group(runs_dir, name, fields):
    """Reserve `name` and write its manifest with `fields` and the next generation; Refusal when the name is already reserved."""
    ensure_runs_dir(runs_dir)
    groups = Path(runs_dir) / ".groups"
    if groups.exists() and not groups.is_dir():
        raise Refusal(f"{groups} is not a directory, so no group can be recorded")
    groups.mkdir(exist_ok=True)
    with _lock(runs_dir, name):
        if _path(runs_dir, name).exists():
            raise Refusal(f"group name {name!r} is reserved by an earlier batch until `clean` releases it", group=name)
        try:
            generation = int(_gen_path(runs_dir, name).read_text().strip() or 0) + 1
        except (OSError, ValueError):
            generation = 1
        _gen_path(runs_dir, name).write_text(f"{generation}\n")
        manifest = {"name": name, "generation": generation, "created_at": now(), "created": iso(now()), "members": [], **fields}
        write_json(_path(runs_dir, name), manifest)
        return manifest


def update_slot(runs_dir, name, generation, index, fields):
    """Fill in one member's slot as soon as its run exists, so an interrupted batch still leaves its live members in the group. Nothing when the name was released or taken by another generation meanwhile."""
    with _lock(runs_dir, name):
        manifest = read_json(_path(runs_dir, name))
        if manifest is None or manifest.get("generation") != generation:
            return None
        for m in manifest.get("members") or []:
            if m.get("index") == index:
                m.update(fields)
        write_json(_path(runs_dir, name), manifest)
        return manifest


def update_group(runs_dir, name, generation, fields):
    """Add `fields` to a group's manifest, under its lock; None when the group is gone or is another generation."""
    with _lock(runs_dir, name):
        manifest = read_json(_path(runs_dir, name))
        if manifest is None or manifest.get("generation") != generation:
            return None
        manifest.update(fields)
        write_json(_path(runs_dir, name), manifest)
        return manifest


def group_view(runs_dir, name):
    """A group's manifest; NotFound when there is none, Refusal when it does not parse."""
    try:
        manifest = read_json(_path(runs_dir, name))
    except ValueError as e:
        raise Refusal(f"the manifest of group {name!r} does not parse: {e}")
    if manifest is None:
        raise NotFound(f"no group {name!r} in {runs_dir}")
    return manifest


def release_group(runs_dir, name, generation):
    """Release the name, if it is still the generation `clean` looked at."""
    with _lock(runs_dir, name):
        manifest = read_json(_path(runs_dir, name))
        if manifest is not None and manifest.get("generation") == generation:
            _path(runs_dir, name).unlink(missing_ok=True)


def list_groups(runs_dir):
    d = Path(runs_dir) / ".groups"
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            m = read_json(p)
        except ValueError:
            continue
        if m:
            out.append(m)
    return out
