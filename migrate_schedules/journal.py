"""The move's record, at <registry>/.migration/journal.json: the inventory it acts on, and per schedule which steps are done with what they need to be undone. Every step writes it as soon as it is done, so the job can stop anywhere and be run again."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path

from delegate.registry import ensure_runs_dir


@contextlib.contextmanager
def held(registry):
    """The move's lock, held by one step from reading the journal to its last write: two steps at once, even on different schedules, would each write back the journal as they read it and erase what the other recorded. A second step waits for the first."""
    ensure_runs_dir(str(registry))
    d = Path(registry) / ".migration"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


class Journal:
    def __init__(self, registry):
        self.dir = Path(registry) / ".migration"
        self.path = self.dir / "journal.json"
        self.backup = self.dir / "backup"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {"inventory": None, "schedules": {}}

    def save(self):
        # The registry made as any command makes it, with the .gitignore that keeps it out of the project's git.
        ensure_runs_dir(str(self.dir.parent))
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        os.replace(tmp, self.path)

    @property
    def inventory(self):
        return self.data.get("inventory")

    def set_inventory(self, inv):
        self.data["inventory"] = inv
        self.save()

    def of(self, name):
        return self.data["schedules"].get(name) or {}

    def update(self, name, **fields):
        self.data["schedules"].setdefault(name, {}).update(fields)
        self.save()
