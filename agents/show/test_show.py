"""Unit tests for show with a fake `rex`. Run: python3 -m unittest test_show.py"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("show_cli", str(HERE / "show"))
_spec = importlib.util.spec_from_loader("show_cli", _loader)
show = importlib.util.module_from_spec(_spec)
_loader.exec_module(show)
_vloader = importlib.machinery.SourceFileLoader("show_viewer", str(HERE / "viewer"))
_vspec = importlib.util.spec_from_loader("show_viewer", _vloader)
viewer = importlib.util.module_from_spec(_vspec)
_vloader.exec_module(viewer)


def write_png(path, w=3, h=2):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big"))
    return path


class FitTest(unittest.TestCase):
    def check(self, cols, rows, cw, ch, iw, ih):
        c, r, left, top = viewer.fit(cols, rows, cw, ch, iw, ih)
        self.assertTrue(1 <= c <= cols and 1 <= r <= rows, (c, r))
        self.assertTrue(left + c <= cols and top + r <= rows, (left, top))
        # Centred.
        self.assertLessEqual(abs(left - (cols - c - left)), 1)
        self.assertLessEqual(abs(top - (rows - r - top)), 1)
        box_w, box_h = c * cw, r * ch
        if iw <= cols * cw and ih <= rows * ch:
            # Native size, rounded to whole cells.
            self.assertLessEqual(abs(box_w - iw), max(cw / 2, cw - iw) + 1e-6)
            self.assertLessEqual(abs(box_h - ih), max(ch / 2, ch - ih) + 1e-6)
        else:
            # One side fills the pane; the other is the aspect-correct length to
            # within half a cell (or clamped to 1 cell / the pane).
            self.assertTrue(c == cols or r == rows, (c, r))
            if c == cols and r < rows:
                self.assertLessEqual(abs(box_h - box_w * ih / iw), max(ch / 2, ch - box_w * ih / iw) + 1e-6)
            elif r == rows and c < cols:
                self.assertLessEqual(abs(box_w - box_h * iw / ih), max(cw / 2, cw - box_h * iw / ih) + 1e-6)
        return c, r, left, top

    def test_reported_case(self):
        # Pane 75x55 with a footer row, cells 13x28; the 1992x1970 PNG and a 1200x760 picture.
        self.assertEqual(self.check(75, 54, 13, 28, 1992, 1970), (75, 34, 0, 10))
        self.assertEqual(self.check(75, 54, 13, 28, 2400, 1520), (75, 22, 0, 16))

    def test_retina_pane(self):
        self.check(49, 35, 21, 42, 1992, 1970)
        self.check(49, 35, 21, 42, 2048, 1297)

    def test_tall_image_is_height_limited(self):
        c, r, _, _ = self.check(75, 54, 13, 28, 400, 4000)
        self.assertEqual(r, 54)
        self.assertLess(c, 75)

    def test_small_image_native(self):
        self.assertEqual(self.check(75, 54, 13, 28, 260, 140), (20, 5, 27, 24))

    def test_many_shapes(self):
        for cols, rows in [(1, 1), (10, 3), (75, 54), (200, 60), (38, 35)]:
            for iw, ih in [(1, 1), (1992, 1970), (2400, 1520), (300, 5000), (5000, 300), (13, 28)]:
                self.check(cols, rows, 13, 28, iw, ih)

    def test_escape_always_has_both_dimensions(self):
        move, place = viewer.placement(75, 55, 13, 28, 1992, 1970)
        self.assertEqual(place, ",c=75,r=34")
        self.assertEqual(move, "\033[11;1H")
        move, place = viewer.placement(75, 55, 13, 28, 2400, 1520)
        self.assertEqual(place, ",c=75,r=22")
        _, place = viewer.placement(75, 55, 13, 28, 260, 140)
        self.assertEqual(place, ",c=20,r=5")


class SvgSizeTest(unittest.TestCase):
    def test_size(self):
        self.assertEqual(show.svg_size('<svg xmlns="x" width="1200" height="760">'), (1200, 760))
        self.assertEqual(show.svg_size('<svg width="120px" height="76px">'), (120, 76))
        self.assertEqual(show.svg_size('<svg viewBox="0 0 300 100" width="100%">'), (300, 100))
        self.assertEqual(show.svg_size('<svg viewBox="0,0,300,100" width="600">'), (600, 200))
        self.assertEqual(show.svg_size('<svg width="72pt" height="36pt">'), (96, 48))
        self.assertIsNone(show.svg_size('<svg width="100%">'))
        self.assertIsNone(show.svg_size('<html>'))

    def test_square_wrap_keeps_aspect(self):
        src = ('<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x">\n'
               '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" x="5">'
               '<rect width="1200" height="760"/></svg>')
        text, w, h, side = show.square_svg(src)
        self.assertEqual((w, h, side), (1200, 760, 1200))
        self.assertTrue(text.startswith('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1200" '
                                        'viewBox="0 0 1200 1200">'))
        self.assertIn('<svg x="0" y="0" width="1200" height="760" xmlns="http://www.w3.org/2000/svg">', text)
        self.assertNotIn("<?xml", text)
        self.assertNotIn("DOCTYPE", text)
        self.assertNotIn('x="5"', text)
        tall = show.square_svg('<svg viewBox="0 0 400 1000"><g/></svg>')
        self.assertEqual(tall[1:], (400, 1000, 1000))

    def test_no_wrap_when_unknown(self):
        self.assertIsNone(show.square_svg('<svg width="100%"><g/></svg>'))
        self.assertIsNone(show.square_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg width="1" height="1"/>'))


class PerThreadStdout:
    """sys.stdout replacement that keeps each thread's output apart."""

    def __init__(self):
        self.bufs = {}

    def write(self, s):
        self.bufs.setdefault(threading.get_ident(), []).append(s)

    def flush(self):
        pass

    def text(self):
        return "".join(self.bufs.get(threading.get_ident(), []))


class RexFake(unittest.TestCase):
    """Show with a fake Rex. Tab window:w holds agents block:a and block:b and an
    unrelated pane block:u; tab window:v holds agent block:c. A split adds a tiled
    block to the caller's tab that runs the viewer in the split's -c directory."""
    SID = "session:01a1fabd-f4aa-745d-aedb-be1ddbc3da73"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (show.STATE, show.REXCHAT_STATE, show.rex, show.run, show.time.sleep,
                      os.environ.pop("REXCHAT_INBOX", None), os.environ.pop("REX_BLOCK", None))
        show.STATE = Path(self.tmp.name) / "sessions"
        show.REXCHAT_STATE = Path(tempfile.mkdtemp(prefix="rc", dir="/tmp"))  # short: socket paths
        self.calls = []
        self.viewers = {}   # block -> the directory its viewer runs on (its cwd)
        self.failures = {}  # block -> number of process queries that fail before one succeeds
        self.n = 0
        self.mutex = threading.Lock()
        self.view = {"active_window_id": "window:w", "windows": [
            {"window_id": "window:w", "layers": [{"layer_id": "layer:t", "kind": "tiled", "blocks": [
                {"block_id": "block:a", "label": "agent"}, {"block_id": "block:b", "label": "agent"},
                {"block_id": "block:u", "label": "shell"}]}]},
            {"window_id": "window:v", "layers": [{"layer_id": "layer:v", "kind": "tiled", "blocks": [
                {"block_id": "block:c", "label": "agent"}]}]}]}

        def fake_rex(*args):
            with self.mutex:
                self.calls.append(args)
                if args[:2] == ("session", "inspect"):
                    return json.loads(json.dumps(self.view))
                if args[0] == "split":
                    self.n += 1
                    new = f"block:s{self.n}"
                    self.window(args[args.index("-b") + 1])["layers"][0]["blocks"].append(
                        {"block_id": new, "label": show.LABEL})
                    self.viewers[new] = args[args.index("-c") + 1]
                    return {"block_ids": [new]}
                if args[:2] == ("block", "close"):
                    self.remove(args[-1])
                if args[:3] == ("api", "call", "session.new_layer"):
                    return {"layer_id": "layer:f", "block_ids": ["block:f"]}
                return {}

        def fake_run(args, timeout=60):
            with self.mutex:
                self.calls.append(tuple(args[1:]))
                out = ""
                if args[1:3] == ["block", "call"] and args[-1] == "process":
                    b = args[args.index("-b") + 1]
                    if self.failures.get(b):
                        self.failures[b] -= 1
                        return show.subprocess.CompletedProcess(args, 124, "", "rex: timed out")
                    fg = {"script": str(show.VIEWER), "cwd": self.viewers[b]} if b in self.viewers \
                        else {"script": None, "cwd": "/tmp", "name": "zsh"}
                    out = json.dumps({"foreground": fg})
                elif args[1:3] == ["block", "close"]:
                    self.remove(args[-1])
                return show.subprocess.CompletedProcess(args, 0, out, "")
        show.rex, show.run = fake_rex, fake_run
        show.time.sleep = lambda s: None
        self.d = show.show_dir(self.SID)
        self.png = write_png(Path(self.tmp.name) / "p.png")

    def tearDown(self):
        (show.STATE, rc, show.rex, show.run, show.time.sleep, inbox, block) = self.saved
        import shutil
        shutil.rmtree(show.REXCHAT_STATE, ignore_errors=True)
        show.REXCHAT_STATE = rc
        for k, v in (("REXCHAT_INBOX", inbox), ("REX_BLOCK", block)):
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()

    # -- fake Rex helpers

    def window(self, block):
        return next(w for w in self.view["windows"] if block in show.window_blocks(w))

    def add_block(self, block, label=None, window=0):
        self.view["windows"][window]["layers"][0]["blocks"].append({"block_id": block, "label": label or show.LABEL})

    def remove(self, block):
        for w in self.view["windows"]:
            for layer in w["layers"]:
                layer["blocks"] = [b for b in layer["blocks"] if b["block_id"] != block]

    def blocks(self):
        return show.all_blocks(self.view)

    def closes(self):
        return [c for c in self.calls if c[:2] == ("block", "close")]

    def splits(self):
        return [c for c in self.calls if c[:1] == ("split",)]

    def home(self, block):
        return show.owner_dir(self.d, block)

    def state(self):
        return json.loads((self.d / "state.json").read_text())

    # -- commands

    def args(self, block, **kw):
        base = {"session": self.SID, "block": block, "file": None, "title": None, "index": None}
        base.update(kw)
        return type("A", (), base)()

    def out(self, fn, block, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(self.args(block, **kw))
        return json.loads(buf.getvalue())

    def show(self, block, png=None):
        return self.out(show.cmd_show, block, file=str(png or self.png))

    def toggle(self, block):
        return self.out(show.cmd_toggle, block)

    def enlarge(self, block, index=None):
        return self.out(show.cmd_enlarge, block, index=index)

    def main(self, *argv):
        saved = sys.argv
        sys.argv = ["show", *argv]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                show.main()
        finally:
            sys.argv = saved
        return json.loads(buf.getvalue())


class PanesTest(RexFake):
    def test_two_callers_get_separate_panes_and_histories(self):
        a1 = self.show("block:a")
        b1 = self.show("block:b")
        a2 = self.show("block:a")
        self.assertEqual((a1["pane"], a1["index"], a1["created_pane"]), ("block:s1", 1, True))
        self.assertEqual((b1["pane"], b1["index"], b1["created_pane"]), ("block:s2", 1, True))
        self.assertEqual((a2["pane"], a2["index"], a2["created_pane"]), ("block:s1", 2, False))
        self.assertEqual([s[s.index("-b") + 1] for s in self.splits()], ["block:a", "block:b"])
        for s in self.splits():
            self.assertIn("--focus=false", s)
            self.assertEqual(s[s.index("--label") + 1], "Show")
        self.assertEqual(self.state()["owners"], {"block:a": "block:s1", "block:b": "block:s2"})
        self.assertEqual(len(show.history_items(self.home("block:a"))), 2)
        self.assertEqual(len(show.history_items(self.home("block:b"))), 1)
        self.assertEqual(self.viewers["block:s1"], str(self.home("block:a")))
        self.assertEqual(self.viewers["block:s2"], str(self.home("block:b")))
        self.assertTrue(a2["image"].startswith(str(self.home("block:a") / "images" / "0002-")))
        self.assertFalse((self.d / "history.json").exists())  # no shared history any more

    def test_open_pane_is_per_caller(self):
        r = self.out(show.cmd_open_pane, "block:a")
        self.assertEqual(r, {"pane": "block:s1", "window": "window:w", "created_pane": True})
        self.assertEqual(show.history_items(self.home("block:a")), [])
        r = self.out(show.cmd_open_pane, "block:a")
        self.assertEqual(r["created_pane"], False)
        self.assertEqual(self.out(show.cmd_open_pane, "block:b")["pane"], "block:s2")

    def test_toggle_from_agent_pane(self):
        self.show("block:a")
        self.show("block:b")
        self.calls.clear()
        r = self.toggle("block:a")
        self.assertEqual(r, {"action": "closed", "pane": "block:s1", "window": "window:w",
                             "owner": "block:a", "resolved": "caller"})
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s1")])
        self.assertNotIn("block:a", self.state()["owners"])
        self.assertIn("block:s2", self.blocks())
        r = self.toggle("block:a")
        self.assertEqual(r, {"action": "opened", "pane": "block:s3", "window": "window:w",
                             "owner": "block:a", "resolved": "caller"})

    def test_toggle_from_show_pane_closes_it_and_focuses_owner(self):
        self.show("block:a")
        self.show("block:b")
        self.calls.clear()
        r = self.toggle("block:s2")
        self.assertEqual(r, {"action": "closed", "pane": "block:s2", "window": "window:w",
                             "owner": "block:b", "resolved": "show pane", "focused": "block:b"})
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s2")])
        self.assertIn(("focus", "-s", self.SID, "block:b"), self.calls)
        self.assertIn("block:s1", self.blocks())

    def test_toggle_from_unrelated_pane_uses_latest_on_tab(self):
        self.show("block:a")
        self.show("block:b")
        os.utime(self.home("block:a") / "history.json", (1, 1))
        r = self.toggle("block:u")
        self.assertEqual((r["action"], r["pane"], r["owner"], r["resolved"]),
                         ("closed", "block:s2", "block:b", "latest on tab"))
        self.assertNotIn("focused", r)
        r = self.toggle("block:u")  # b's history is still the latest: reopen beside b
        self.assertEqual((r["action"], r["owner"], r["resolved"]), ("opened", "block:b", "latest on tab"))
        self.assertEqual(self.splits()[-1][self.splits()[-1].index("-b") + 1], "block:b")

    def test_toggle_ignores_other_tabs_histories(self):
        self.show("block:c")  # tab window:v
        r = self.toggle("block:u")
        self.assertEqual((r["action"], r["owner"], r["resolved"]), ("opened", "block:u", "caller"))
        self.assertIn("block:s1", self.blocks())  # c's pane untouched

    def test_enlarge_indexes_are_per_pane(self):
        self.show("block:a", write_png(Path(self.tmp.name) / "a1.png"))
        self.show("block:a", write_png(Path(self.tmp.name) / "a2.png"))
        self.show("block:b", write_png(Path(self.tmp.name) / "b1.png"))
        r = self.enlarge("block:a")
        self.assertEqual((r["index"], r["owner"], r["resolved"]), (2, "block:a", "caller"))
        self.assertIn("0002-a2", r["image"])
        r = self.enlarge("block:s1", index=1)  # from a's Show pane (its viewer's Enter key)
        self.assertEqual((r["index"], r["owner"], r["resolved"]), (1, "block:a", "show pane"))
        self.assertIn("0001-a1", r["image"])
        r = self.enlarge("block:b")
        self.assertIn("0001-b1", r["image"])
        with self.assertRaises(SystemExit):
            self.enlarge("block:b", index=2)  # b has one picture
        os.utime(self.home("block:b") / "history.json", (1, 1))
        r = self.enlarge("block:u")
        self.assertEqual((r["owner"], r["resolved"]), ("block:a", "latest on tab"))

    def test_labelled_pane_without_state_is_not_ours(self):
        self.add_block("block:x")
        self.viewers["block:x"] = str(self.home("block:a"))  # even running a viewer: not recorded
        r = self.toggle("block:a")
        self.assertEqual((r["action"], r["pane"]), ("opened", "block:s1"))
        self.assertEqual(self.closes(), [])

    def test_recorded_pane_running_something_else_is_not_ours(self):
        self.add_block("block:x")
        self.d.mkdir(parents=True)
        show.write_json(self.d / "state.json", {"owners": {"block:a": "block:x"}})
        r = self.toggle("block:a")
        self.assertEqual((r["action"], r["pane"]), ("opened", "block:s1"))
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.state()["owners"]["block:a"], "block:s1")

    def test_other_owners_viewer_is_not_ours(self):
        # A recorded pane running the viewer on another pane's history does not count.
        self.add_block("block:x")
        self.viewers["block:x"] = str(self.home("block:b"))
        self.d.mkdir(parents=True)
        show.write_json(self.d / "state.json", {"owners": {"block:a": "block:x"}})
        self.assertEqual(self.show("block:a")["pane"], "block:s1")

    def recorded(self):
        self.add_block("block:x")
        self.viewers["block:x"] = str(self.home("block:a"))
        (self.home("block:a") / "images").mkdir(parents=True)
        show.write_json(self.d / "state.json", {"owners": {"block:a": "block:x"}})

    def test_one_failed_query_is_retried(self):
        self.recorded()
        self.failures["block:x"] = 1
        self.assertEqual(self.toggle("block:a")["action"], "closed")

    def test_unknown_toggle_changes_nothing(self):
        self.recorded()
        self.failures["block:x"] = 2
        with self.assertRaises(SystemExit) as e:
            self.toggle("block:a")
        self.assertEqual(e.exception.code, show.RETRYABLE)
        self.assertEqual((self.closes(), self.splits()), ([], []))
        self.assertEqual(self.state()["owners"], {"block:a": "block:x"})

    def test_unknown_open_pane_changes_nothing(self):
        self.recorded()
        self.failures["block:x"] = 2
        with self.assertRaises(SystemExit) as e:
            show.cmd_open_pane(self.args("block:a"))
        self.assertEqual(e.exception.code, show.RETRYABLE)
        self.assertEqual(self.splits(), [])
        self.assertEqual(self.state()["owners"], {"block:a": "block:x"})

    def test_unknown_show_keeps_picture(self):
        self.recorded()
        self.failures["block:x"] = 2
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as e:
            show.cmd_show(self.args("block:a", file=str(self.png)))
        self.assertEqual(e.exception.code, show.RETRYABLE)
        items = show.history_items(self.home("block:a"))
        self.assertEqual(len(items), 1)
        self.assertTrue(Path(items[0]["png"]).is_file())
        out = json.loads(buf.getvalue())
        self.assertIsNone(out["pane"])
        self.assertIn("error", out)
        self.assertEqual(self.splits(), [])
        self.assertEqual(self.state()["owners"], {"block:a": "block:x"})

    def test_bad_block_id_refused(self):
        for bad in ["block:../x", "block:a/b", "block:", "block:.."]:
            with self.assertRaises(SystemExit):
                show.caller(self.args(bad))

    def test_query_answers(self):
        d = self.d

        def answer(stdout, code=0):
            show.run = lambda args, timeout=60: show.subprocess.CompletedProcess(args, code, stdout, "")
            return show.query_viewer(self.SID, "block:s", d)
        self.assertIsNone(answer("", 1))
        self.assertIsNone(answer("not json"))
        self.assertFalse(answer(json.dumps({"foreground": None})))
        self.assertFalse(answer(json.dumps({"foreground": {"script": "/x/viewer", "cwd": str(d)}})))
        self.assertTrue(answer(json.dumps({"foreground": {"script": str(show.VIEWER), "cwd": str(d)}})))


class OrphanTest(RexFake):
    def test_orphaned_show_pane_is_closed_others_left(self):
        self.show("block:a")
        self.show("block:b")
        self.show("block:c")
        self.add_block("block:h")  # hand-made pane labelled Show: never touched
        self.remove("block:a")     # a's agent pane closed; its Show pane s1 lingers
        self.calls.clear()
        r = self.show("block:b")
        self.assertEqual(r["closed_orphans"], ["block:s1"])
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s1")])
        self.assertEqual(self.state()["owners"], {"block:b": "block:s2", "block:c": "block:s3"})
        self.assertTrue({"block:s2", "block:s3", "block:h"} <= self.blocks())
        self.assertTrue(show.history_items(self.home("block:a")))  # history kept
        self.assertNotIn("closed_orphans", self.show("block:b"))

    def test_orphan_record_running_something_else_is_forgotten_not_closed(self):
        self.show("block:a")
        self.viewers.pop("block:s1")  # s1 now runs a shell
        self.remove("block:a")
        r = self.show("block:b")
        self.assertNotIn("closed_orphans", r)
        self.assertEqual(self.closes(), [])
        self.assertNotIn("block:a", self.state()["owners"])

    def test_unverifiable_orphan_is_kept(self):
        self.show("block:a")
        self.remove("block:a")
        self.failures["block:s1"] = 2
        self.show("block:b")
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.state()["owners"]["block:a"], "block:s1")

    def test_toggle_also_cleans_up(self):
        self.show("block:a")
        self.remove("block:a")
        self.toggle("block:b")
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s1")])


class LegacyTest(RexFake):
    def setUp(self):
        super().setUp()
        images = self.d / "images"
        images.mkdir(parents=True)
        items = []
        for i in (1, 2):
            p = write_png(images / f"000{i}-old.png")
            items.append({"png": str(p), "title": f"old {i}", "source": "/x", "width": 3, "height": 2, "time": i})
        show.write_json(self.d / "history.json", {"items": items})
        # Old state.json: per-tab Show pane, a single-record enlarge, no owners.
        show.write_json(self.d / "state.json", {"panes": {"window:w": "block:L"}, "session": self.SID,
                                                "enlarge": {"window": "window:w", "layer": "layer:old",
                                                            "block": "block:old"}})
        show.write_json(self.d.parent / "session.json", {"main_block": "block:a"})
        self.add_block("block:L")
        self.viewers["block:L"] = str(self.d)
        self.before = {p: p.read_bytes() for p in self.d.rglob("*") if p.is_file() and p.name != "lock"}

    def test_old_state_shapes_load(self):
        for old in [{}, [], {"panes": {"window:w": "block:L"}}, {"panes": None, "owners": "x"},
                    {"panes": {"window:w": 5}, "owners": {"block:a": 3}}]:
            show.write_json(self.d / "state.json", old)
            state = show.load_state(self.d / "state.json")
            self.assertIsInstance(state["owners"], dict)
            self.assertIsInstance(state["panes"], dict)

    def test_legacy_pictures_reachable_by_enlarge(self):
        r = self.enlarge("block:u")
        self.assertEqual((r["index"], r["owner"], r["resolved"]), (2, "legacy", "legacy history"))
        self.assertTrue(r["image"].endswith("0002-old.png"))
        r = self.enlarge("block:L", index=1)  # the old Show pane's own Enter key
        self.assertEqual((r["index"], r["owner"], r["resolved"]), (1, "legacy", "legacy show pane"))

    def test_toggle_from_old_show_pane_closes_it(self):
        r = self.toggle("block:L")
        self.assertEqual(r, {"action": "closed", "pane": "block:L", "window": "window:w", "owner": "legacy",
                             "resolved": "legacy show pane", "focused": "block:a"})
        self.assertNotIn("window:w", self.state()["panes"])

    def test_toggle_reaches_legacy_history_when_tab_has_none(self):
        self.toggle("block:L")
        r = self.toggle("block:u")
        self.assertEqual((r["action"], r["owner"], r["resolved"]), ("opened", "legacy", "legacy history"))
        self.assertEqual(self.viewers[r["pane"]], str(self.d))

    def test_new_pictures_go_per_pane_and_legacy_is_untouched(self):
        r = self.show("block:a")
        self.assertEqual((r["index"], r["pane"]), (1, "block:s1"))
        self.assertIn("block:L", self.blocks())  # the old pane is not closed
        after = {p: p.read_bytes() for p in self.before}
        self.assertEqual(after.keys(), self.before.keys())
        for p in self.before:
            if p.name != "state.json":
                self.assertEqual(after[p], self.before[p], p)
        state = self.state()
        self.assertEqual(state["panes"], {"window:w": "block:L"})
        self.assertEqual(show.enlarge_records(state), {"window:w": {"window": "window:w", "layer": "layer:old",
                                                                    "block": "block:old"}})
        self.assertEqual(self.enlarge("block:a")["owner"], "block:a")  # now a's own history


class ConcurrencyTest(RexFake):
    def test_concurrent_calls(self):
        stdout = PerThreadStdout()
        results, errors = [], []

        def worker(block):
            try:
                for _ in range(3):
                    show.cmd_show(self.args(block, file=str(self.png)))
                results.extend(json.loads(line) for line in stdout.text().splitlines())
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(b,)) for b in ["block:a", "block:b"] * 4]
        saved = sys.stdout
        sys.stdout = stdout
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sys.stdout = saved
        self.assertEqual(errors, [])
        self.assertEqual(len(self.splits()), 2)
        for block in ("block:a", "block:b"):
            mine = [r for r in results if r["pane"] == self.state()["owners"][block]]
            self.assertEqual(sorted(r["index"] for r in mine), list(range(1, 13)))
            items = show.history_items(self.home(block))
            self.assertEqual(len(items), 12)
            self.assertEqual(len({i["png"] for i in items}), 12)
        self.assertEqual(sum(r["created_pane"] for r in results), 2)


class RexchatTest(RexFake):
    """Delivery inline under a live rexchat, exactly as rexchat's shim/show did."""
    INLINE_PANE = {"inline": True, "note": "pictures are drawn inline in this chat; there is no Show pane"}

    def setUp(self):
        super().setUp()
        saved = show.REXCHAT_TMP
        show.REXCHAT_TMP = Path(self.tmp.name) / "rexchat"   # stands for <tmp>/rexchat

        def restore():
            show.REXCHAT_TMP = saved
        self.addCleanup(restore)

    def serve(self, name, base=None, make=True):
        """A fake live rexchat for pane NAME: its control socket answers one line per connection.
        Returns its inbox under BASE (default <tmp>/rexchat), made as the launcher makes it."""
        inbox = (base or show.REXCHAT_TMP) / name / f"inbox-{os.getpid()}"
        if make:
            show.make_private_dirs(inbox)
        path = show.REXCHAT_STATE / f"{name}.sock"
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(str(path))
        srv.listen()
        self.addCleanup(srv.close)
        self.requests = []

        def loop():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                with c:
                    self.requests.append(c.makefile().readline())
                    c.sendall(b'{"error": "nothing to send"}\n')
        threading.Thread(target=loop, daemon=True).start()
        return inbox

    def refused(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as e:
            self.main(*argv)
        self.assertEqual(e.exception.code, 1)
        self.assertEqual(self.calls, [])
        return err.getvalue()

    def check_inline(self, out, inbox, title="p.png"):
        self.assertEqual(set(out), {"image", "inline", "note"})
        self.assertEqual((out["inline"], out["note"]), (True, "drawn inline in the chat"))
        png = Path(out["image"])
        self.assertEqual(png.parent, inbox)
        self.assertRegex(png.name, r"^[0-9]+\.png$")
        self.assertEqual(show.png_size(png), (3, 2))
        self.assertEqual(json.loads(png.with_suffix(".json").read_text()),
                         {"title": title, "source": str(self.png.resolve())})
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), sorted([png.name, png.stem + ".json"]))
        self.assertEqual((self.splits(), self.calls), ([], []))  # no Rex at all

    def test_by_rexchat_inbox(self):
        inbox = Path(self.tmp.name) / "inbox"
        os.environ["REXCHAT_INBOX"] = str(inbox)
        self.check_inline(self.main(str(self.png)), inbox)
        for flag in ("--toggle", "--enlarge", "--open-pane"):
            self.assertEqual(self.main(flag), self.INLINE_PANE)

    def test_unwritable_inbox_fails_plainly(self):
        # As under Codex's sandbox: no Show pane instead, a plain error the agent can act on.
        locked = Path(self.tmp.name) / "locked"
        locked.mkdir(mode=0o500)
        os.environ["REXCHAT_INBOX"] = str(locked / "inbox")
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as e:
                self.main(str(self.png), "--session", self.SID, "--block", "block:a")
        finally:
            locked.chmod(0o700)
        self.assertEqual(e.exception.code, 1)
        self.assertIn("cannot write to rexchat's picture inbox", err.getvalue())
        self.assertEqual(self.calls, [])

    def test_title(self):
        inbox = Path(self.tmp.name) / "inbox"
        os.environ["REXCHAT_INBOX"] = str(inbox)
        out = self.main(str(self.png), "--title", "Flow")
        self.assertEqual(json.loads(Path(out["image"]).with_suffix(".json").read_text())["title"], "Flow")

    def test_by_rex_block_with_live_instance(self):
        inbox = self.serve("rcb1")
        self.check_inline(self.main(str(self.png), "--session", self.SID, "--block", "block:rcb1"), inbox)
        self.assertEqual(self.requests, ["{}\n"])  # the harmless probe, nothing else
        os.environ["REX_BLOCK"] = "block:rcb1"
        self.assertEqual(self.main("--toggle", "--session", self.SID), self.INLINE_PANE)
        self.assertEqual(self.main("--enlarge", "--session", self.SID, "--block", "rcb1"), self.INLINE_PANE)

    def test_inbox_in_old_location_still_found(self):
        inbox = self.serve("rcb2", base=show.REXCHAT_STATE / "pictures")
        self.check_inline(self.main(str(self.png), "--session", self.SID, "--block", "block:rcb2"), inbox)

    def test_new_dirs_are_private(self):
        inbox = Path(self.tmp.name) / "fresh" / "pane" / "inbox-9"
        os.environ["REXCHAT_INBOX"] = str(inbox)
        self.main(str(self.png))
        for d in (inbox, inbox.parent, inbox.parent.parent):
            self.assertEqual(d.stat().st_mode & 0o777, 0o700, d)

    def test_planted_temp_dir_symlink_refused(self):
        other = Path(self.tmp.name) / "elsewhere"
        planted = other / "rcb3" / f"inbox-{os.getpid()}"
        show.make_private_dirs(planted)
        show.REXCHAT_TMP.symlink_to(other)          # <tmp>/rexchat is a symlink
        self.serve("rcb3", make=False)
        msg = self.refused(str(self.png), "--session", self.SID, "--block", "block:rcb3")
        self.assertIn("inbox is missing or not safe", msg)
        self.assertEqual(list(planted.iterdir()), [])
        # Through REXCHAT_INBOX too: the path is under <tmp>/rexchat, a symlink.
        os.environ["REXCHAT_INBOX"] = str(show.REXCHAT_TMP / "rcb3" / f"inbox-{os.getpid()}")
        self.assertIn("refusing rexchat's picture inbox", self.refused(str(self.png)))
        self.assertEqual(list(planted.iterdir()), [])

    def test_temp_dir_of_another_user_refused(self):
        inbox = self.serve("rcb4")
        uid = os.getuid() + 1
        with unittest.mock.patch.object(show.os, "getuid", return_value=uid):  # as if another user owned it
            self.refused(str(self.png), "--session", self.SID, "--block", "block:rcb4")
        self.assertEqual(list(inbox.iterdir()), [])

    def test_others_writable_pane_dir_refused(self):
        inbox = self.serve("rcb5")
        inbox.parent.chmod(0o777)
        self.refused(str(self.png), "--session", self.SID, "--block", "block:rcb5")
        self.assertEqual(list(inbox.iterdir()), [])

    def test_live_rexchat_without_inbox_still_answers_inline_for_pane_commands(self):
        self.serve("rcb6", make=False)
        self.assertEqual(self.main("--toggle", "--session", self.SID, "--block", "block:rcb6"), self.INLINE_PANE)

    def test_stale_status_file_opens_show_pane(self):
        name = "a"
        (show.REXCHAT_STATE / f"{name}.json").write_text(json.dumps({"state": "idle"}))
        dead = socket.socket(socket.AF_UNIX)  # a socket file nobody listens on (a killed run)
        dead.bind(str(show.REXCHAT_STATE / f"{name}.sock"))
        dead.close()
        left = [show.REXCHAT_STATE / "pictures" / name / "inbox-1", show.REXCHAT_TMP / name / "inbox-1"]
        for d in left:
            show.make_private_dirs(d)
        out = self.main(str(self.png), "--session", self.SID, "--block", "block:a")
        self.assertEqual((out["pane"], out["index"], out["created_pane"]), ("block:s1", 1, True))
        for d in left:
            self.assertEqual(list(d.iterdir()), [])
        self.assertEqual(self.toggle("block:a")["action"], "closed")

    def test_neither_opens_show_pane(self):
        out = self.main(str(self.png), "--session", self.SID, "--block", "block:a")
        self.assertEqual(out["pane"], "block:s1")
        self.assertFalse((show.REXCHAT_STATE / "pictures").exists())

    def test_live_instance_in_another_pane_does_not_count(self):
        self.serve("b")
        out = self.main(str(self.png), "--session", self.SID, "--block", "block:a")
        self.assertEqual(out["pane"], "block:s1")


class SessionDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = show.STATE
        show.STATE = Path(self.tmp.name) / "sessions"

    def tearDown(self):
        show.STATE = self.saved
        self.tmp.cleanup()

    def test_valid(self):
        sid = "01a1fabd-f4aa-745d-aedb-be1ddbc3da73"
        want = show.STATE.resolve() / sid
        self.assertEqual(show.session_dir(sid), want)
        self.assertEqual(show.session_dir("session:" + sid), want)

    def test_invalid(self):
        for bad in ["../../x", "session:../../x", "", "session:", "abc", "01a1fabd/../x",
                    "01a1fabd-f4aa-745d-aedb-be1ddbc3da73/..", "g" * 10, "a" * 65,
                    "01a1fabd f4aa", "session:session:01a1fabd"]:
            self.assertIsNone(show.session_dir(bad), bad)

    def test_result_is_direct_child(self):
        for sid in ["12345678", "--------", "a" * 64]:
            self.assertEqual(show.session_dir(sid).parent, show.STATE.resolve())


class WriteJsonTest(unittest.TestCase):
    def test_atomic_and_no_leftovers(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "state.json"
            errors = []

            def writer(i):
                try:
                    for j in range(50):
                        show.write_json(path, {"writer": i, "n": j})
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.assertEqual(errors, [])
            self.assertIn("writer", json.loads(path.read_text()))
            self.assertEqual(sorted(os.listdir(t)), ["state.json"])


def view_with(*layers):
    return {"windows": [{"window_id": "window:w", "layers": [
        {"layer_id": "layer:t", "kind": "tiled", "blocks": [{"block_id": "block:a", "label": "shell"}]},
        *layers]}]}


class OwnFloatTest(unittest.TestCase):
    rec = {"layer": "layer:f", "block": "block:f"}

    def test_own_float(self):
        v = view_with({"layer_id": "layer:f", "kind": "floating",
                       "blocks": [{"block_id": "block:f", "label": show.ENLARGED_LABEL}]})
        self.assertEqual(show.own_float(v, self.rec, "window:w"), "layer:f")

    def test_no_record(self):
        v = view_with({"layer_id": "layer:f", "kind": "floating",
                       "blocks": [{"block_id": "block:f", "label": show.ENLARGED_LABEL}]})
        self.assertIsNone(show.own_float(v, None, "window:w"))
        self.assertIsNone(show.own_float(v, {}, "window:w"))

    def test_hand_made_float_with_same_label(self):
        v = view_with({"layer_id": "layer:h", "kind": "floating",
                       "blocks": [{"block_id": "block:h", "label": show.ENLARGED_LABEL}]})
        self.assertIsNone(show.own_float(v, self.rec, "window:w"))

    def test_shared_layer(self):
        v = view_with({"layer_id": "layer:f", "kind": "floating",
                       "blocks": [{"block_id": "block:f"}, {"block_id": "block:x"}]})
        self.assertIsNone(show.own_float(v, self.rec, "window:w"))

    def test_block_replaced(self):
        v = view_with({"layer_id": "layer:f", "kind": "floating", "blocks": [{"block_id": "block:x"}]})
        self.assertIsNone(show.own_float(v, self.rec, "window:w"))

    def test_tiled_never(self):
        v = {"windows": [{"window_id": "window:w", "layers": [{"layer_id": "layer:f", "kind": "tiled",
                                      "blocks": [{"block_id": "block:f"}]}]}]}
        self.assertIsNone(show.own_float(v, self.rec, "window:w"))

    def test_other_tabs_float_left_alone(self):
        # Tab A's float is never closed by an enlarge from tab B.
        v = view_with({"layer_id": "layer:f", "kind": "floating",
                       "blocks": [{"block_id": "block:f", "label": show.ENLARGED_LABEL}]})
        v["windows"].append({"window_id": "window:b", "layers": []})
        self.assertIsNone(show.own_float(v, self.rec, "window:b"))
        self.assertEqual(show.own_float(v, self.rec, "window:w"), "layer:f")


class EnlargeRecordsTest(unittest.TestCase):
    def test_per_window(self):
        a = {"layer": "layer:a", "block": "block:a"}
        b = {"layer": "layer:b", "block": "block:b"}
        state = {"enlarge": {"window:a": a, "window:b": b}}
        self.assertEqual(show.enlarge_records(state), {"window:a": a, "window:b": b})

    def test_legacy_single_record(self):
        rec = {"window": "window:a", "layer": "layer:a", "block": "block:a"}
        self.assertEqual(show.enlarge_records({"enlarge": rec}), {"window:a": rec})
        self.assertEqual(show.enlarge_records({"enlarge": {"layer": "layer:a"}}), {})

    def test_missing_or_bad(self):
        for state in [{}, {"enlarge": None}, {"enlarge": []}, {"enlarge": {"window:a": "x"}}]:
            self.assertEqual(show.enlarge_records(state), {}, state)


if __name__ == "__main__":
    unittest.main()
