"""Unit tests for the pure parts of show. Run: python3 -m unittest test_show.py"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import threading
import unittest
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


class ToggleTest(unittest.TestCase):
    SID = "session:01a1fabd-f4aa-745d-aedb-be1ddbc3da73"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (show.STATE, show.rex, show.run)
        show.STATE = Path(self.tmp.name) / "sessions"
        self.calls = []
        self.viewers = set()  # blocks whose foreground program is show's viewer for this session
        self.failures = {}  # block -> number of process queries that fail before one succeeds
        self.view = {"windows": [{"window_id": "window:w", "layers": [
            {"layer_id": "layer:t", "kind": "tiled", "blocks": [{"block_id": "block:a", "label": "shell"}]}]}]}

        def fake_rex(*args):
            self.calls.append(args)
            if args[:2] == ("session", "inspect"):
                return self.view
            if args[0] == "split":
                return {"block_ids": ["block:s"]}
            return {}

        def fake_run(args, timeout=60):
            self.calls.append(tuple(args[1:]))
            out = ""
            if args[1:3] == ["block", "call"] and args[-1] == "process":
                b = args[args.index("-b") + 1]
                if self.failures.get(b):
                    self.failures[b] -= 1
                    return show.subprocess.CompletedProcess(args, 124, "", "rex: timed out")
                fg = {"script": str(show.VIEWER), "cwd": str(show.show_dir(self.SID))} if b in self.viewers \
                    else {"script": None, "cwd": "/tmp", "name": "zsh"}
                out = json.dumps({"foreground": fg})
            return show.subprocess.CompletedProcess(args, 0, out, "")
        show.rex, show.run = fake_rex, fake_run
        self.sleep = show.time.sleep
        show.time.sleep = lambda s: None

    def tearDown(self):
        show.STATE, show.rex, show.run = self.saved
        show.time.sleep = self.sleep
        self.tmp.cleanup()

    def add_block(self, block_id, label=None):
        self.view["windows"][0]["layers"][0]["blocks"].append({"block_id": block_id, "label": label or show.LABEL})

    def toggle(self, block="block:a"):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            show.cmd_toggle(type("A", (), {"session": self.SID, "block": block})())
        return json.loads(buf.getvalue())

    def closes(self):
        return [c for c in self.calls if c[:2] == ("block", "close")]

    def test_opens_then_closes(self):
        self.assertEqual(self.toggle(), {"action": "opened", "pane": "block:s", "window": "window:w"})
        split = [c for c in self.calls if c[0] == "split"][0]
        self.assertIn("--focus=false", split)
        self.add_block("block:s")
        self.viewers.add("block:s")
        self.calls.clear()
        self.assertEqual(self.toggle(), {"action": "closed", "pane": "block:s", "window": "window:w"})
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s")])
        state = json.loads((show.show_dir(self.SID) / "state.json").read_text())
        self.assertNotIn("window:w", state["panes"])

    def test_labelled_pane_without_state_is_not_ours(self):
        self.add_block("block:x")
        self.viewers.add("block:x")  # even running a viewer: not recorded, so not adopted
        result = self.toggle()
        self.assertEqual(result["action"], "opened")
        self.assertEqual(result["pane"], "block:s")
        self.assertEqual(self.closes(), [])

    def test_recorded_pane_running_something_else_is_not_ours(self):
        self.add_block("block:x")
        d = show.show_dir(self.SID)
        d.mkdir(parents=True)
        show.write_json(d / "state.json", {"panes": {"window:w": "block:x"}})
        result = self.toggle()  # block:x now runs a shell, not the viewer
        self.assertEqual((result["action"], result["pane"]), ("opened", "block:s"))
        self.assertEqual(self.closes(), [])
        state = json.loads((d / "state.json").read_text())
        self.assertEqual(state["panes"]["window:w"], "block:s")

    def test_ensure_pane_does_not_adopt_label(self):
        self.add_block("block:x")
        d = show.show_dir(self.SID)
        d.mkdir(parents=True)
        pane, _, created = show.ensure_pane(self.SID, "block:a", d)
        self.assertEqual((pane, created), ("block:s", True))

    def test_toggle_from_show_pane_closes_it_and_focuses_main(self):
        self.add_block("block:s")
        self.viewers.add("block:s")
        d = show.show_dir(self.SID)
        d.mkdir(parents=True)
        show.write_json(d / "state.json", {"panes": {"window:w": "block:s"}})
        show.write_json(d.parent / "session.json", {"main_block": "block:a"})
        result = self.toggle(block="block:s")
        self.assertEqual(result, {"action": "closed", "pane": "block:s", "window": "window:w",
                                  "focused": "block:a"})
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s")])
        self.assertIn(("focus", "-s", self.SID, "block:a"), self.calls)

    def test_toggle_from_show_pane_main_elsewhere(self):
        self.add_block("block:s")
        self.viewers.add("block:s")
        d = show.show_dir(self.SID)
        d.mkdir(parents=True)
        show.write_json(d / "state.json", {"panes": {"window:w": "block:s"}})
        show.write_json(d.parent / "session.json", {"main_block": "block:other-tab"})
        result = self.toggle(block="block:s")
        self.assertEqual(result["action"], "closed")
        self.assertNotIn("focused", result)
        self.assertFalse([c for c in self.calls if c[:1] == ("focus",)])


    def recorded(self, block="block:s"):
        self.add_block(block)
        d = show.show_dir(self.SID)
        (d / "images").mkdir(parents=True)
        show.write_json(d / "state.json", {"panes": {"window:w": block}})
        return d

    def splits(self):
        return [c for c in self.calls if c[:1] == ("split",)]

    def test_one_failed_query_is_retried(self):
        self.recorded()
        self.viewers.add("block:s")
        self.failures["block:s"] = 1
        self.assertEqual(self.toggle()["action"], "closed")
        self.assertEqual(self.closes(), [("block", "close", "-s", self.SID, "block:s")])

    def test_unknown_toggle_changes_nothing(self):
        d = self.recorded()
        self.viewers.add("block:s")
        self.failures["block:s"] = 2
        with self.assertRaises(SystemExit) as e:
            self.toggle()
        self.assertEqual(e.exception.code, show.RETRYABLE)
        self.assertEqual((self.closes(), self.splits()), ([], []))
        self.assertEqual(json.loads((d / "state.json").read_text())["panes"], {"window:w": "block:s"})

    def test_unknown_open_pane_changes_nothing(self):
        d = self.recorded()
        self.failures["block:s"] = 2
        with self.assertRaises(SystemExit) as e:
            show.cmd_open_pane(type("A", (), {"session": self.SID, "block": "block:a"})())
        self.assertEqual(e.exception.code, show.RETRYABLE)
        self.assertEqual(self.splits(), [])
        self.assertEqual(json.loads((d / "state.json").read_text())["panes"], {"window:w": "block:s"})

    def test_unknown_show_keeps_picture(self):
        import contextlib
        import io
        d = self.recorded()
        self.failures["block:s"] = 2
        png = Path(self.tmp.name) / "p.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (3).to_bytes(4, "big") + (2).to_bytes(4, "big"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as e:
            show.cmd_show(type("A", (), {"session": self.SID, "block": "block:a", "file": str(png), "title": None})())
        self.assertEqual(e.exception.code, show.RETRYABLE)
        items = json.loads((d / "history.json").read_text())["items"]
        self.assertEqual(len(items), 1)
        self.assertTrue(Path(items[0]["png"]).is_file())
        out = json.loads(buf.getvalue())
        self.assertIsNone(out["pane"])
        self.assertIn("error", out)
        self.assertEqual(self.splits(), [])
        self.assertEqual(json.loads((d / "state.json").read_text())["panes"], {"window:w": "block:s"})

    def test_query_answers(self):
        d = show.show_dir(self.SID)

        def answer(stdout, code=0):
            show.run = lambda args, timeout=60: show.subprocess.CompletedProcess(args, code, stdout, "")
            return show.query_viewer(self.SID, "block:s", d)
        self.assertIsNone(answer("", 1))
        self.assertIsNone(answer("not json"))
        self.assertFalse(answer(json.dumps({"foreground": None})))
        self.assertFalse(answer(json.dumps({"foreground": {"script": "/x/viewer", "cwd": str(d)}})))
        self.assertTrue(answer(json.dumps({"foreground": {"script": str(show.VIEWER), "cwd": str(d)}})))


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
