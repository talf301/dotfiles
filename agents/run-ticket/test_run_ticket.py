import unittest

from run_ticket import STALL_SECONDS, derive

ACTIVE = {"type": "active", "activeFlags": []}
IDLE = {"type": "idle"}


class DeriveTest(unittest.TestCase):
    def test_running(self):
        self.assertEqual(derive("active", ACTIVE, "", False, 0), "running")

    def test_approval_needs_you_even_if_active(self):
        s = {"type": "active", "activeFlags": ["waitingOnApproval"]}
        self.assertEqual(derive("active", s, "", False, 0), "needs-you")

    def test_complete(self):
        self.assertEqual(derive("complete", IDLE, "", False, 0), "done")

    def test_limited(self):
        self.assertEqual(derive("budgetLimited", IDLE, "", False, 0), "limited")
        self.assertEqual(derive("usageLimited", IDLE, "", False, 0), "limited")

    def test_blocked_with_live_run_is_waiting(self):
        self.assertEqual(derive("blocked", IDLE, "WAITING: abc", True, 10 ** 6), "waiting-run")

    def test_blocked_question_needs_you(self):
        self.assertEqual(derive("blocked", IDLE, "NEEDS INPUT: which split?", False, 0), "needs-you")

    def test_blocked_without_marker_needs_you(self):
        self.assertEqual(derive("blocked", {"type": "notLoaded"}, "Blocked: need colour", False, 0), "needs-you")

    def test_waiting_after_run_ended_gets_grace_then_stalls(self):
        self.assertEqual(derive("blocked", IDLE, "WAITING: abc", False, 60), "waiting-run")
        self.assertEqual(derive("blocked", IDLE, "WAITING: abc", False, STALL_SECONDS + 1), "stalled")

    def test_active_goal_idle_thread_stalls_after_grace(self):
        self.assertEqual(derive("active", IDLE, "", False, 60), "running")
        self.assertEqual(derive("active", IDLE, "", False, STALL_SECONDS + 1), "stalled")

    def test_system_error(self):
        self.assertEqual(derive("active", {"type": "systemError"}, "", False, 0), "error")


if __name__ == "__main__":
    unittest.main()
