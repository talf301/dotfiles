import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).with_name("attention_hook.py")


def run(payload, state, *args):
    env = {**os.environ, "ATTENTION_STATE_DIR": str(state)}
    p = subprocess.run([sys.executable, str(HOOK), *args], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout


class AttentionHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.cwd = Path(self.tmp.name) / "work"
        self.cwd.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        return [json.loads(l) for l in (self.state / "events.jsonl").read_text().splitlines()]

    def test_notification_logged_private(self):
        rc, out = run({"hook_event_name": "Notification", "session_id": "s1",
                       "notification_type": "permission_prompt", "message": "needs ok"}, self.state)
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(self.events()[0]["notification_type"], "permission_prompt")
        mode = stat.S_IMODE((self.state / "events.jsonl").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_permission_request_makes_no_decision(self):
        rc, out = run({"hook_event_name": "PermissionRequest", "session_id": "s1",
                       "tool_name": "Bash", "tool_input": {"command": "ls"}}, self.state)
        self.assertEqual((rc, out), (0, ""))
        self.assertNotIn("tool_input", self.events()[0])

    def test_prompt_text_not_stored(self):
        run({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "secret"}, self.state)
        self.assertNotIn("secret", (self.state / "events.jsonl").read_text())

    def test_session_start_injects_expect_path(self):
        rc, out = run({"hook_event_name": "SessionStart", "session_id": "abc-1", "source": "startup"}, self.state)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(self.state / "expect" / "abc-1.json"), ctx)

    def test_stop_without_declaration_allows(self):
        rc, out = run({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(self.cwd)}, self.state)
        self.assertEqual((rc, out), (0, ""))

    def test_stop_blocks_on_missing_or_empty_artifact(self):
        (self.state / "expect").mkdir(parents=True)
        (self.cwd / "empty.md").write_text("")
        (self.cwd / "ok.md").write_text("x")
        (self.state / "expect" / "s1.json").write_text(
            json.dumps({"paths": ["ok.md", "empty.md", "missing.md"]}))
        rc, out = run({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(self.cwd)}, self.state)
        res = json.loads(out)
        self.assertEqual(res["decision"], "block")
        self.assertIn("missing.md", res["reason"])
        self.assertIn("empty.md", res["reason"])
        self.assertNotIn("ok.md,", res["reason"])

    def test_stop_allows_second_attempt(self):
        (self.state / "expect").mkdir(parents=True)
        (self.state / "expect" / "s1.json").write_text(json.dumps({"paths": ["missing.md"]}))
        rc, out = run({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(self.cwd),
                       "stop_hook_active": True}, self.state)
        self.assertEqual(out, "")
        self.assertEqual(self.events()[-1]["missing"], [str(self.cwd / "missing.md")])

    def test_stop_allows_when_complete(self):
        (self.state / "expect").mkdir(parents=True)
        (self.cwd / "ok.md").write_text("x")
        (self.state / "expect" / "s1.json").write_text(json.dumps({"paths": [str(self.cwd / "ok.md")]}))
        rc, out = run({"hook_event_name": "Stop", "session_id": "s1", "cwd": str(self.cwd)}, self.state)
        self.assertEqual(out, "")

    def test_bad_input_never_fails(self):
        env = {**os.environ, "ATTENTION_STATE_DIR": str(self.state)}
        p = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, env=env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_report_counts_waits(self):
        run({"hook_event_name": "Notification", "session_id": "s1", "notification_type": "idle_prompt"}, self.state)
        run({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}, self.state)
        env = {**os.environ, "ATTENTION_STATE_DIR": str(self.state)}
        out = subprocess.run([sys.executable, str(HOOK), "report"], capture_output=True, text=True, env=env).stdout
        self.assertIn("Notification:idle_prompt: 1", out)
        self.assertIn("Waits answered by a prompt: 1", out)


if __name__ == "__main__":
    unittest.main()
