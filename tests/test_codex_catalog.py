"""The Codex model catalog as a check before spawning: an unknown model or effort costs nothing and is a command line to change, a valid one passes, and a lookup that breaks never blocks a run. And the version floors a `codex --version` line is read against.

Carried from codex in claude e8461a1 tests/test_catalog.py; the config.toml-sourced cases are gone with config defaults (a run names its model and effort), and `models` itself is tested with step 5.
"""

from __future__ import annotations

import json
import unittest

from support.harness import CODEX_RUN, Sandbox, engine


class ChecksBeforeSpawning(Sandbox):
    def test_an_unknown_model_or_effort_is_refused_and_costs_nothing(self):
        for model, effort, message in (("gpt-nope", "high", "unknown model"), ("fake-small", "ultra", "does not accept effort")):
            with self.subTest(model=model, effort=effort):
                out = self.cli_json("start", "--engine", "codex", "--model", model, "--effort", effort, "x", expect=2)
                self.assertIn(message, out["error"])
                self.assertEqual(out["help"], "start --help")
        self.assertFalse(self.runs_dir().exists() and [d for d in self.runs_dir().iterdir() if not d.name.startswith(".")])

    def test_an_effort_valid_for_its_model_passes(self):
        out = self.codex("start", "--model", "fake-big", "--effort", "ultra", "x")
        self.assertEqual(self.wait_state(out["run_id"])["state"], "completed")

    def test_the_users_config_toml_is_not_a_default(self):
        """An isolated run reads no config.toml, and the skill has no default of its own: the model is what the command line names."""
        (self.root / "codex-home" / "config.toml").write_text('model = "fake-small"\nmodel_reasoning_effort = "low"\n')
        out = self.cli_json("start", "--engine", "codex", "x", "--effort", "high", expect=2)
        self.assertIn("--model is required", out["error"])
        out = self.codex("start", "x")
        self.wait_state(out["run_id"])
        argv = self.codex_calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("-m") + 1], "fake-big")
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertIn("--ignore-user-config", argv)


class ABrokenLookupNeverBlocksARun(Sandbox):
    def test_each_way_the_lookup_can_fail(self):
        broken = ("", "!fail", "!garbage", json.dumps({"models": [{"slug": "fake-big", "supported_reasoning_levels": 5}]}), json.dumps({"models": "not a list"}))
        for catalog in broken:
            with self.subTest(catalog=catalog):
                out = self.codex("start", "--effort", "anything", "x", env={"FAKE_CODEX_MODELS": catalog})
                self.assertEqual(self.wait_state(out["run_id"])["state"], "completed")


class VersionFloors(unittest.TestCase):
    """0.122.0 is the first release with `--ignore-user-config`, which every isolated run passes; 0.160.0 is the first on which the read-only profile was measured to reach both `exec` and `exec resume`. A version that cannot be read is no reason to refuse a run, and no promise that the profile works."""

    def support(self, text):
        out = engine("delegate.codex_cli").support_for(text)
        return out["version"], out["isolation"], out["profile"]

    def test_each_side_of_each_floor(self):
        cases = [("codex-cli 0.121.9", ("0.121.9", False, False)), ("codex-cli 0.122.0", ("0.122.0", True, False)), ("codex-cli 0.159.4", ("0.159.4", True, False)), ("codex-cli 0.160.0", ("0.160.0", True, True)), ("codex-cli 0.161.2", ("0.161.2", True, True)), ("codex-cli 1.0.0", ("1.0.0", True, True))]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.support(text), expected)

    def test_a_pre_release_is_below_its_release(self):
        self.assertEqual(self.support("codex-cli 0.160.0-alpha.3"), ("0.160.0-alpha.3", True, False))
        self.assertEqual(self.support("codex-cli 0.122.0-alpha.1"), ("0.122.0-alpha.1", False, False))

    def test_a_version_that_cannot_be_read_is_unknown(self):
        for text in (None, "", "codex-cli", "not a version"):
            with self.subTest(text=text):
                self.assertEqual(self.support(text), (None, None, False))


class BelowTheIsolationFloor(Sandbox):
    OLD = {"FAKE_CODEX_VERSION": "codex-cli 0.121.0"}

    def test_start_is_refused_and_costs_nothing(self):
        refused = self.codex("start", "x", expect=1, env=self.OLD)
        self.assertEqual((refused["codex_version"], refused["required_version"]), ("0.121.0", "0.122.0"))
        self.assertFalse(self.runs_dir().exists() and [d for d in self.runs_dir().iterdir() if not d.name.startswith(".")])

    def test_an_unreadable_version_is_not_refused(self):
        self.wait_state(self.codex("start", "x", env={"FAKE_CODEX_VERSION": ""})["run_id"])


class NoCodex(Sandbox):
    def test_start_says_codex_is_missing(self):
        out = self.cli_json("start", *CODEX_RUN, "x", expect=1, env={"PATH": f"{self.env['PATH'].split(':', 1)[0]}:/usr/bin:/bin"})
        self.assertIn("codex is not on PATH", out["error"])


if __name__ == "__main__":
    unittest.main()
