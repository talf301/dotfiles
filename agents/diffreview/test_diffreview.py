#!/usr/bin/env python3
"""Tests for diffreview. Run: python3 agents/diffreview/test_diffreview.py"""
import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "diffreview"
_loader = SourceFileLoader("diffreview", str(SCRIPT))
dr = importlib.util.module_from_spec(importlib.util.spec_from_loader("diffreview", _loader))
_loader.exec_module(dr)

UUID = "01a1fabd-0000-7000-8000-000000000001"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="drtest-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "rexwork" / "sessions"
        self.state.mkdir(parents=True)
        self._state, dr.STATE = dr.STATE, self.state
        self.addCleanup(setattr, dr, "STATE", self._state)


class SessionDir(Tmp):
    def test_accepts_uuid_with_or_without_prefix(self):
        self.assertEqual(dr.session_dir("session:" + UUID), self.state / UUID)
        self.assertEqual(dr.session_dir(UUID), self.state / UUID)

    def test_rejects_paths(self):
        for bad in ["/etc", "session:/etc", "..", "session:../../x", "../" + UUID, UUID + "/..",
                    "session:" + UUID + "/x", "short", "zzzzzzzz-zzzz"]:
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                dr.session_dir(bad)

    def test_rejects_symlink_out_of_state(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.state / UUID).symlink_to(outside)
        with self.assertRaises(SystemExit):
            dr.session_dir(UUID)

    def test_rejects_symlink_to_sibling_session(self):
        other = "01a1fabd-0000-7000-8000-000000000002"
        (self.state / other).mkdir()
        (self.state / UUID).symlink_to(self.state / other)
        with self.assertRaises(SystemExit):
            dr.session_dir(UUID)

    def test_block_ids(self):
        self.assertEqual(dr.agent_block_id("01a1-ab"), "block:01a1-ab")
        for bad in ["block:../x", "block:a/b", "block:" + "a" * 65]:
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                dr.agent_block_id(bad)


class Marker(Tmp):
    def test_waits_for_rexwork_lock_and_keeps_its_update(self):
        directory = self.state / UUID
        directory.mkdir()
        (directory / "session.json").write_text(json.dumps({"label": "scratch"}))
        lock = open(directory / ".lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        worker = threading.Thread(target=dr.set_marker, args=(directory, "/repo", "abc"))
        worker.start()
        time.sleep(0.3)
        self.assertTrue(worker.is_alive(), "set_marker did not wait for the lock")
        # A concurrent `rexwork promote` lands while the lock is held.
        (directory / "session.json").write_text(json.dumps({"label": "scratch", "ticket": "AI-1"}))
        lock.close()
        worker.join(5)
        data = json.loads((directory / "session.json").read_text())
        self.assertEqual(data, {"label": "scratch", "ticket": "AI-1", "last_reviewed": {"/repo": "abc"}})


class Keys(unittest.TestCase):
    def test_per_block_viewer_and_comments(self):
        d = Path("/state/x")
        a = dr.review_paths(d, "/repo", "session:s", "block:a")
        b = dr.review_paths(d, "/repo", "session:s", "block:b")
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertEqual(a, dr.review_paths(d, "/repo", "session:s", "block:a"))
        self.assertNotEqual(dr.comments_path(d, "/repo", "block:a"), dr.comments_path(d, "/repo", "block:b"))

    def test_one_editor_per_session_one_tab_per_agent(self):
        # Every review of a session is a tab of the session's one docview Neovim.
        self.assertEqual(dr.docview.sock_path(Path("/s/" + UUID)), Path("/s/" + UUID) / "nvim.sock")
        self.assertNotEqual(dr.tab_key("/repo", "block:a"), dr.tab_key("/repo", "block:b"))
        self.assertNotEqual(dr.tab_key("/repo", "block:a"), dr.tab_key("/other", "block:a"))
        self.assertEqual(dr.tab_key("/repo", "block:a"), dr.tab_key("/repo", "block:a"))

    def test_head_moved_note(self):
        self.assertIsNone(dr.head_moved_note("a" * 40, "a" * 40))
        note = dr.head_moved_note("a" * 40, "b" * 40)
        self.assertEqual(note, f"HEAD moved from {'a' * 11} to {'b' * 11} since this diff was opened; "
                               f"marked {'a' * 11}; reopen to review the rest.")


class LuaData(unittest.TestCase):
    """Values reach Lua as data, whatever they contain."""

    def test_awkward_strings_round_trip(self):
        if not shutil.which("nvim"):
            self.skipTest("no nvim")
        tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="drlua-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        sock = tmp / "s.sock"
        proc = subprocess.Popen(["nvim", "--headless", "-u", "NONE", "--listen", str(sock)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        for _ in range(50):
            if dr.alive(sock):
                break
            time.sleep(0.1)
        value = "/tmp/a ]] b ]=] c' \" \\ é\n%#*"
        self.assertEqual(dr.lua(sock, "_A.v", arg={"v": value}), value)
        self.assertEqual(dr.lua(sock, "_A", arg="x]]y"), "x]]y")
        self.assertIsNone(dr.lua(sock, "error('boom')"))


class MarkCommand(Tmp):
    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo ]] x"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "A")

    def head(self):
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()

    def mark(self, *args):
        env = dict(os.environ, XDG_STATE_HOME=str(self.tmp), REX_SESSION="session:" + UUID, REX_BLOCK="block:t1")
        out = subprocess.run([str(SCRIPT), "mark", "--json", *args], cwd=self.repo, env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_opened_head_is_marked_when_head_moved(self):
        a = self.head()
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "B")
        b = self.head()
        data = self.mark("--opened-head", a)
        self.assertEqual(data["marker"], a)
        self.assertTrue(data["head_moved"])
        self.assertTrue(data["message"].startswith(
            f"HEAD moved from {a[:11]} to {b[:11]} since this diff was opened; marked {a[:11]}; "
            "reopen to review the rest."), data["message"])
        # From the shell, no REV still marks HEAD.
        data = self.mark()
        self.assertEqual(data["marker"], b)
        self.assertFalse(data["head_moved"])

    def test_comments_archived_per_block(self):
        directory = self.state / UUID
        top = str(self.repo.resolve())
        mine, other = dr.comments_path(directory, top, "block:t1"), dr.comments_path(directory, top, "block:t2")
        mine.parent.mkdir(parents=True)
        mine.write_text("mine\n")
        other.write_text("other\n")
        data = self.mark()
        self.assertFalse(mine.exists())
        self.assertEqual(Path(data["archived_comments"]).read_text(), "mine\n")
        self.assertEqual(other.read_text(), "other\n")


class OpenHoldsTheSessionLock(MarkCommand):
    """diffreview open starts the server, places the viewer and picks its tab under docview's
    session lock, as `docview open` does, so concurrent opens cannot interleave."""

    test_opened_head_is_marked_when_head_moved = test_comments_archived_per_block = None  # run once, above

    def test_open_under_lock(self):
        import contextlib
        events, held = [], []
        dv = dr.docview

        @contextlib.contextmanager
        def lock(session, timeout=None):
            held.append(session)
            events.append("lock")
            try:
                yield
            finally:
                held.pop()
                events.append("unlock")

        def step(name, result):
            def f(*a, **k):
                events.append((name, bool(held)))
                return result
            return f
        saved = {k: getattr(dv, k) for k in ("session_lock", "find_window", "ensure_server", "show_viewer", "call")}
        self.addCleanup(lambda: [setattr(dv, k, v) for k, v in saved.items()])
        dv.session_lock = lock
        dv.find_window = lambda s, b: "window:w"
        dv.ensure_server = step("ensure", (Path("/s/nvim.sock"), True))
        dv.show_viewer = step("show", ({"window_id": "window:w"}, "attached"))
        dv.call = step("open_diff", True)
        args = dr.argparse.Namespace(session="session:" + UUID, agent_block="block:t1", base=None,
                                     bounds="0,0,1,1", no_focus=True, json=True)
        old = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, old)
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            dr.cmd_open(args)
        self.assertEqual(events, ["lock", ("ensure", True), ("show", True), ("open_diff", True), "unlock"])

    def test_base_names_the_tab(self):
        env = dict(os.environ, XDG_STATE_HOME=str(self.tmp))
        out = subprocess.run([str(SCRIPT), "base", "--json", "--session", "session:" + UUID, "--agent-block", "block:t2"],
                             cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["tab_key"], dr.tab_key(str(self.repo.resolve()), "block:t2"))
        self.assertTrue(data["comments"].endswith(".t2.diff-comments.md"))


if __name__ == "__main__":
    unittest.main()
