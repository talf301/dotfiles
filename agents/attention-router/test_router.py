import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch
import unittest
from types import SimpleNamespace
from pathlib import Path

from delegate import delegate_claude
from router import decide, open_database


def request(revision=0):
    return {
        "request_id": "r1",
        "revision": revision,
        "session_id": "session-1",
        "task_id": "task-1",
        "objective": "Keep the experiment moving",
        "question": "May the supervisor continue?",
        "options": ["continue", "request_input"],
        "evidence": [{"id": "e1", "text": "worker is idle", "provenance": {"source": "fixture"}}],
        "grants": [],
    }


def proposal(**changes):
    value = {"status": "answer", "action": "continue", "evidence_ids": ["e1"], "provenance": {"source": "fixture"}}
    value.update(changes)
    return value


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_database(Path(self.tmp.name) / "router.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixture_answer_is_durable_and_idempotent(self):
        first = decide(self.db, request(), proposal())
        second = decide(self.db, request(), proposal(action="request_input"))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "answer")
        self.assertTrue(first["supervisor_authored"])

    def test_no_proposal_needs_evidence(self):
        result = decide(self.db, request())
        self.assertEqual(result["status"], "need_evidence")
        self.assertEqual(result["reason"], "supervisor_proposal_required")

    def test_revision_payload_is_immutable(self):
        decide(self.db, request(), proposal())
        changed = request()
        changed["question"] = "different"
        with self.assertRaises(ValueError):
            decide(self.db, changed, proposal())
        decide(self.db, request(1), proposal())
        with self.assertRaises(ValueError):
            decide(self.db, request(0), proposal())

    def test_malformed_proposal_fails_closed_and_keeps_evidence(self):
        result = decide(self.db, request(), {"status": "answer", "action": "shell"})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["reason"], "malformed_proposal")
        self.assertIsNone(result["effect_id"])

    def test_unhashable_proposal_fields_fail_closed(self):
        result = decide(self.db, request(), {"status": [], "evidence_ids": [[], []]})
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["reason"], "malformed_proposal")
        raw = self.db.execute("SELECT proposal FROM requests").fetchone()[0]
        self.assertEqual(json.loads(raw), {"status": [], "evidence_ids": [[], []]})

    def test_duplicate_evidence_ids_are_rejected(self):
        duplicate = request()
        duplicate["evidence"] = [duplicate["evidence"][0], duplicate["evidence"][0]]
        with self.assertRaises(ValueError):
            decide(self.db, duplicate, proposal())

    def test_answer_without_evidence_needs_evidence(self):
        result = decide(self.db, request(), proposal(evidence_ids=[]))
        self.assertEqual(result["status"], "need_evidence")

    def test_stale_revision_cannot_return_cached_answer(self):
        decide(self.db, request(0), proposal())
        decide(self.db, request(1), proposal())
        with self.assertRaises(ValueError):
            decide(self.db, request(0), proposal())

    def test_human_grant_cannot_be_created_by_proposal(self):
        result = decide(self.db, request(), proposal(provenance={"source": "fixture", "grants": [{"actor": "human"}]}))
        self.assertEqual(result["status"], "answer")
        self.assertNotIn("grants", result)

    def test_cli_round_trip(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "db.sqlite"
            completed = subprocess.run(
                [sys.executable, str(root / "cli.py"), "--db", str(db)],
                input=json.dumps(request()),
                text=True,
                capture_output=True,
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "need_evidence")
            self.assertEqual(result["reason"], "supervisor_proposal_required")

    def test_cli_rejects_bad_evidence_types(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "db.sqlite"
            bad = request()
            bad["evidence"][0]["id"] = 3
            completed = subprocess.run(
                [sys.executable, str(root / "cli.py"), "--db", str(db)],
                input=json.dumps(bad), text=True, capture_output=True
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(json.loads(completed.stdout)["status"], "error")

    def test_cli_error_result_has_nonzero_exit(self):
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "db.sqlite"
            completed = subprocess.run(
                [sys.executable, str(root / "cli.py"), "--db", str(db), "--proposal", "missing.json", "--delegate", "claude"],
                input=json.dumps(request()), text=True, capture_output=True
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)["status"], "error")

    def test_restart_reopens_ledger_and_concurrent_identical_request_is_safe(self):
        path = Path(self.tmp.name) / "restart.sqlite"
        first = open_database(path)
        expected = decide(first, request(), proposal())
        first.close()
        reopened = open_database(path)
        self.assertEqual(decide(reopened, request(), proposal(action="request_input")), expected)
        results, errors = [], []

        def worker():
            try:
                connection = open_database(path)
                results.append(decide(connection, request(), proposal(action="request_input")))
                connection.close()
            except Exception as exc:  # pragma: no cover - assertion below reports unexpected races
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(results, [expected] * 4)

    def test_claude_delegate_caches_completed_response_and_metadata(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            model_proposal = proposal(provenance={"source": "claude"})
            return SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": model_proposal, "model": "fixture", "modelUsage": {"inputTokens": 1}, "total_cost_usd": 0}).encode(), stderr=b"")

        first = delegate_claude(self.db, request(), runner=runner)
        second = delegate_claude(self.db, request(), runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retried")))
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        row = self.db.execute("SELECT status, metadata FROM model_calls").fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertIn("total_cost_usd", row["metadata"])
        self.assertIn("modelUsage", row["metadata"])
        self.assertIn("--safe-mode", calls[0][0])
        self.assertIn("--tools", calls[0][0])
        self.assertEqual(calls[0][1]["input"], json.dumps(request(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())

    def test_claude_delegate_malformed_response_is_recoverable_without_retry(self):
        calls = []

        def runner(*args, **kwargs):
            calls.append(1)
            return SimpleNamespace(returncode=0, stdout=b"not json", stderr=b"")

        first = delegate_claude(self.db, request(), runner=runner)
        second = delegate_claude(self.db, request(), runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retried")))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "error")
        self.assertEqual(first["reason"], "malformed_model_response")
        self.assertEqual(calls, [1])

    def test_claude_delegate_timeout_is_unknown_and_not_retried(self):
        calls = []

        def runner(*args, **kwargs):
            calls.append(1)
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output=b"partial")

        first = delegate_claude(self.db, request(), runner=runner, timeout=1)
        second = delegate_claude(self.db, request(), runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retried")))
        self.assertEqual(first["reason"], "claude_timeout")
        self.assertEqual(second["reason"], "claude_timeout")
        self.assertEqual(calls, [1])

    def test_delegate_does_not_call_for_final_or_stale_request(self):
        calls = []
        runner = lambda *args, **kwargs: (calls.append(1) or SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": proposal(provenance={"source": "claude"})}).encode(), stderr=b""))
        newer = request(1)
        self.assertEqual(delegate_claude(self.db, newer, runner=runner)["status"], "answer")
        with self.assertRaises(ValueError):
            delegate_claude(self.db, request(), runner=runner)
        self.assertEqual(calls, [1])

    def test_oversized_model_output_is_bounded_and_not_retried(self):
        output = b"x" * (1_048_576 + 1)
        runner = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output, stderr=b"")
        first = delegate_claude(self.db, request(), runner=runner)
        second = delegate_claude(self.db, request(), runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retried")))
        self.assertEqual(first["reason"], "claude_output_too_large")
        self.assertEqual(second["reason"], "claude_output_too_large")
        raw = self.db.execute("SELECT raw_response FROM model_calls").fetchone()[0]
        self.assertLessEqual(len(raw.encode()), 1_048_576)

    def test_real_child_output_cap_kills_only_that_child(self):
        child = [sys.executable, "-c", "import sys,time; sys.stdout.write('x' * 1100000); sys.stdout.flush(); time.sleep(5)"]
        with patch("delegate.command", return_value=child):
            result = delegate_claude(self.db, request(), timeout=2)
        self.assertEqual(result["reason"], "claude_output_too_large")
        raw = self.db.execute("SELECT raw_response FROM model_calls").fetchone()[0]
        self.assertLessEqual(len(raw.encode()), 1_048_576)

    def test_reservation_payload_exists_before_timeout(self):
        observed = []

        def runner(*args, **kwargs):
            observed.append(self.db.execute("SELECT payload FROM model_calls").fetchone()[0])
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        delegate_claude(self.db, request(), runner=runner, timeout=1)
        self.assertEqual(json.loads(observed[0]), request())

    def test_stale_delegate_replay_never_calls_runner(self):
        calls = []
        runner = lambda *args, **kwargs: (calls.append(1) or SimpleNamespace(returncode=0, stdout=json.dumps({"structured_output": proposal(provenance={"source": "claude"})}).encode(), stderr=b""))
        delegate_claude(self.db, request(), runner=runner)
        delegate_claude(self.db, request(1), runner=runner)
        with self.assertRaises(ValueError):
            delegate_claude(self.db, request(), runner=runner)
        self.assertEqual(calls, [1, 1])

    def test_ledger_and_wal_sidecars_are_private(self):
        path = Path(self.tmp.name) / "private.sqlite"
        connection = open_database(path)
        decide(connection, request(), proposal())
        for sibling in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            if sibling.exists():
                self.assertEqual(stat.S_IMODE(os.stat(sibling).st_mode), 0o600)
        connection.close()

    def test_broadly_readable_parent_is_rejected(self):
        parent = Path(self.tmp.name) / "public"
        parent.mkdir(mode=0o755)
        os.chmod(parent, 0o755)
        with self.assertRaises(ValueError):
            open_database(parent / "router.sqlite")


if __name__ == "__main__":
    unittest.main()
