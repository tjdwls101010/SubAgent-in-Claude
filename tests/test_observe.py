"""Watching runs: attention thresholds in process with an injected clock (J10), and through the CLI — `result --wait` coming back early, partial and schema results, `status` and `log`."""

from __future__ import annotations

import json
import time
import unittest

from support.harness import CLAUDE_RUN, CLAUDE_FIXTURES as FIXTURES, FAST_TIMINGS, Sandbox, engine

observe = engine("delegate.observe")
claude = engine("delegate.engine").adapter("claude")


def view(meta, name="basic_write.jsonl", upto=None):
    events = [json.loads(l) for l in (FIXTURES / name).read_text().splitlines()][:upto]
    return claude.view_of(events, meta)


class AttentionThresholds(unittest.TestCase):
    """Production thresholds: 600 s without an event, 120 s of retries, 120 s finishing."""

    def reasons(self, meta, at, since=None, event_at=1000.0, name="basic_write.jsonl", upto=6):
        meta = {"engine": "claude", **meta}
        return [a["reason"] for a in observe.attention(meta, view(meta, name, upto), event_at, at, since=since)]

    def test_stall(self):
        self.assertEqual(self.reasons({"state": "running"}, 1599.0), [])
        self.assertEqual(self.reasons({"state": "running"}, 1600.0), ["stalled"])
        self.assertEqual(self.reasons({"state": "starting"}, 1600.0), ["stalled"])
        self.assertEqual(self.reasons({"state": "finishing", "finishing_since": 1590.0}, 1600.0), ["stalled"])
        self.assertEqual(self.reasons({"state": "running"}, 1600.0, since=1500.0), [], "a wait that began 100 s ago has not seen a full stall")
        self.assertEqual(self.reasons({"state": "completed"}, 9999.0), [])

    def test_retrying(self):
        meta = {"state": "running", "retrying_since": 1000.0}
        self.assertEqual(self.reasons(meta, 1119.0, event_at=1110.0), [])
        self.assertEqual(self.reasons(meta, 1120.0, event_at=1110.0), ["retrying"])

    def test_rate_limit_rejected(self):
        self.assertEqual(self.reasons({"state": "running", "rate_limit": {"status": "rejected"}}, 1001.0), ["rate_limited"])
        self.assertEqual(self.reasons({"state": "running", "rate_limit": {"status": "allowed_warning"}}, 1001.0), [])

    def test_finishing(self):
        meta = {"state": "finishing", "finishing_since": 1000.0}
        self.assertEqual(self.reasons(meta, 1119.0, event_at=1110.0), [])
        self.assertEqual(self.reasons(meta, 1120.0, event_at=1110.0), ["finishing"])


class Wait(Sandbox):
    def result(self, run_id, *args, expect=0):
        proc = self.cli("result", "--run", run_id, *args, timeout=60)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        header, _, text = proc.stdout.partition("\n")
        return json.loads(header), text

    def test_stall_ends_the_wait_and_a_second_wait_needs_something_new(self):
        reply = self.start("x [[fake:silent:30]]")
        began = time.time()
        header, _ = self.result(reply["run_id"], "--wait")
        self.assertGreaterEqual(time.time() - began, 2.5)
        self.assertEqual([a["reason"] for a in header["attention"]], ["stalled"])
        self.assertIn(header["state"], ("running", "starting"))
        self.assertEqual(header["next"]["command"].split(" result ", 1)[1], f"--run {reply['run_id']} --wait")
        self.assertIn("live", header["note"])
        header, _ = self.result(reply["run_id"], "--wait", "--wait-timeout", "1")
        self.assertIn("--wait-timeout passed", header["note"])
        self.assertIn("--wait-timeout 1", header["next"]["command"])
        self.assertEqual(self.status(reply["run_id"])["state"], "stalled")
        self.cli_json("stop", "--run", reply["run_id"])

    def test_retrying(self):
        reply = self.start("x [[fake:retry_hang:30]]")
        self.wait_for(lambda: self.status(reply["run_id"])["state"] == "retrying", what="retrying state")
        header, _ = self.result(reply["run_id"], "--wait")
        self.assertEqual([a["reason"] for a in header["attention"]], ["retrying"])
        self.assertEqual(header["attention"][0]["retry"]["error"], "overloaded")
        self.assertIsNotNone(self.status(reply["run_id"])["retrying_since"])
        self.cli_json("stop", "--run", reply["run_id"])

    def test_rate_limit_rejected(self):
        reply = self.start("x [[fake:silent:1.5]] [[fake:ratelimit_late:rejected]] [[fake:sleep:30]]")
        header, _ = self.result(reply["run_id"], "--wait")
        self.assertEqual([a["reason"] for a in header["attention"]], ["rate_limited"])
        self.assertEqual(self.cli_json("status")["rate_limit"]["status"], "rejected")
        self.cli_json("stop", "--run", reply["run_id"])

    def test_attention_present_when_the_wait_begins_does_not_end_it(self):
        reply = self.start("x [[fake:ratelimit:rejected]] [[fake:silent:30]]")
        self.wait_for(lambda: self.status(reply["run_id"])["attention"], what="rate limit attention")
        self.assertEqual([a["reason"] for a in self.status(reply["run_id"])["attention"]], ["rate_limited"])
        header, _ = self.result(reply["run_id"], "--wait")
        self.assertEqual([a["reason"] for a in header["attention"]], ["stalled"])
        self.cli_json("stop", "--run", reply["run_id"])

    def test_wait_timeout_needs_wait(self):
        reply = self.start("x")
        bad = self.cli_json("result", "--run", reply["run_id"], "--wait-timeout", "5", expect=2)
        self.assertEqual(bad["help"], "result --help")
        self.cli_json("result", "--run", reply["run_id"], "--wait", "--wait-timeout", "0", expect=2)


class Schema(Sandbox):
    def test_structured_output_as_one_document(self):
        schema = self.root / "s.json"
        schema.write_text(json.dumps({"type": "object", "properties": {"a": {"type": "integer"}}}))
        reply = self.start('x [[fake:schema:{"a": 1}]]', "--schema", str(schema))
        proc = self.cli("result", "--run", reply["run_id"], "--wait")
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["json"], {"a": 1})
        self.assertEqual(doc["state"], "completed")
        for field in ("usage", "answered", "files_changed", "engine", "native_id"):
            self.assertIn(field, doc, "the document carries the whole header")
        self.assertTrue(proc.stdout.startswith("{\n"))
        argv = [c for c in self.fake_calls() if "-p" in c["argv"]][0]["argv"]
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), json.loads(schema.read_text()))

    def test_ended_without_structured_output_is_read_not_refused(self):
        """The run was read, so `result` exits 0: `json` is null and `message` holds the final text."""
        schema = self.root / "s.json"
        schema.write_text("{}")
        reply = self.start("x [[fake:reply:plain text]]", "--schema", str(schema))
        self.wait_state(reply["run_id"])
        doc = json.loads(self.cli("result", "--run", reply["run_id"]).stdout)
        self.assertIsNone(doc["json"])
        self.assertIn("without structured output", doc["note"])
        self.assertEqual(doc["message"], "plain text")

    def test_bad_schema_file(self):
        bad = self.root / "bad.json"
        bad.write_text("[1, 2]")
        self.assertEqual(self.cli_json("start", *CLAUDE_RUN, "x", "--schema", str(bad), expect=2)["help"], "start --help")
        self.cli_json("start", *CLAUDE_RUN, "x", "--schema", str(self.root / "none.json"), expect=2)


class StatusAndLog(Sandbox):
    def test_partial_result_while_live(self):
        reply = self.start("x [[fake:sleep:30]]")
        header, _ = json.loads(self.cli("result", "--run", reply["run_id"]).stdout.splitlines()[0]), None
        self.assertIn("live", header["note"])
        self.assertIn("next", header)
        # The fake's tool call follows its init by a moment, as a real model's does; read the row once it is there.
        row = self.wait_for(lambda: (r := self.status(reply["run_id"]))["in_progress"] and r, what="the tool call in progress")
        self.assertEqual([p["tool"] for p in row["in_progress"]], ["Bash"])
        self.assertTrue(row["stdin_open"])
        self.cli_json("stop", "--run", reply["run_id"])

    def test_listing_keeps_live_runs_and_truncates_the_rest(self):
        live = self.start("x [[fake:sleep:60]]")
        done = [self.start(f"r{i}") for i in range(21)]
        for r in done:
            self.wait_state(r["run_id"])
        listing = self.cli_json("status")
        self.assertEqual(listing["live"], [live["run_id"]])
        self.assertEqual(len(listing["runs"]), 21)
        self.assertEqual(listing["runs_truncated"], 1)
        self.assertEqual(listing["runs"][0]["run_id"], live["run_id"])
        self.cli_json("stop", "--run", live["run_id"])

    def test_status_by_session_and_prefix(self):
        reply = self.start("x")
        self.wait_state(reply["run_id"])
        self.assertEqual(self.status(reply["native_id"])["run_id"], reply["run_id"])
        self.assertEqual(self.status(reply["native_id"][:8])["run_id"], reply["run_id"])
        self.cli_json("status", "--run", "zzzz", expect=1)

    def test_log_through_the_cli(self):
        reply = self.start("x [[fake:write:a.txt]] [[fake:bash_fail]] [[fake:reply:done]]", "--model", "sonnet")
        self.wait_state(reply["run_id"])
        out = self.cli("log", "--run", reply["run_id"]).stdout.splitlines()
        self.assertEqual(out[0], f"init model=claude-sonnet-5-5 mode=acceptEdits cwd={self.project}")
        self.assertIn("write a.txt", out)
        i = out.index("bash exit=1 out=26B make test")
        self.assertEqual(out[i + 1 : i + 3], ["    FAIL: test_x", "    assert 1 == 2"])
        self.assertIn("msg done", out)
        self.assertEqual(out[-1], f"run={reply['run_id']} state=completed")


class WaitEnded(Sandbox):
    def test_a_continuing_stall_is_reported_once_and_the_end_is_returned(self):
        import os
        from unittest import mock

        reply = self.start("x [[fake:silent:10]]")
        calls = []
        with mock.patch.dict(os.environ, {"DELEGATE_TEST_TIMINGS": json.dumps(FAST_TIMINGS)}):
            snap = observe.wait_ended(cwd=str(self.project), ref=reply["run_id"], on_attention=lambda items: calls.append([a["reason"] for a in items]))
        self.assertEqual(snap["meta"]["state"], "completed")
        self.assertEqual(calls, [["stalled"]], "one stall that lasts several stall intervals is one attention")
