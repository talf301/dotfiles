#!/usr/bin/env python3
"""Tests for docview's shared per-session Neovim. Rex is faked; Neovim is real
(--clean plus ~/dotfiles/nvim on the runtimepath for config.review).

Run: python3 agents/docview/test_docview.py
"""
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import fcntl
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid as uuidlib
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
NVIM_CONFIG = HERE.parent.parent / "nvim"
_loader = SourceFileLoader("docview", str(HERE / "docview"))
dv = importlib.util.module_from_spec(importlib.util.spec_from_loader("docview", _loader))
_loader.exec_module(dv)

UUID = "01a1fabd-0000-7000-8000-0000000000d1"
SID = "session:" + UUID
W1, W2 = "window:w1", "window:w2"
A, B, C = "block:aaaa", "block:bbbb", "block:cccc"


class FakeRex:
    """Just enough of Rex: a session with two tabs (windows), agent panes and float layers."""

    def __init__(self):
        self.windows = {W1: [{"layer_id": "layer:t1", "kind": "tiled", "blocks": [{"block_id": A}, {"block_id": B}]}],
                        W2: [{"layer_id": "layer:t2", "kind": "tiled", "blocks": [{"block_id": C}]}]}
        self.calls = []
        self.mutex = threading.Lock()

    def view(self):
        return {"session_id": SID, "windows": [{"window_id": w, "layers": ls} for w, ls in self.windows.items()]}

    def floats(self):
        return [(w, l) for w, ls in self.windows.items() for l in ls if l["kind"] == "floating"]

    def __call__(self, args):
        with self.mutex:
            return self.handle(args)

    def handle(self, args):
        self.calls.append(args)
        if args[:2] == ["session", "inspect"]:
            return 0, json.dumps(self.view())
        if args[:2] == ["api", "call"]:
            req = json.loads(args[3])
            if args[2] == "session.new_layer":
                n = len(self.calls)
                layer = {"layer_id": f"layer:f{n}", "kind": "floating", "blocks": [{"block_id": f"block:f{n}"}]}
                self.windows[req["window_id"]].append(layer)
                return 0, json.dumps({"layer_id": layer["layer_id"], "block_ids": [f"block:f{n}"], "revision": 1})
            if args[2] == "session.set_layer_state":
                for _, layer in self.floats():
                    if layer["layer_id"] == req["layer_id"]:
                        layer.pop("state", None)
                        if req["state"] == "minimized":
                            layer["state"] = "minimized"
                        return 0, '{"revision": 2}'
                return 1, ""
        if args[:2] == ["block", "close"]:
            for w, layer in self.floats():
                if layer["blocks"][0]["block_id"] == args[4]:
                    self.windows[w].remove(layer)
            return 0, ""
        return 1, "unknown rex call"


class Shared(unittest.TestCase):
    def setUp(self):
        if not shutil.which("nvim"):
            self.skipTest("no nvim")
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="dvt-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fake = FakeRex()
        real_run = dv.run

        def run(args, timeout=20):
            if args[0] == "rex":
                code, out = self.fake(list(args[1:]))
                return SimpleNamespace(returncode=code, stdout=out, stderr="" if code == 0 else out)
            return real_run(args, timeout=timeout)
        saved = {k: getattr(dv, k) for k in ("run", "SESSIONS", "LEGACY", "NVIM_ARGS", "ATTACH_WAIT", "open_tab",
                                             "show_viewer", "ensure_server", "LOCK_TIMEOUT", "CALL_TIMEOUT")}
        dv.run = run
        dv.SESSIONS = self.tmp / "rexwork" / "sessions"
        dv.LEGACY = self.tmp / "docview"
        dv.NVIM_ARGS = ["--clean", "--cmd", "set rtp^=" + str(NVIM_CONFIG).replace(" ", "\\ ")]
        dv.ATTACH_WAIT = 0
        self.addCleanup(lambda: [setattr(dv, k, v) for k, v in saved.items()])
        self.addCleanup(self.kill_server)
        self.sock = dv.sock_path(dv.SESSIONS / UUID)
        self.docs = {}
        for name in ("plan", "notes", "other"):
            p = self.tmp / f"{name}.md"
            p.write_text(f"# {name}\n\nbody of {name}\n")
            self.docs[name] = p.resolve()

    def kill_server(self):
        if dv.alive(self.sock):
            dv.lua(self.sock, "(function() vim.schedule(function() vim.cmd('qall!') end) return 1 end)()")
            for _ in range(30):
                if not dv.alive(self.sock):
                    break
                time.sleep(0.1)

    def cli(self, *argv):
        """Run a docview command in-process; returns (exit code, JSON output or None)."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                import sys
                old = sys.argv
                sys.argv = ["docview", *argv]
                dv.main()
            except SystemExit as e:
                code = e.code or 0
            finally:
                sys.argv = old
        self.last_err = err.getvalue()
        return code, json.loads(out.getvalue()) if out.getvalue().strip() else None

    def open(self, name, block, *extra):
        code, data = self.cli("open", str(self.docs[name]), "--session", SID, "--agent-block", block, *extra)
        self.assertEqual(code, 0, self.last_err)
        return data

    def state(self):
        return dv.server_state(self.sock)

    def labeled(self):
        return [t for t in self.state()["tabs"] if t.get("label")]

    def test_two_docs_two_tabs_one_server_one_viewer(self):
        first = self.open("plan", A, "--title", "Plan")
        second = self.open("notes", B)
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertEqual(first["sock"], second["sock"])
        self.assertEqual(second["viewer_action"], "reused")
        tabs = self.labeled()
        self.assertEqual([t["label"] for t in tabs], ["Doc: Plan", "Doc: notes"])
        self.assertEqual([t["block"] for t in tabs], [A, B])
        self.assertEqual({t["session"] for t in tabs}, {SID})
        self.assertEqual(tabs[0]["comments"], str(self.docs["plan"].with_name("plan.comments.md")))
        self.assertEqual(len(self.state()["tabs"]), 2, "the empty startup tab was not dropped")
        self.assertEqual(len(self.fake.floats()), 1)
        # Opening an open document again switches to its tab; the newest caller gets its comments.
        self.open("plan", C)
        tabs = self.labeled()
        self.assertEqual(len(tabs), 2)
        self.assertTrue(tabs[0]["current"])
        self.assertEqual(tabs[0]["block"], C)
        tabline = dv.lua(self.sock, "require('docview_server').tabline()")
        self.assertIn("Doc: Plan", tabline)
        self.assertIn("Doc: notes", tabline)
        code, rows = self.cli("ls")
        mine = [r for r in rows if r.get("session") == SID]
        self.assertEqual(sorted(r["label"] for r in mine), ["Doc: Plan", "Doc: notes"])
        self.assertEqual(mine[0]["viewer"]["state"], "normal")

    def test_comments_go_to_each_tabs_own_agent(self):
        self.open("plan", A)
        self.open("notes", B)
        code = """(function()
          local review = require('config.review')
          local sent = {}
          vim.system = function(args, opts, cb)
            if args[8] == 'process' then
              cb({code = 0, stdout = vim.json.encode({foreground = {argv0 = 'claude', name = 'claude'}})})
            else
              sent[#sent + 1] = {args[5], args[7], vim.json.decode(opts.stdin).data}
              cb({code = 0})
            end
            return {}
          end
          vim.env.REX_REVIEW_BLOCK = 'block:stale'
          vim.env.REX_SESSION = 'session:stale'
          for i, tab in ipairs(vim.api.nvim_list_tabpages()) do
            vim.api.nvim_set_current_tabpage(tab)
            vim.fn.writefile({'comment from tab ' .. i}, vim.t.review_comments_file)
            review.send()
            vim.wait(200, function() return #sent >= i end)
          end
          return sent
        end)()"""
        sent = dv.lua(self.sock, code)
        self.assertEqual([(s[0], s[1]) for s in sent], [(SID, A), (SID, B)])
        self.assertIn("comment from tab 1", sent[0][2])
        self.assertIn("comment from tab 2", sent[1][2])

    def test_open_from_another_rex_tab_moves_the_viewer(self):
        first = self.open("plan", A)
        self.assertEqual(first["viewer"]["window_id"], W1)
        moved = self.open("notes", C)
        self.assertEqual(moved["viewer_action"], "moved")
        self.assertEqual(moved["viewer"]["window_id"], W2)
        floats = self.fake.floats()
        self.assertEqual(len(floats), 1)
        self.assertEqual(floats[0][0], W2)
        self.assertEqual(len(self.labeled()), 2)

    def test_toggle(self):
        self.open("plan", A)
        layer = self.fake.floats()[0][1]
        code, out = self.cli("toggle", "--session", SID, "--block", A)
        self.assertEqual((code, out["action"]), (0, "minimized"))
        self.assertEqual(layer.get("state"), "minimized")
        code, out = self.cli("toggle", "--session", SID, "--window", W1)
        self.assertEqual(out["action"], "restored")
        self.assertNotIn("state", layer)
        restore = [c for c in self.fake.calls if c[:3] == ["api", "call", "session.set_layer_state"]][-1]
        self.assertTrue(json.loads(restore[3])["focus"])
        # Open while minimized restores it.
        self.cli("toggle", "--session", SID, "--window", W1)
        again = self.open("notes", B)
        self.assertEqual(again["viewer_action"], "restored")
        self.assertNotIn("state", layer)
        # From another Rex tab: moves there.
        code, out = self.cli("toggle", "--session", SID, "--block", C)
        self.assertEqual(out["action"], "moved")
        self.assertEqual(self.fake.floats()[0][0], W2)
        # Viewer pane gone: toggle attaches a new one.
        self.fake(["block", "close", "-s", SID, self.fake.floats()[0][1]["blocks"][0]["block_id"]])
        code, out = self.cli("toggle", "--session", SID, "--block", A)
        self.assertEqual(out["action"], "attached")
        self.assertEqual(len(self.fake.floats()), 1)

    def test_toggle_with_nothing_open(self):
        code, _ = self.cli("toggle", "--session", SID, "--block", A)
        self.assertEqual(code, 1)
        self.assertIn("nothing is open", self.last_err)

    def test_close_and_quit_with_unsaved_edits(self):
        self.open("plan", A)
        self.open("notes", B)
        plan = str(self.docs["plan"])
        dv.lua(self.sock, "(function() for _, b in ipairs(vim.api.nvim_list_bufs()) do"
                          " if vim.api.nvim_buf_get_name(b) == _A then"
                          " vim.api.nvim_buf_set_lines(b, 0, 0, false, {'unsaved'}) end end return 1 end)()", arg=plan)
        code, out = self.cli("close", plan, "--quit")
        self.assertEqual(code, 2)
        self.assertEqual(out["modified"], [plan])
        self.assertEqual(len(self.labeled()), 2, "--quit closed a tab with unsaved edits")
        code, out = self.cli("close", plan)
        self.assertEqual(code, 0)
        self.assertTrue(out["viewer_closed"])
        self.assertEqual([t["label"] for t in self.labeled()], ["Doc: notes"])
        self.assertEqual(self.state()["modified"], [plan], "closing the tab lost the edits")
        code, rows = self.cli("ls")
        self.assertIn(("hidden", plan), [(r["kind"], r["path"]) for r in rows])
        # Closing the last tab closes the viewer; the edits keep Neovim running.
        code, out = self.cli("close", str(self.docs["notes"]), "--quit")
        self.assertEqual(code, 0)
        self.assertTrue(out["sessions"][0]["viewer_closed"])
        self.assertFalse(out["sessions"][0]["server_quit"])
        self.assertEqual(self.fake.floats(), [])
        self.assertTrue(dv.alive(self.sock))
        # Reopening finds the unsaved edits.
        self.open("plan", A)
        lines = dv.lua(self.sock, "vim.api.nvim_buf_get_lines(0, 0, 1, false)")
        self.assertEqual(lines, ["unsaved"])

    def test_closing_everything_quits_a_clean_server(self):
        self.open("plan", A)
        code, out = self.cli("close", str(self.docs["plan"]), "--quit")
        self.assertEqual(code, 0)
        self.assertTrue(out["sessions"][0]["server_quit"])
        for _ in range(30):
            if not dv.alive(self.sock):
                break
            time.sleep(0.1)
        self.assertFalse(dv.alive(self.sock))

    def test_last_window_quit_guard(self):
        self.open("plan", A)
        self.open("notes", B)
        self.open("other", C)

        def typed(keys):
            dv.lua(self.sock, "vim.api.nvim_input(_A)", arg=keys)
            time.sleep(0.4)

        typed(":q<CR>")
        self.assertTrue(dv.alive(self.sock), ":q killed the shared Neovim")
        self.assertEqual([t["label"] for t in self.labeled()], ["Doc: plan", "Doc: notes"])
        typed(":qa<CR>")
        self.assertTrue(dv.alive(self.sock), ":qa killed the shared Neovim")
        self.assertEqual(len(self.labeled()), 2)
        # A split closes as usual.
        typed(":split<CR>")
        typed(":q<CR>")
        self.assertEqual(len(self.labeled()), 2)
        # Unsaved edits: the tab closes, the buffer stays.
        typed("ichanged<Esc>")
        typed(":q<CR>")
        self.assertTrue(dv.alive(self.sock))
        self.assertEqual(len(self.labeled()), 1)
        self.assertEqual(len(self.state()["modified"]), 1)
        # The last tab: Neovim stays for the unsaved edits.
        typed(":q<CR>")
        self.assertTrue(dv.alive(self.sock))
        self.assertEqual(len(self.labeled()), 0)
        # ...and quits by itself once nothing is unsaved and nothing is open.
        dv.lua(self.sock, "(function() for _, b in ipairs(vim.api.nvim_list_bufs()) do"
                          " vim.bo[b].modified = false end return 1 end)()")
        self.open("plan", A)
        typed(":q<CR>")
        for _ in range(30):
            if not dv.alive(self.sock):
                break
            time.sleep(0.1)
        self.assertFalse(dv.alive(self.sock))

    def test_unanswered_prompt_times_out_with_a_message(self):
        self.open("plan", A)
        saved = dv.CALL_TIMEOUT
        dv.CALL_TIMEOUT = 1
        self.addCleanup(setattr, dv, "CALL_TIMEOUT", saved)
        # Something that holds Neovim busy longer than the timeout.
        busy = subprocess.Popen([dv.NVIM, "--server", str(self.sock), "--remote-expr", "luaeval('vim.uv.sleep(4000)')"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(busy.wait)
        time.sleep(1)
        code, _ = self.cli("toggle", "--session", SID, "--block", C)
        self.assertEqual(code, 1)
        self.assertIn("did not answer", self.last_err)
        self.assertEqual(self.fake.floats()[0][0], W1, "moved the viewer although Neovim was stuck")
        time.sleep(3)

    def test_diff_tabs_per_repo_and_agent(self):
        self.open("plan", A)
        # Stand-ins for diffview: DiffviewOpen opens a tab, DiffviewClose closes it.
        dv.lua(self.sock, """(function()
          vim.api.nvim_create_user_command('DiffviewOpen', function(c)
            vim.cmd('tabnew')
            vim.api.nvim_buf_set_name(0, 'diffview://' .. vim.fn.getcwd() .. '/' .. c.args)
            vim.t.fake_diff = true
          end, {nargs = '*'})
          vim.api.nvim_create_user_command('DiffviewClose', function()
            if vim.t.fake_diff then vim.cmd('tabclose') end
          end, {})
          return 1 end)()""")
        repo = self.tmp / "repo dir"
        repo.mkdir()
        repo = repo.resolve()

        def diff(block, base):
            return dv.call(self.sock, "require('docview_server').open_diff(_A)", arg={
                "key": f"repo.{block}", "top": str(repo), "base": base, "head": "h" + base, "dirty": True,
                "comments": f"/c/{block}.md", "block": block, "session": SID, "label": "Review: repo"})

        self.assertIs(diff(A, "b1"), True)
        self.assertIs(diff(A, "b2"), True)  # the same agent's review is replaced, not added
        self.assertIs(diff(B, "b3"), True)
        tabs = self.labeled()
        self.assertEqual([t.get("kind") for t in tabs], ["doc", "diff", "diff"])
        self.assertEqual([t.get("block") for t in tabs], [A, A, B])
        self.assertEqual(tabs[1]["comments"], f"/c/{A}.md")
        here = dv.lua(self.sock, "{vim.fn.getcwd(), vim.t.diffreview_head, vim.t.diffreview_dirty}")
        self.assertEqual(here, [str(repo), "hb3", True])
        # The document's tab keeps its own directory.
        self.assertEqual(dv.lua(self.sock, "vim.fn.getcwd(-1, 1)"), str(self.docs["plan"].parent))
        res = dv.close_in_server(SID, self.sock, "diffreview_key", f"repo.{A}", False)
        self.assertTrue(res["closed"])
        self.assertEqual([t.get("block") for t in self.labeled()], [A, B])

    def pid(self):
        return dv.lua(self.sock, "vim.fn.getpid()")

    def test_busy_server_is_never_replaced(self):
        self.open("plan", A)
        pid = self.pid()
        dv.CALL_TIMEOUT = 1
        busy = subprocess.Popen([dv.NVIM, "--server", str(self.sock), "--remote-expr", "luaeval('vim.uv.sleep(5000)')"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(busy.wait)
        time.sleep(1)
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            dv.ensure_server(SID)
        self.assertIn("is busy", err.getvalue())
        code, _ = self.cli("open", str(self.docs["notes"]), "--session", SID, "--agent-block", B)
        self.assertEqual(code, 1)
        self.assertTrue(self.sock.exists(), "the busy Neovim's socket was removed")
        busy.wait()
        self.assertEqual(self.pid(), pid, "a second Neovim replaced the busy one")
        self.assertEqual([t["label"] for t in self.labeled()], ["Doc: plan"])

    def test_stale_socket_is_replaced(self):
        directory = dv.session_dir(SID)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(dv.sock_path(directory)))
        stale.close()  # the file stays; nothing listens
        self.assertFalse(dv.listening(self.sock))
        sock, started = dv.ensure_server(SID)
        self.assertTrue(started)
        self.assertTrue(dv.alive(sock))

    def test_concurrent_opens_serialize(self):
        seen, starts = [], []
        real_open, real_show, real_ensure = dv.open_tab, dv.show_viewer, dv.ensure_server

        def slow_open(sock, path, *a, **k):
            res = real_open(sock, path, *a, **k)
            time.sleep(0.4)  # widen the window between choosing a tab and placing the viewer
            return res

        def show(session, window, sock, **k):
            seen.append((window, dv.lua(sock, "vim.t.docview_path")))
            return real_show(session, window, sock, **k)

        def ensure(session, cwd=None):
            sock, started = real_ensure(session, cwd)
            starts.append(started)
            return sock, started
        dv.open_tab, dv.show_viewer, dv.ensure_server = slow_open, show, ensure
        argv = {A: ("plan", W1), C: ("notes", W2)}
        errors = []

        def one(block):
            args = dv.argparse.Namespace(file=str(self.docs[argv[block][0]]), session=SID, agent_block=block,
                                         title=None, tab_label=None, kind="doc", comments=None,
                                         bounds="0.06,0.06,0.88,0.88", no_focus=True)
            try:
                dv.cmd_open(args)
            except SystemExit as e:
                errors.append(e)
        out, sys.stdout = sys.stdout, io.StringIO()
        try:
            threads = [threading.Thread(target=one, args=(b,)) for b in (A, C)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sys.stdout = out
        self.assertEqual(errors, [])
        self.assertEqual(sorted(starts), [False, True], "two Neovims were started")
        expect = {W1: str(self.docs["plan"]), W2: str(self.docs["notes"])}
        for window, shown in seen:
            self.assertEqual(shown, expect[window], "the viewer was placed while another caller's tab was shown")
        floats = self.fake.floats()
        self.assertEqual(len(floats), 1)
        last_window = seen[-1][0]
        self.assertEqual(floats[0][0], last_window)
        current = [t for t in self.labeled() if t["current"]][0]
        self.assertEqual(current["path"], expect[last_window])

    def test_lock_timeout_says_so(self):
        self.open("plan", A)
        dv.LOCK_TIMEOUT = 0.5
        with open(dv.session_dir(SID) / "docview.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            code, _ = self.cli("toggle", "--session", SID, "--block", A)
        self.assertEqual(code, 1)
        self.assertIn("held the editor", self.last_err)

    def test_server_environment_has_no_agent_pane(self):
        import os
        os.environ["REX_BLOCK"], os.environ["REX_REVIEW_BLOCK"] = "block:caller", "block:caller"
        self.addCleanup(os.environ.pop, "REX_BLOCK", None)
        self.addCleanup(os.environ.pop, "REX_REVIEW_BLOCK", None)
        self.open("plan", A)
        env = dv.lua(self.sock, "{vim.env.REX_BLOCK or false, vim.env.REX_REVIEW_BLOCK or false, vim.env.REX_SESSION}")
        self.assertEqual(env, [False, False, SID])

    def test_space_r_d_runs_diffreview_open_without_deadlock(self):
        """Space r d in the shared editor runs `diffreview open`, which calls back into the same
        Neovim over its socket; the job is asynchronous, so the call-back is answered."""
        import os
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        log = self.tmp / "diffreview.log"
        fake = bin_dir / "diffreview"
        fake.write_text(f"""#!{sys.executable}
import json, os, subprocess, sys
out = subprocess.run([{dv.NVIM!r}, "--server", {str(self.sock)!r}, "--remote-expr",
                      "json_encode(luaeval('#vim.api.nvim_list_tabpages()'))"],
                     capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
with open({str(log)!r}, "w") as f:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "tabs": out.stdout.strip(),
               "rex_block": os.environ.get("REX_BLOCK")}}, f)
print(json.dumps({{"message": "opened"}}))
""")
        fake.chmod(0o755)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bin_dir}:{old_path}"
        self.addCleanup(os.environ.__setitem__, "PATH", old_path)
        self.open("plan", A)
        self.assertEqual(dv.lua(self.sock, "(function() require('config.review').open_since_review() return 1 end)()"), 1)
        for _ in range(100):
            if log.exists() and log.read_text():
                break
            time.sleep(0.1)
        got = json.loads(log.read_text())
        self.assertEqual(got["argv"], ["open", "--json", "--session", SID, "--agent-block", A])
        self.assertEqual(Path(got["cwd"]).resolve(), self.docs["plan"].parent)
        self.assertEqual(got["tabs"], "1", "the call-back into Neovim was not answered")
        self.assertIsNone(got["rex_block"])

    def test_long_state_dir_uses_short_socket(self):
        deep = Path("/tmp") / ("x" * 90) / UUID
        sock = dv.sock_path(deep)
        self.assertLessEqual(len(str(sock)), 104)
        self.assertEqual(sock.name, UUID + ".sock")

    def test_session_ids_are_checked(self):
        for bad in ["/etc", "session:../x", "..", "short"]:
            with self.subTest(bad=bad), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                dv.session_dir(bad)

    def test_legacy_documents_listed_and_retired(self):
        path = self.tmp / "old.md"
        path.write_text("old\n")
        directory = dv.doc_dir(path.resolve())
        sock = self.tmp / "legacy.sock"
        proc = subprocess.Popen([dv.NVIM, "--clean", "--headless", "--listen", str(sock), str(path)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        for _ in range(50):
            if dv.alive(sock):
                break
            time.sleep(0.1)
        dv.write_meta(directory, {"path": str(path.resolve()), "sock": str(sock), "title": "old",
                                  "viewer": None, "opened_at": 0})
        code, rows = self.cli("ls")
        self.assertIn((str(path.resolve()), True), [(r["path"], r["legacy"]) for r in rows])
        code, out = self.cli("close", str(path), "--quit")
        self.assertEqual(code, 0)
        self.assertTrue(out["quit"])
        proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
