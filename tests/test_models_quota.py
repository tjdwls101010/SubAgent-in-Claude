"""What can be chosen and how much is left: `models` per engine from the engines' own answers (the codex catalog, claude's `/model`), the account limits read from `codex app-server` or a rollout and from claude's `/usage` or a run's rate-limit event, a start refused only when a fresh reading shows its engine's limit used up until a time to come, and a run the limit ends recorded `quota_exhausted` with the way to hand it over.

Expected values come from real captures: tests/support/fixtures/codex/rate-limits-exhausted.json (`account/rateLimits/read`, codex 0.167.1), codex/rollout-token-count.jsonl (a rollout's `token_count` event), codex/usage-limit.jsonl (a turn that hit the limit), claude/model.json (`claude -p /model`), claude/usage.jsonl (`claude -p /usage`). Reset times are moved into the future where a test needs a limit still in force; everything else is as captured.
"""

from __future__ import annotations

import json
import time

from support.harness import CLAUDE_FIXTURES, CLAUDE_RUN, CODEX_FIXTURES, CODEX_RUN, FAST_TIMINGS, Sandbox


def exhausted_answer(path, resets_in=3600):
    answer = json.loads((CODEX_FIXTURES / "rate-limits-exhausted.json").read_text())
    for rl in [answer["result"]["rateLimits"], *answer["result"]["rateLimitsByLimitId"].values()]:
        rl["primary"]["resetsAt"] = int(time.time()) + resets_in
        if resets_in < 0:
            # Once the full window has reset, ordinary use is allowed again, as the server then says.
            rl["rateLimitReachedType"] = None
    if resets_in < 0:
        answer["result"]["ordinaryUsageAllowed"] = True
    path.write_text(json.dumps(answer))
    return str(path)


class Models(Sandbox):
    def test_each_engine_lists_what_can_be_chosen_and_its_limits(self):
        out = self.cli_json("models", timeout=120)
        codex = {m["slug"]: m for m in out["engines"]["codex"]["models"]}
        self.assertEqual(codex["fake-small"]["efforts"], ["low", "medium", "high"])
        self.assertNotIn("base_instructions", json.dumps(out))
        claude = [m["alias"] for m in out["engines"]["claude"]["models"]]
        self.assertEqual(claude[:4], ["sonnet", "opus", "haiku", "fable"], "as /model listed them")
        self.assertEqual(out["engines"]["claude"]["efforts"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(out["engines"]["codex"]["quota"]["state"], "ok")
        self.assertEqual(out["engines"]["codex"]["quota"]["source"], "app-server")
        self.assertEqual([l["kind"] for l in out["engines"]["claude"]["quota"]["limits"]], [], "the captured /usage limits have all reset by now")
        self.assertIn("first turn", out["note"])

    def test_a_list_that_cannot_be_read_says_so(self):
        out = self.cli_json("models", env={"FAKE_CODEX_MODELS": "!fail", "FAKE_CLAUDE_MODEL_LIST": "fail", "FAKE_CODEX_RATE_LIMITS": "!fail"}, timeout=120)
        self.assertIsNone(out["engines"]["codex"]["models"])
        self.assertIn("could not be read", out["engines"]["codex"]["error"])
        self.assertIsNone(out["engines"]["claude"]["models"])
        self.assertEqual(out["engines"]["codex"]["quota"]["state"], "unknown")

    def test_a_claude_alias_it_does_not_offer_is_refused_and_a_full_id_passes(self):
        out = self.cli_json("start", "--engine", "claude", "--model", "sonett", "--effort", "high", "x", expect=2)
        self.assertIn("sonett", out["error"])
        self.assertIn("opus", out["known_aliases"])
        ok = self.start("x", "--model", "claude-future-9")
        self.wait_state(ok["run_id"])
        ok = self.start("x", "--model", "sonnet[1m]")
        self.wait_state(ok["run_id"])


class CodexLimit(Sandbox):
    def test_a_start_is_refused_while_the_limit_is_used_up(self):
        env = {"FAKE_CODEX_RATE_LIMITS": exhausted_answer(self.root / "rl.json")}
        out = self.codex("start", "x", env=env, expect=1)
        self.assertEqual((out["error"], out["engine"], out["source"]), ("quota_exhausted", "codex", "app-server"))
        self.assertTrue(out["resets_at"])
        self.assertIn("start --engine claude", out["instead"])
        self.assertIn(f"--cwd {self.project}", out["instead"])
        self.assertEqual(self.codex_calls(), [], "nothing started")
        ok = self.start("the other engine still starts", env=env)
        self.wait_state(ok["run_id"])

    def test_a_limit_whose_reset_has_passed_refuses_nothing(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_RATE_LIMITS": exhausted_answer(self.root / "rl.json", resets_in=-60)})
        self.wait_state(out["run_id"])

    def fresh_rollout(self, age=0):
        """The captured token_count event, its 5h window still full, written `age` seconds ago."""
        from datetime import datetime, timezone

        event = json.loads((CODEX_FIXTURES / "rollout-token-count.jsonl").read_text())
        event["payload"]["rate_limits"]["primary"]["resets_at"] = int(time.time()) + 3600
        event["timestamp"] = datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat().replace("+00:00", "Z")
        return event

    def test_an_old_rollout_reading_refuses_nothing(self):
        # A limit can reset early, be raised or be paid past, and a refused start writes no new reading: only a fresh one refuses.
        rollouts = self.root / "codex-home" / "sessions" / "2026" / "11" / "27"
        rollouts.mkdir(parents=True)
        (rollouts / "rollout-2026-11-27T01-47-16-x.jsonl").write_text(json.dumps(self.fresh_rollout(age=3600)) + "\n")
        out = self.codex("start", "x", env={"FAKE_CODEX_RATE_LIMITS": "!fail"})
        self.wait_state(out["run_id"])

    def test_an_app_server_that_never_answers_delays_a_start_but_refuses_nothing(self):
        began = time.time()
        out = self.codex("start", "x", env={"FAKE_CODEX_RATE_LIMITS": "!silent"}, timeout=60)
        self.wait_state(out["run_id"])
        self.assertLess(time.time() - began, 40)

    def test_ordinary_use_the_server_allows_is_not_exhausted(self):
        path = self.root / "rl.json"
        exhausted_answer(path)
        answer = json.loads(path.read_text())
        answer["result"]["ordinaryUsageAllowed"] = True  # a full window paid past with credits
        path.write_text(json.dumps(answer))
        out = self.codex("start", "x", env={"FAKE_CODEX_RATE_LIMITS": str(path)})
        self.wait_state(out["run_id"])

    def test_without_the_app_server_the_newest_rollout_is_read(self):
        event = self.fresh_rollout()
        rollouts = self.root / "codex-home" / "sessions" / "2026" / "11" / "28"
        rollouts.mkdir(parents=True)
        (rollouts / "rollout-2026-11-28T01-47-16-x.jsonl").write_text(json.dumps(event) + "\n")
        out = self.codex("start", "x", env={"FAKE_CODEX_RATE_LIMITS": "!fail"}, expect=1)
        self.assertEqual((out["error"], out["source"]), ("quota_exhausted", "rollout"))
        q = self.cli_json("models", "--engine", "codex", env={"FAKE_CODEX_RATE_LIMITS": "!fail"})["engines"]["codex"]["quota"]
        self.assertEqual([(w["kind"], w["used_percent"]) for w in q["windows"]], [("5h", 100.0), ("7d", 44.0)])

    def test_a_turn_the_limit_ends_is_quota_exhausted_with_the_way_over(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "usage-limit.jsonl"), "FAKE_CODEX_EXIT": "1"})
        row = self.wait_state(out["run_id"])
        self.assertEqual(row["state"], "quota_exhausted")
        header, _ = self.result_view("--run", out["run_id"])
        self.assertIn("usage limit", header["error"])
        self.assertTrue(header["resets_at"], "from the time the failure names (4:31 AM)")
        self.assertIn("start --engine claude", header["instead"])
        self.assertIn("unverified draft", header["why"])

    def test_in_a_mixed_group_it_returns_the_wait_at_once(self):
        self.env["DELEGATE_TEST_TIMINGS"] = json.dumps({**FAST_TIMINGS, "stall": 60})
        tasks = self.root / "t.jsonl"
        tasks.write_text("".join(json.dumps(l) + "\n" for l in ({"prompt": "c [[fake:sleep:30]]", "engine": "claude", "model": "claude-test-model", "effort": "high"}, {"prompt": "x", "engine": "codex", "model": "fake-big", "effort": "high"})))
        self.cli_json("batch", "--group", "q", "--tasks-file", str(tasks), timeout=120, env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "usage-limit.jsonl"), "FAKE_CODEX_EXIT": "1", "FAKE_CODEX_DELAY": "1.5"})
        began = time.time()
        proc = self.cli("result", "--group", "q", "--wait", timeout=120)
        header = json.loads(proc.stdout.split("\n", 1)[0])
        self.assertLess(time.time() - began, 20, "it did not wait for the claude member")
        self.assertEqual(header["returned_because"], "member_failed")
        self.assertEqual([m["state"] for m in header["members"]], ["running", "quota_exhausted"])
        self.cli_json("stop", "--group", "q", timeout=120)


class ReadingsReported(Sandbox):
    def test_status_and_doctor_say_when_in_the_skills_own_terms(self):
        out = self.codex("start", "x", env={"FAKE_CODEX_FIXTURE": str(CODEX_FIXTURES / "usage-limit.jsonl"), "FAKE_CODEX_EXIT": "1"})
        row = self.wait_state(out["run_id"])
        self.assertEqual(row["state"], "quota_exhausted")
        self.assertRegex(row["resets_at"], r"^\d{4}-\d{2}-\d{2}T", "status says until when, as `status --help` promises")
        q = self.cli_json("doctor", "--engine", "codex", env={"FAKE_CODEX_RATE_LIMITS": exhausted_answer(self.root / "rl.json")}, timeout=120)["engines"]["codex"]["quota"]
        self.assertRegex(q["resets_at"], r"^\d{4}-\d{2}-\d{2}T", "as `models` prints it")


class ClaudeLimit(Sandbox):
    def usage(self, percent, scope=None):
        rows = [json.loads(l) for l in (CLAUDE_FIXTURES / "usage.jsonl").read_text().splitlines()]
        for e in rows:
            report = e.get("usage_report") if isinstance(e, dict) else None
            if report:
                limits = report["rate_limits"]["limits"]
                for l in limits:
                    l["resets_at"] = "2099-01-01T00:00:00+00:00"
                target = next(l for l in limits if (l["scope"] is None) == (scope is None))
                target["percent"] = percent
        path = self.root / "usage.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in rows))
        return {"FAKE_CLAUDE_USAGE_FILE": str(path)}

    def test_a_used_up_account_limit_refuses_every_claude_model(self):
        out = self.cli_json("start", *CLAUDE_RUN, "x", env=self.usage(100), expect=1)
        self.assertEqual((out["error"], out["source"]), ("quota_exhausted", "claude -p /usage"))
        self.assertIn("start --engine codex", out["instead"])

    def test_a_model_scoped_limit_refuses_only_that_model(self):
        env = self.usage(100, scope="Fable")
        out = self.cli_json("start", "--engine", "claude", "--model", "fable", "--effort", "high", "x", env=env, expect=1)
        self.assertEqual(out["error"], "quota_exhausted")
        ok = self.start("x", "--model", "sonnet", env=env)
        self.wait_state(ok["run_id"])

    def test_an_unreadable_limit_refuses_nothing(self):
        ok = self.start("x", env={"FAKE_CLAUDE_USAGE": "fail"})
        self.wait_state(ok["run_id"])

    def test_extra_usage_goes_on_past_a_full_limit(self):
        env = self.usage(100)
        path = env["FAKE_CLAUDE_USAGE_FILE"]
        rows = [json.loads(l) for l in open(path).read().splitlines()]
        for e in rows:
            if isinstance(e, dict) and e.get("usage_report"):
                e["usage_report"]["rate_limits"]["extra_usage"] = {"is_enabled": True}
        open(path, "w").write("".join(json.dumps(e) + "\n" for e in rows))
        ok = self.start("x", env=env)
        self.wait_state(ok["run_id"])

    def test_a_model_scoped_rejection_leaves_other_models_to_usage(self):
        out = self.start("x [[fake:ratelimit:rejected]] [[fake:error]]", env={"FAKE_CLAUDE_RATE_LIMIT_TYPE": "seven_day_opus"})
        self.wait_state(out["run_id"])
        ok = self.start("other model", "--model", "sonnet")
        self.wait_state(ok["run_id"])

    def test_a_run_that_hit_the_limit_is_quota_exhausted_and_the_next_start_is_refused(self):
        out = self.start("x [[fake:ratelimit:rejected]] [[fake:error]]")
        row = self.wait_state(out["run_id"])
        self.assertEqual(row["state"], "quota_exhausted")
        refused = self.cli_json("start", *CLAUDE_RUN, "again", expect=1)
        self.assertEqual((refused["error"], refused["source"]), ("quota_exhausted", "a run's rate_limit_event"), "a fresh event of this registry is read before /usage")
