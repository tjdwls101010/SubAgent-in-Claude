"""One group of both engines (a release gate of the plan): one name for `status`, `result`, `stop` and `clean`, a native handle per member in its engine's own terms, a slot that fails to start keeping its place, a checkout of its own for each writer — Claude Code's worktree for a claude member, a detached checkout at its run for a codex member — and a group that keeps showing the runs it started after a member is resumed in a later batch. Then `clean` against runs working inside a checkout, and a name used again.
"""

from __future__ import annotations

import json
import subprocess
import sys

from support.harness import CLAUDE_RUN, CODEX_RUN, ENTRY, Sandbox, engine, git


class MixedGroup(Sandbox):
    def tasks(self, *lines):
        path = self.root / f"tasks-{len(list(self.root.glob('tasks-*')))}.jsonl"
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        return str(path)

    def group_result(self, name, *args):
        proc = self.cli("result", "--group", name, *args, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        header, _, rest = proc.stdout.partition("\n")
        return json.loads(header), rest

    def test_one_group_of_both_engines(self):
        f = self.tasks(
            {"prompt": "c [[fake:write:shared.txt]] [[fake:reply:from claude]]", "engine": "claude", "model": "claude-test-model", "effort": "high", "label": "cl"},
            {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high", "label": "cx"},
            {"prompt": "y", "kind": "resume", "resume": "no-such-run", "label": "gone"},
        )
        reply = self.cli_json("batch", "--group", "mix", "--tasks-file", f, "--worktree", "--profile", "write", timeout=120)
        self.assertEqual((reply["spawned"], reply["requested"]), (2, 3))
        cl, cx, gone = reply["runs"]
        self.assertEqual((cl["engine"], cx["engine"]), ("claude", "codex"))
        self.assertTrue(cl["native_id"], "a claude session id is known at once")
        self.assertIn("error", gone)
        self.assertTrue(cl["worktree"].endswith("/.claude/worktrees/mix-1"))
        self.assertTrue(cx["worktree"].endswith(f"/{cx['run_id']}/wt"))
        self.assertEqual(git(cx["worktree"], "rev-parse", "HEAD"), git(self.project, "rev-parse", "HEAD"))
        self.assertIn("missing_ignored", reply)
        header, body = self.group_result("mix", "--wait", "--wait-timeout", "60")
        self.assertEqual(header["group_state"], "partial", "a slot that never started counts against completed")
        self.assertEqual([m["engine"] for m in header["members"]], ["claude", "codex", None])
        self.assertTrue(all(m["native_id"] for m in header["members"][:2]), "each started member has its engine's handle")
        self.assertIn(f"--- [1:cl] engine=claude run={cl['run_id']} state=completed", body)
        self.assertIn(f"--- [2:cx] engine=codex run={cx['run_id']} state=completed", body)
        st = self.cli_json("status", "--group", "mix")
        self.assertEqual([u["label"] for u in st["unstarted"]], ["gone"])
        self.assertEqual(sorted(st["done"]), sorted([cl["run_id"], cx["run_id"]]))
        listing = self.cli_json("status")["groups"]
        self.assertEqual([(g["group"], g["engines"]) for g in listing], [("mix", ["claude", "codex"])])

        # A next round resumes one member in a new batch; the first group still shows the runs it started.
        again = self.cli_json("batch", "--group", "mix-r2", "--tasks-file", self.tasks({"prompt": "more", "kind": "resume", "resume": cx["run_id"]}), timeout=120)
        self.assertEqual(again["runs"][0]["native_id"], cx["native_id"])
        self.wait_state(again["runs"][0]["run_id"])
        header, _ = self.group_result("mix")
        self.assertEqual([m["run_id"] for m in header["members"][:2]], [cl["run_id"], cx["run_id"]], "slots are pinned to the runs they started")

        out = self.cli_json("clean", "--group", "mix")
        kept = {k["index"]: k for k in out["kept"]}
        self.assertEqual(list(kept), [1], "the claude member left a file; the codex member's checkout holds nothing")
        self.assertEqual(kept[1]["untracked"], ["shared.txt"])
        self.assertEqual([r["engine"] for r in out["removed"]], ["codex"])
        forced = self.cli_json("clean", "--group", "mix", "--force")
        self.assertTrue(forced["name_released"])

    def test_stop_group_reaches_both_engines(self):
        f = self.tasks({"prompt": "a [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "b", "engine": "codex", "model": "fake-big", "effort": "high"})
        reply = self.cli_json("batch", "--group", "both", "--tasks-file", f, timeout=120, env={"FAKE_CODEX_HANG": "60"})
        header, _ = self.group_result("both")
        self.assertEqual(header["group_state"], "running")
        stopped = self.cli_json("stop", "--group", "both", timeout=120)["stopped"]
        self.assertEqual(sorted((e["engine"], e["state"]) for e in stopped), [("claude", "interrupted"), ("codex", "interrupted")])
        self.assertEqual(self.cli_json("status", "--group", "both")["group_state"], "partial")

    def test_a_member_needs_an_engine_model_and_effort_before_anything_starts(self):
        for line, word in (({"prompt": "x"}, "no engine"), ({"prompt": "x", "engine": "codex", "effort": "high"}, "--model"), ({"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "galaxy"}, "effort")):
            with self.subTest(line=line):
                out = self.cli_json("batch", "--group", "bad", "--tasks-file", self.tasks(line), expect=2)
                self.assertIn(word, out["error"])
        self.assertEqual(self.codex_calls(), [])
        self.assertFalse((self.runs_dir() / ".groups" / "bad.json").exists())

    def test_full_is_never_a_group_default(self):
        out = self.cli_json("batch", *CLAUDE_RUN, "--group", "g", "--profile", "full", "--task", "x", expect=2)
        self.assertEqual(out["help"], "batch --help")


class CleanAgainstWorkInsideACheckout(Sandbox):
    def batch(self, name):
        reply = self.cli_json("batch", *CODEX_RUN, "--group", name, "--worktree", "--task", "x", timeout=120)
        self.wait_state(reply["runs"][0]["run_id"])
        return reply["runs"][0]

    def test_a_live_run_of_any_engine_inside_a_checkout_keeps_it_even_forced(self):
        member = self.batch("occ")
        live = self.start("busy [[fake:sleep:30]]", "--cwd", member["worktree"])
        for args in ((), ("--force",)):
            with self.subTest(args=args):
                out = self.cli_json("clean", "--group", "occ", *args)
                self.assertEqual(out["kept"][0]["occupied_by"][0]["run_id"], live["run_id"])
                self.assertFalse(out["name_released"])
        self.cli_json("stop", "--run", live["run_id"])
        self.assertTrue(self.cli_json("clean", "--group", "occ")["name_released"])

    def test_clean_waits_for_a_run_being_published(self):
        """Removal is made under the lock every run is published under: a run starting in the checkout lands before the look (and keeps it) or after the removal, never in between."""
        member = self.batch("lock")
        reg = engine("delegate.registry")
        with reg.publish_lock(str(self.runs_dir())):
            proc = subprocess.Popen([sys.executable, str(ENTRY), "clean", "--group", "lock"], cwd=self.project, env=self.env, stdout=subprocess.PIPE, text=True)
            import time

            time.sleep(1.5)
            self.assertIsNone(proc.poll(), "clean removed a checkout while a run was being published")
        out, _ = proc.communicate(timeout=60)
        self.assertTrue(json.loads(out)["name_released"])

    def test_a_checkout_git_never_finished_is_removed_only_by_force(self):
        member = self.batch("half")
        wt = member["worktree"]
        # As `git worktree add` killed partway leaves it: still under the `initializing` lock it holds for the whole add, and no .git yet. No .git alone is not that (GPT review of the clean fixes, finding 1).
        admin = __import__("pathlib").Path(git(wt, "rev-parse", "--absolute-git-dir"))
        (__import__("pathlib").Path(wt) / ".git").unlink()
        self.assertEqual([k["index"] for k in self.cli_json("clean", "--group", "half", "--force")["kept"]], [1])
        (admin / "locked").write_text("initializing")
        kept = self.cli_json("clean", "--group", "half")
        self.assertFalse(kept["name_released"])
        forced = self.cli_json("clean", "--group", "half", "--force")
        self.assertTrue(forced["name_released"], forced)
        self.assertFalse(__import__("pathlib").Path(wt).exists())

    def test_a_name_used_again_is_a_new_generation(self):
        self.batch("again")
        first = self.cli_json("status", "--group", "again")["generation"]
        self.assertTrue(self.cli_json("clean", "--group", "again")["name_released"])
        self.batch("again")
        self.assertEqual(self.cli_json("status", "--group", "again")["generation"], first + 1)


class ARoundThatResumesAMember(Sandbox):
    def test_its_clean_never_touches_the_checkout_an_earlier_group_made(self):
        first = self.cli_json("batch", *CODEX_RUN, "--group", "r1", "--worktree", "--task", "x", timeout=120)["runs"][0]
        self.wait_state(first["run_id"])
        tasks = self.root / "r2.jsonl"
        tasks.write_text(json.dumps({"prompt": "go on", "kind": "resume", "resume": first["run_id"]}) + "\n")
        second = self.cli_json("batch", "--group", "r2", "--tasks-file", str(tasks), timeout=120)["runs"][0]
        self.wait_state(second["run_id"])
        self.assertEqual(second["cwd"], first["worktree"], "a resumed member keeps working in its checkout")
        out = self.cli_json("clean", "--group", "r2", "--force")
        self.assertEqual((out["removed"], out["kept"]), ([], []))
        self.assertTrue(__import__("pathlib").Path(first["worktree"]).is_dir(), "the checkout is r1's, which r2's clean leaves alone")
        self.assertTrue(self.cli_json("clean", "--group", "r1")["name_released"])


class GroupFlagsBelongToTheirEngine(Sandbox):
    """A group's --model, --effort and --priority are its --engine's: a member of the other engine names its own, and a resumed member keeps what its run recorded."""

    def tasks(self, *lines):
        path = self.root / f"g-{len(list(self.root.glob('g-*')))}.jsonl"
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        return str(path)

    def test_the_other_engines_member_keeps_its_own_settings(self):
        f = self.tasks({"prompt": "c", "engine": "claude", "model": "claude-own-model", "effort": "low"}, {"prompt": "x"})
        reply = self.cli_json("batch", *CODEX_RUN, "--priority", "--group", "own", "--tasks-file", f, timeout=120)
        cl, cx = reply["runs"]
        self.assertNotIn("error", cl, "--priority is the codex members' and never reaches a claude member")
        self.assertEqual((cl["model"], cl["effort"]), ("claude-own-model", "low"))
        self.assertEqual(cx["service_tier"], "priority")

    def test_a_member_of_the_other_engine_without_a_model_is_refused_before_anything_starts(self):
        f = self.tasks({"prompt": "c", "engine": "claude", "effort": "high"}, {"prompt": "x"})
        out = self.cli_json("batch", *CODEX_RUN, "--group", "none", "--tasks-file", f, expect=2)
        self.assertIn("--model", out["error"])
        self.assertEqual(self.codex_calls(), [])

    def test_a_resumed_member_keeps_what_its_run_recorded(self):
        first = self.start("x")
        self.wait_state(first["run_id"])
        f = self.tasks({"prompt": "more", "kind": "resume", "resume": first["run_id"]})
        reply = self.cli_json("batch", "--engine", "claude", "--model", "claude-other-model", "--effort", "low", "--group", "r", "--tasks-file", f, timeout=120)
        (row,) = reply["runs"]
        self.assertEqual((row["model"], row["effort"]), (first["model"], first["effort"]))

    def test_a_resume_line_its_run_cannot_take_starts_nothing(self):
        first = self.start("x")
        self.wait_state(first["run_id"])
        f = self.tasks({"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"}, {"prompt": "more", "kind": "resume", "resume": first["run_id"], "effort": "galaxy"})
        out = self.cli_json("batch", "--group", "bad-resume", "--tasks-file", f, expect=2)
        self.assertIn("line 2", out["error"])
        self.assertEqual(self.codex_calls(), [])


class OverlapsAcrossRepositories(Sandbox):
    def test_the_same_path_in_two_repositories_is_two_overlaps(self):
        from support.harness import make_repo

        other = self.root / "other"
        make_repo(other)
        path = self.root / "ov.jsonl"
        lines = [{"prompt": "w [[fake:write:README.md]]", "cwd": str(cwd)} for cwd in (self.project, self.project, other, other)]
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        reply = self.cli_json("batch", *CLAUDE_RUN, "--group", "ov", "--tasks-file", str(path), timeout=120)
        for r in reply["runs"]:
            self.wait_state(r["run_id"])
        proc = self.cli("result", "--group", "ov", timeout=120)
        header = json.loads(proc.stdout.split("\n", 1)[0])
        self.assertEqual(sorted(header["overlaps"].values()), [[1, 2], [3, 4]], header["overlaps"])
