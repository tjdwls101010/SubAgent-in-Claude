"""Which settings a claude turn gets: a flag over the session's record, no default model or effort for a new session, and a session the registry never saw needing a profile and an effort (claude keeps its model)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import os

from support.harness import FAKE_CODEX_DIR, FAKE_DIR, engine


def setUpModule():
    # In process, a model alias is checked against the claude on PATH (and cached): the fake's captured list, never the developer's own install.
    os.environ["PATH"] = f"{FAKE_DIR}{os.pathsep}{FAKE_CODEX_DIR}{os.pathsep}{os.environ.get('PATH', '')}"


claude = engine("delegate.engine").adapter("claude")
Refusal = engine("delegate.errors").Refusal

NONE = {"profile": None, "model": None, "effort": None, "schema": None}


class Settings(unittest.TestCase):
    def test_a_new_run_names_its_model_and_effort(self):
        for missing in ("model", "effort"):
            flags = {**NONE, "model": "claude-m", "effort": "high", missing: None}
            with self.assertRaises(Refusal) as e:
                claude.settings(flags)
            self.assertTrue(e.exception.arguments)
            self.assertIn(f"--{missing} is required", e.exception.message)

    def test_profile_defaults_to_write(self):
        s = claude.settings({**NONE, "model": "claude-m", "effort": "high"})
        self.assertEqual((s["engine"], s["profile"], s["model"], s["effort"], s["schema_text"]), ("claude", "write", "claude-m", "high", None))
        self.assertEqual(s["changed"], {})

    def test_an_effort_claude_does_not_take_is_refused(self):
        with self.assertRaises(Refusal) as e:
            claude.settings({**NONE, "model": "claude-m", "effort": "extreme"})
        self.assertTrue(e.exception.arguments)
        self.assertEqual(e.exception.fields["valid_efforts"], ["low", "medium", "high", "xhigh", "max"])

    def test_record_then_flag(self):
        record = {"profile": "read-only", "model": "haiku", "effort": "low", "schema_text": '{"type": "object"}'}
        s = claude.settings({**NONE, "resume": True}, record)
        self.assertEqual((s["profile"], s["model"], s["effort"], s["schema_text"]), ("read-only", "haiku", "low", '{"type": "object"}'))
        s = claude.settings({**NONE, "resume": True, "profile": "full", "effort": "max"}, record)
        self.assertEqual((s["profile"], s["model"], s["effort"]), ("full", "haiku", "max"))
        self.assertEqual(s["changed"], {"profile": "read-only", "effort": "low"})

    def test_unknown_session_needs_a_profile_and_an_effort(self):
        for flags in ({**NONE, "resume": True, "effort": "high"}, {**NONE, "resume": True, "profile": "read-only"}):
            with self.assertRaises(Refusal) as e:
                claude.settings(flags, None, command="resume")
            self.assertTrue(e.exception.arguments)
        s = claude.settings({**NONE, "resume": True, "profile": "read-only", "effort": "high"}, None, command="resume")
        self.assertEqual((s["profile"], s["model"], s["effort"]), ("read-only", None, "high"))

    def test_schema_file(self):
        d = Path(tempfile.mkdtemp())
        (d / "s.json").write_text('{ "type" : "object" }')
        base = {**NONE, "model": "claude-m", "effort": "high"}
        self.assertEqual(json.loads(claude.settings({**base, "schema": str(d / "s.json")})["schema_text"]), {"type": "object"})
        (d / "bad.json").write_text("not json")
        for path in (d / "bad.json", d / "missing.json"):
            with self.assertRaises(Refusal) as e:
                claude.settings({**base, "schema": str(path)})
            self.assertTrue(e.exception.arguments)


if __name__ == "__main__":
    unittest.main()
