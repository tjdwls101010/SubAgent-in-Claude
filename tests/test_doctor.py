"""`doctor` through the CLI with the fake claude, and the retry and rate-limit lines a run's log carries."""

from __future__ import annotations

import json

from support.harness import CLAUDE_RUN, Sandbox


class Doctor(Sandbox):
    def doctor(self, expect=0, **kw):
        return self.cli_json("doctor", expect=expect, timeout=120, **kw)

    def test_healthy(self):
        d = self.doctor()
        self.assertTrue(d["ok"])
        self.assertEqual(d["blockers"], [])
        c = d["engines"]["claude"]
        self.assertTrue(c["ok"])
        self.assertEqual(c["version"], "2.1.314")
        self.assertEqual(c["floor"], "2.1.314")
        self.assertEqual(c["auth"], {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "apiProvider": None})
        self.assertNotIn("email", json.dumps(d))
        self.assertEqual(d["registry"]["path"], str(self.project / ".delegate-runs"))
        self.assertTrue(d["invocation"].startswith('uv run "'))
        self.assertEqual([c for c in self.fake_calls() if "-p" in c["argv"]], [], "doctor starts no worker")

    def test_not_logged_in_is_a_blocker(self):
        d = self.doctor(expect=3, env={"FAKE_CLAUDE_AUTH": json.dumps({"loggedIn": False})})
        self.assertFalse(d["ok"])
        self.assertTrue(any("not logged in" in b for b in d["blockers"]))

    def test_no_claude_is_a_blocker(self):
        d = self.doctor(expect=3, env={"PATH": "/usr/bin:/bin"})
        self.assertTrue(any("not on PATH" in b for b in d["blockers"]))

    def test_widening_settings_are_warned(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"sandbox": {"excludedCommands": ["docker"]}}))
        d = self.doctor()
        self.assertTrue(any("excludedCommands" in w for w in d["warnings"]))
        self.assertEqual([x["setting"] for x in d["engines"]["claude"]["profile_exceptions"]["read-only"]], ["sandbox.excludedCommands"])

    def test_registry_that_does_not_exist_yet(self):
        d = self.cli_json("doctor", "--runs-dir", str(self.root / "nope" / "a" / "b"), timeout=120)
        self.assertTrue(d["ok"], d["blockers"])
        self.assertTrue(d["registry"]["writable"])

    def test_registry_live_runs(self):
        live = self.start("x [[fake:sleep:30]]")
        d = self.doctor()
        self.assertEqual(d["registry"]["live_runs"], [live["run_id"]])
        self.cli_json("stop", "--run", live["run_id"])


class OldClaude(Sandbox):
    fake_version = "2.1.300"

    def test_below_the_floor_is_a_blocker(self):
        d = self.cli_json("doctor", expect=3)
        self.assertTrue(any("2.1.314" in b for b in d["blockers"]))


class LimitsInLogs(Sandbox):
    def test_retry_and_rate_limit_lines(self):
        reply = self.start("x [[fake:retry:2]] [[fake:silent:0.2]] [[fake:ratelimit_late:allowed_warning]]")
        self.wait_state(reply["run_id"])
        lines = self.cli("log", "--run", reply["run_id"]).stdout.splitlines()
        self.assertIn("retry attempt=1/10 error=overloaded status=529", lines)
        self.assertIn("retry attempt=2/10 error=overloaded status=529", lines)
        self.assertEqual([l for l in lines if l.startswith("ratelimit ")], ["ratelimit allowed 5h=2% 7d=42%", "ratelimit allowed_warning 5h=2% 7d=42%"])
        self.assertEqual(self.status(reply["run_id"])["rate_limit"]["status"], "allowed_warning")
