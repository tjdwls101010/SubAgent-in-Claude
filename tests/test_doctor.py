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

    def test_no_claude_is_a_blocker_when_claude_is_asked_about(self):
        d = self.cli_json("doctor", "--engine", "claude", expect=3, timeout=120, env={"PATH": "/usr/bin:/bin"})
        self.assertTrue(any("not on PATH" in b for b in d["blockers"]))

    def test_one_missing_engine_is_a_warning_and_none_installed_a_blocker(self):
        from support.harness import FAKE_DIR

        d = self.doctor(env={"PATH": f"{FAKE_DIR}:/usr/bin:/bin"})
        self.assertTrue(d["ok"], d["blockers"])
        self.assertIn("codex: codex is not on PATH", d["warnings"])
        self.assertFalse(d["engines"]["codex"]["installed"])
        self.assertFalse(d["engines"]["codex"]["ok"], "the engine itself is not ready; only the verdict lets other runs go")
        d = self.doctor(expect=3, env={"PATH": "/usr/bin:/bin"})
        self.assertTrue(any("no engine is installed" in b for b in d["blockers"]))

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

    def test_a_registry_that_is_a_file_is_a_blocker(self):
        f = self.root / "regfile"
        f.write_text("")
        d = self.cli_json("doctor", "--runs-dir", str(f), expect=3, timeout=120)
        self.assertTrue(any("cannot be written" in b for b in d["blockers"]))

    def test_checkouts_are_credited_to_their_own_group(self):
        from support.harness import CLAUDE_RUN, CODEX_RUN

        for name in ("fix", "fix-db"):
            reply = self.cli_json("batch", *CLAUDE_RUN, "--group", name, "--worktree", "--task", "x", timeout=120)
            self.wait_state(reply["runs"][0]["run_id"])
        reply = self.cli_json("batch", *CODEX_RUN, "--group", "cx", "--worktree", "--task", "x", timeout=120)
        self.wait_state(reply["runs"][0]["run_id"])
        d = self.doctor()
        self.assertEqual(sorted(d["registry"]["groups"]), ["cx", "fix", "fix-db"])
        got = sorted((w["path"].rsplit("/", 2)[-2] if w["path"].endswith("/wt") else w["path"].rsplit("/", 1)[1], w["group"]) for w in d["registry"]["worktrees"])
        self.assertEqual(got, sorted([("fix-1", "fix"), ("fix-db-1", "fix-db"), (reply["runs"][0]["run_id"], "cx")]))

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


class CodexDoctor(Sandbox):
    """doctor's codex section with the fake codex: version and what it can run, login (whose failure is a blocker, and a config problem named as one), CODEX_HOME, and what config.toml sets — which runs do not read."""

    def codex_doctor(self, expect=0, **kw):
        return self.cli_json("doctor", "--engine", "codex", expect=expect, timeout=120, **kw)

    def test_healthy(self):
        d = self.codex_doctor()
        c = d["engines"]["codex"]
        self.assertEqual((c["version"], c["isolation"], c["read_only_scratch"], c["login"]["cause"]), ("0.160.0", True, True, "authenticated"))
        self.assertEqual(c["codex_home"], str(self.root / "codex-home"))
        self.assertTrue(c["codex_home_from_env"])
        self.assertEqual(self.codex_calls(), [], "doctor starts no worker")

    def test_not_logged_in_is_a_blocker_and_a_config_problem_says_so(self):
        d = self.codex_doctor(expect=3, env={"FAKE_CODEX_LOGIN_RC": "1"})
        self.assertTrue(any("not logged in" in b for b in d["blockers"]))
        d = self.codex_doctor(expect=3, env={"FAKE_CODEX_LOGIN_RC": "1", "FAKE_CODEX_LOGIN_OUT": "Error loading config.toml: invalid"})
        self.assertTrue(any("not an auth one" in b for b in d["blockers"]))

    def test_below_the_isolation_floor_is_a_blocker_and_below_the_profile_floor_a_warning(self):
        d = self.codex_doctor(expect=3, env={"FAKE_CODEX_VERSION": "codex-cli 0.121.0"})
        self.assertTrue(any("0.122.0" in b for b in d["blockers"]))
        d = self.codex_doctor(env={"FAKE_CODEX_VERSION": "codex-cli 0.159.0"})
        self.assertTrue(any("stricter sandbox" in w for w in d["warnings"]))

    def test_config_toml_is_reported_not_used(self):
        (self.root / "codex-home" / "config.toml").write_text('sandbox_mode = "danger-full-access"\napproval_policy = "never"\n')
        c = self.codex_doctor()["engines"]["codex"]
        self.assertEqual(c["config"], {"path": str(self.root / "codex-home" / "config.toml"), "sandbox_mode": "danger-full-access", "approval_policy": "never"})
