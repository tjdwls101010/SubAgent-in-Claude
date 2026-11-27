"""Where the registry lives: one per repository, in its main checkout, found the same from every checkout of it — a linked worktree, a submodule's linked worktree, a subdirectory — and from a plain directory.

Carried from codex in claude e8461a1 tests/test_registry_location.py (a53e26a: a submodule's linked worktree). Its codex-run cases are here with either engine, since the registry is shared.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support.harness import Sandbox, engine, git, make_repo

reg = engine("delegate.registry")


class MainCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = make_repo(self.tmp / "main")

    def of(self, path):
        return Path(reg.resolve_runs_dir(str(path))).parent

    def test_a_linked_worktree_leads_to_the_main_checkout(self):
        git(self.main, "worktree", "add", "-q", "-b", "side", str(self.tmp / "linked"))
        self.assertEqual(self.of(self.tmp / "linked"), self.main)

    def test_a_plain_directory_is_itself(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(self.of(plain), plain)

    def test_a_worktree_of_a_bare_repository_is_itself(self):
        bare = self.tmp / "bare.git"
        git(self.tmp, "clone", "-q", "--bare", str(self.main), str(bare))
        git(bare, "worktree", "add", "-q", str(self.tmp / "bwt"))
        self.assertEqual(self.of(self.tmp / "bwt"), (self.tmp / "bwt").resolve())

    def test_a_linked_worktree_of_a_submodule_leads_to_the_submodules_own_checkout(self):
        sub = make_repo(self.tmp / "sub-src")
        sup = make_repo(self.tmp / "super")
        subprocess.run(["git", "-C", str(sup), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "mod"], capture_output=True, text=True, check=True, env={"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        git(sup, "commit", "-qm", "add submodule")
        git(sup / "mod", "worktree", "add", "-q", str(self.tmp / "modwt"), "-b", "side")
        self.assertEqual(self.of(self.tmp / "modwt"), sup / "mod")
        self.assertEqual(self.of(sup / "mod"), sup / "mod", "a submodule's own checkout is its main checkout")

    def test_a_linked_worktree_whose_main_checkout_is_gone_is_itself(self):
        git(self.main, "worktree", "add", "-q", "-b", "side", str(self.tmp / "linked"))
        shutil.rmtree(self.main)
        self.assertEqual(self.of(self.tmp / "linked"), (self.tmp / "linked").resolve())


class OneRegistryPerRepository(Sandbox):
    def linked(self):
        path = self.root / "linked"
        git(self.project, "worktree", "add", "-q", "-b", "side", str(path))
        return path.resolve()

    def test_a_run_started_in_a_linked_worktree_is_recorded_in_the_main_checkout(self):
        wt = self.linked()
        out = self.codex("start", "x", cwd=wt)
        self.assertTrue(out["events"].startswith(str(self.runs_dir()) + "/"), out["events"])
        self.assertEqual(out["cwd"], str(wt), "it works where it was started")
        self.wait_state(out["run_id"])
        self.assertEqual(self.codex_calls()[-1]["cwd"], str(wt))
        self.assertFalse((wt / ".delegate-runs").exists())

    def test_its_thread_is_found_from_every_checkout(self):
        wt = self.linked()
        first = self.codex("start", "x", cwd=wt)
        self.wait_state(first["run_id"])
        self.assertEqual(self.cli_json("status", "--run", first["run_id"], cwd=wt)["state"], "completed")
        again = self.cli_json("resume", first["run_id"], "go on", cwd=wt)
        self.wait_state(again["run_id"])
        self.assertEqual(self.codex_calls()[-1]["cwd"], str(wt), "the thread goes on in its own directory")
        from_main = self.cli_json("resume", first["native_id"], "and on")
        self.wait_state(from_main["run_id"])

    def test_project_names_a_checkout_as_if_run_from_it(self):
        wt = self.linked()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        out = self.start("x", "--project", str(wt), cwd=elsewhere)
        self.assertTrue(out["events"].startswith(str(self.runs_dir()) + "/"))
        self.assertEqual(out["cwd"], str(wt))


if __name__ == "__main__":
    unittest.main()
