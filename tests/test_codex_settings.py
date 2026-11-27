"""Which settings a codex turn gets: a flag over the thread's record, no default and no config.toml for a new thread, the read-only a run gets, the service tier, and a thread the registry never saw needing a profile, a model and an effort.

Carried from codex in claude e8461a1 tests/test_settings.py. Its config.toml precedence cases are gone with config defaults: an isolated run names its own model and effort.
"""

from __future__ import annotations

import os
import unittest

from support.harness import FAKE_CODEX_DIR, engine


def setUpModule():
    # In process, the catalog check asks the codex on PATH, and caches its answer: the fake's two-model catalog, never the developer's own install.
    os.environ["PATH"] = f"{FAKE_CODEX_DIR}{os.pathsep}{os.environ.get('PATH', '')}"


codex = engine("delegate.engine").adapter("codex")
Refusal = engine("delegate.errors").Refusal

NONE = {"profile": None, "model": None, "effort": None, "schema": None, "priority": False}
NEW = {**NONE, "model": "fake-big", "effort": "high"}
THREAD = {"engine": "codex", "profile": "read-only", "sandbox": "read-only", "isolated": True, "model": "old-model", "effort": "low", "service_tier": "priority", "read_only": "scratch"}


def settings(flags, record=None, cwd="/work/proj"):
    return codex.settings(flags, record, command="resume" if flags.get("resume") else "start", cwd=cwd)


class Precedence(unittest.TestCase):
    def test_a_new_thread_names_its_model_and_effort(self):
        for missing in ("model", "effort"):
            with self.assertRaises(Refusal) as e:
                settings({**NEW, missing: None})
            self.assertTrue(e.exception.arguments)

    def test_a_new_thread_is_isolated_and_writes_by_default(self):
        s = settings(NEW)
        self.assertEqual((s["isolated"], s["profile"], s["sandbox"], s["service_tier"]), (True, "write", "workspace-write", None))

    def test_a_resume_keeps_the_threads_record(self):
        s = settings({**NONE, "resume": True}, THREAD)
        self.assertEqual((s["sandbox"], s["model"], s["effort"], s["service_tier"], s["read_only"]), ("read-only", "old-model", "low", "priority", "scratch"))
        self.assertEqual(s["changed"], {})

    def test_an_inherited_model_is_not_checked_against_todays_catalog(self):
        s = settings({**NONE, "resume": True}, {**THREAD, "model": "retired-model"})
        self.assertEqual(s["model"], "retired-model")

    def test_a_flag_still_beats_the_record(self):
        s = settings({**NONE, "resume": True, "profile": "write", "model": "fake-small"}, THREAD)
        self.assertEqual((s["sandbox"], s["model"], s["effort"], s["service_tier"]), ("workspace-write", "fake-small", "low", "priority"))
        self.assertEqual(s["changed"], {"profile": "read-only", "model": "old-model"})

    def test_priority_forces_the_tier(self):
        self.assertEqual(settings({**NEW, "priority": True})["service_tier"], "priority")

    def test_a_thread_the_registry_never_saw_needs_profile_model_and_effort(self):
        for missing in ("profile", "model", "effort"):
            with self.subTest(missing=missing):
                flags = {**NONE, "resume": True, "profile": "write", "model": "fake-big", "effort": "high", missing: None}
                with self.assertRaises(Refusal) as e:
                    settings(flags)
                self.assertTrue(e.exception.arguments)
                self.assertEqual(e.exception.fields["help"], "resume --help")

    def test_full_is_danger_full_access(self):
        self.assertEqual(settings({**NEW, "profile": "full"})["sandbox"], "danger-full-access")


class ReadOnlyMarker(unittest.TestCase):
    """Which read-only a run gets. `scratch` writes TMPDIR and ~/.cache, so tests and builds run; `strict` cannot write even a temporary file. A run takes scratch only when it is new or names read-only again, and only isolated; a thread that recorded scratch keeps it; any other read-only thread keeps strict, so no thread's permissions widen without being asked."""

    def marker(self, flags, record=None, cwd="/work/proj"):
        s = settings(flags, record, cwd)
        return s["read_only"], s["read_only_note"]

    def test_a_new_read_only_run_takes_scratch(self):
        self.assertEqual(self.marker({**NEW, "profile": "read-only"}), ("scratch", None))

    def test_a_directory_that_is_itself_tmpdir_makes_it_strict_and_says_why(self):
        tmp = os.environ.get("TMPDIR") or "/tmp"
        os.environ["TMPDIR"] = tmp
        marker, note = self.marker({**NEW, "profile": "read-only"}, cwd=tmp)
        self.assertEqual(marker, "strict")
        self.assertIn("TMPDIR", note)

    def test_a_writing_sandbox_has_no_marker(self):
        for flags, record in ((NEW, None), ({**NEW, "profile": "full"}, None), ({**NONE, "resume": True, "profile": "write"}, THREAD)):
            with self.subTest(profile=flags["profile"]):
                self.assertEqual(self.marker(flags, record), (None, None))

    def test_a_thread_that_recorded_scratch_keeps_it(self):
        self.assertEqual(self.marker({**NONE, "resume": True}, THREAD), ("scratch", None))

    def test_a_strict_thread_stays_strict_unless_asked(self):
        record = {**THREAD, "read_only": "strict"}
        marker, note = self.marker({**NONE, "resume": True}, record)
        self.assertEqual(marker, "strict")
        self.assertIn("resume --profile read-only", note)
        self.assertEqual(self.marker({**NONE, "resume": True, "profile": "read-only"}, record), ("scratch", None))

    def test_a_thread_that_loads_the_users_config_stays_strict_even_when_asked(self):
        record = {**THREAD, "isolated": False}
        for flags in ({**NONE, "resume": True}, {**NONE, "resume": True, "profile": "read-only"}):
            with self.subTest(asked=bool(flags.get("profile"))):
                marker, note = self.marker(flags, record)
                self.assertEqual(marker, "strict")
                self.assertIn("config.toml", note)


if __name__ == "__main__":
    unittest.main()
