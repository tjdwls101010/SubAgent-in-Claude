"""Continuing a Codex thread: every recorded setting re-asserted on the resumed turn (`codex exec resume` takes no `-s` or `-C`), the read-only a turn gets, threads the registry never saw, one live turn per thread, and handing a run to the other engine.

Carried from codex in claude e8461a1 tests/test_resume.py. Dropped with reason: the config.toml precedence cases (no config defaults), `--force` (one live turn per thread, with no override, as the claude skill had), and the legacy boolean tier and pre-isolation threads (the old registries are not read).
"""

from __future__ import annotations

import json
import os
import signal
import threading

from support.harness import CLAUDE_RUN, CODEX_RUN, Sandbox


def worker_calls(calls):
    return [c for c in calls if "-p" in c["argv"]]


class CodexResume(Sandbox):
    def test_every_recorded_setting_reaches_the_resumed_turn(self):
        schema = self.root / "s.json"
        schema.write_text('{"type": "object"}')
        first = self.codex("start", "x", "--profile", "read-only", "--model", "fake-small", "--effort", "low", "--priority", "--schema", str(schema))
        self.wait_state(first["run_id"])
        a = self.codex_calls()[-1]["argv"]
        again = self.cli_json("resume", first["run_id"], "y")
        self.wait_state(again["run_id"])
        b = self.codex_calls()[-1]["argv"]
        self.assertEqual(b[:4], ["exec", "resume", first["native_id"], "--json"])
        for key in ("default_permissions", "model_reasoning_effort", "service_tier"):
            self.assertEqual(self.config_values(a)[key], self.config_values(b)[key], key)
        self.assertEqual(b[b.index("-m") + 1], "fake-small")
        self.assertIn("--output-schema", b)
        self.assertIn("--ignore-user-config", b)
        for flag in ("-s", "--sandbox", "-C", "--cd"):
            self.assertNotIn(flag, b)
        self.assertEqual(again["native_id"], first["native_id"])

    def test_the_resumed_turn_runs_in_the_threads_directory(self):
        sub = self.project / "sub"
        sub.mkdir()
        first = self.codex("start", "x", "--cwd", str(sub))
        self.wait_state(first["run_id"])
        again = self.cli_json("resume", first["run_id"], "y", "--project", str(self.project), cwd=self.root)
        self.wait_state(again["run_id"])
        self.assertEqual(self.codex_calls()[-1]["cwd"], str(sub))

    def test_changing_the_profile_is_explicit_and_recorded(self):
        first = self.codex("start", "x", "--profile", "read-only")
        self.wait_state(first["run_id"])
        again = self.cli_json("resume", first["run_id"], "y", "--profile", "write")
        self.assertEqual((again["profile_changed_from"], again["sandbox"]), ("read-only", "workspace-write"))
        self.wait_state(again["run_id"])
        self.assertIn('sandbox_mode="workspace-write"', self.codex_calls()[-1]["argv"])


class ReadOnlyWritesScratch(Sandbox):
    def test_a_read_only_run_and_its_resume_get_the_profile(self):
        first = self.codex("start", "x", "--profile", "read-only")
        self.assertEqual((first["read_only"], first.get("read_only_note")), ("scratch", None))
        self.wait_state(first["run_id"])
        self.assertEqual(self.config_values(self.codex_calls()[-1]["argv"])["default_permissions"], '"codex_skill_read_only"')
        again = self.cli_json("resume", first["run_id"], "y")
        self.assertEqual(again["read_only"], "scratch")
        self.wait_state(again["run_id"])
        self.assertNotIn("sandbox_mode", self.config_values(self.codex_calls()[-1]["argv"]))

    def test_a_writing_run_has_no_marker(self):
        out = self.codex("start", "x")
        self.assertNotIn("read_only", out)
        self.wait_state(out["run_id"])

    def test_a_codex_below_the_profile_floor_gets_the_stricter_sandbox_and_says_why(self):
        out = self.codex("start", "x", "--profile", "read-only", env={"FAKE_CODEX_VERSION": "codex-cli 0.159.0"})
        self.assertEqual(out["read_only"], "strict")
        self.assertIn("0.160.0", out["read_only_note"])
        self.wait_state(out["run_id"])
        self.assertIn('sandbox_mode="read-only"', self.codex_calls()[-1]["argv"])


class ThreadsTheRegistryNeverSaw(Sandbox):
    """A thread started outside this skill has no recorded sandbox, model or effort, so the caller names them."""

    def test_an_unknown_ref_is_passed_through_under_what_was_named(self):
        out = self.cli_json("resume", "thread-from-elsewhere", "go on", "--engine", "codex", "--profile", "read-only", "--cwd", str(self.project), "--model", "fake-big", "--effort", "high")
        self.assertEqual(out["native_id"], "thread-from-elsewhere")
        self.assertIn("not in the registry", out["note"])
        self.wait_state(out["run_id"])
        self.assertEqual(self.codex_calls()[-1]["argv"][:3], ["exec", "resume", "thread-from-elsewhere"])

    def test_what_is_missing_is_refused_before_anything_is_claimed(self):
        given = {"--engine": "codex", "--profile": "write", "--cwd": str(self.project), "--model": "fake-big", "--effort": "high"}
        for missing in given:
            with self.subTest(missing=missing):
                args = [x for k, v in given.items() if k != missing for x in (k, v)]
                out = self.cli_json("resume", "thread-x", "go", *args, expect=2)
                self.assertIn(missing, out["error"])
        self.assertEqual(self.codex_calls(), [])

    def test_a_run_that_never_recorded_a_thread_cannot_be_resumed(self):
        (self.root / "empty.jsonl").write_text("")
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(self.root / "empty.jsonl")}, expect=1)
        self.assertIsNone(out["native_id"])
        refused = self.cli_json("resume", out["run_id"], "again", expect=1)
        self.assertIn("nothing to resume", refused["error"])


class OneTurnPerThread(Sandbox):
    def test_simultaneous_resumes_of_a_known_thread_have_one_winner(self):
        first = self.codex("start", "x")
        self.wait_state(first["run_id"])
        out = []

        def go():
            out.append(self.cli("resume", first["native_id"], "again", env={"FAKE_CODEX_HANG": "5"}))

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(p.returncode for p in out), [0, 1], [p.stdout for p in out])
        refused = json.loads(next(p for p in out if p.returncode == 1).stdout)
        self.assertIn("live run", refused["error"])
        self.assertIn("result", refused["instead"], "a codex turn takes no message: wait, or stop and resume")
        for p in out:
            if p.returncode == 0:
                self.cli_json("stop", "--run", json.loads(p.stdout)["run_id"])

    def test_an_orphan_whose_codex_still_writes_holds_the_thread(self):
        first = self.codex("start", "x", env={"FAKE_CODEX_HANG": "60"})
        meta = self.wait_for(lambda: (m := json.loads((self.run_dir(first["run_id"]) / "meta.json").read_text())).get("worker_pid") and m, what="its worker")
        os.kill(meta["supervisor_pid"], signal.SIGKILL)
        self.wait_for(lambda: self.status(first["run_id"])["stored_state"] == "orphaned", what="the orphan")
        refused = self.cli_json("resume", first["native_id"], "again", expect=1)
        self.assertIn("still writing", refused["error"])
        self.cli_json("stop", "--run", first["run_id"])


class AnotherEngine(Sandbox):
    """A conversation does not cross engines: resuming a run on the other engine is refused with the two commands that hand the work over in the same directory."""

    def test_a_claude_run_resumed_as_codex(self):
        first = self.start("x")
        self.wait_state(first["run_id"])
        out = self.cli_json("resume", first["run_id"], "y", "--engine", "codex", expect=2)
        self.assertEqual(out["help"], "resume --help")
        read, start = out["instead"]
        self.assertIn(f"result --run {first['run_id']}", read)
        self.assertIn("start --engine codex", start)
        self.assertIn(f"--cwd {self.project}", start)
        self.assertIn("unverified draft", out["why"])

    def test_a_codex_thread_named_with_engine_claude(self):
        first = self.codex("start", "x")
        self.wait_state(first["run_id"])
        out = self.cli_json("resume", first["native_id"], "y", "--engine", "claude", expect=2)
        self.assertIn("start --engine claude", out["instead"][1])


class ALateThreadId(Sandbox):
    def test_a_run_whose_events_name_the_thread_before_its_meta_does_holds_it(self):
        """Codex names a thread in its first event and the supervisor records it on its next step: in between, the events are what say the thread is busy."""
        first = self.codex("start", "x", env={"FAKE_CODEX_HANG": "60"})
        path = self.run_dir(first["run_id"]) / "meta.json"
        meta = json.loads(path.read_text())
        path.write_text(json.dumps({**meta, "native_id": None}))
        refused = self.cli_json("resume", first["native_id"], "again", "--engine", "codex", "--profile", "write", "--cwd", str(self.project), "--model", "fake-big", "--effort", "high", expect=1)
        self.assertIn("live run", refused["error"])
        self.cli_json("stop", "--run", first["run_id"])


class ThreadNames(Sandbox):
    def test_a_thread_resumed_by_name_holds_it_under_its_name_and_its_id(self):
        """`codex exec resume <name>` answers with the thread's id: the run is found, and its thread held, by either."""
        given = ["--engine", "codex", "--profile", "write", "--cwd", str(self.project), "--model", "fake-big", "--effort", "high"]
        first = self.cli_json("resume", "saved thread", "go", *given, env={"FAKE_CODEX_HANG": "60", "FAKE_CODEX_THREAD_ID": "019f0000-0000-7000-0000-0000000000aa"})
        self.wait_for(lambda: self.status(first["run_id"])["native_id"] == "019f0000-0000-7000-0000-0000000000aa", what="the confirmed id")
        for ref in ("saved thread", "019f0000-0000-7000-0000-0000000000aa"):
            with self.subTest(ref=ref):
                refused = self.cli_json("resume", ref, "again", expect=1)
                self.assertIn("live run", refused["error"])
        self.cli_json("stop", "--run", first["run_id"])

    def test_a_new_effort_is_checked_against_the_threads_own_model(self):
        first = self.codex("start", "x", "--model", "fake-small", "--effort", "low")
        self.wait_state(first["run_id"])
        refused = self.cli_json("resume", first["run_id"], "y", "--effort", "ultra", expect=2)
        self.assertIn("does not accept effort", refused["error"])

