"""Unit tests for rexbar. Run: python3 -m unittest test_rexbar.py (from this directory)."""
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("rexbar_cli", str(HERE / "rexbar"))
_spec = importlib.util.spec_from_loader("rexbar_cli", _loader)
rb = importlib.util.module_from_spec(_spec)
_loader.exec_module(rb)

UUID = "01a1fabd-0000-7000-8000-000000000001"
SID = "session:" + UUID

OBJECTIVES = """# Objectives

Nested goals, each a few words with a status: todo, doing, done, blocked.

- doing: top goal
  - done: first
  - doing: middle
    - doing: deepest one
    - doing: deepest two
    - todo: later
  - blocked: waiting on review
- todo: other top
* done: star bullet
- maybe: not a status
not a goal: at all
"""


class TempState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = rb.STATE
        rb.STATE = Path(self.tmp.name) / "sessions"
        self.dir = rb.STATE / UUID
        self.dir.mkdir(parents=True)

    def tearDown(self):
        rb.STATE = self.saved
        self.tmp.cleanup()


class ObjectivesTest(unittest.TestCase):
    def test_parse_nesting(self):
        items = rb.parse_objectives(OBJECTIVES)
        self.assertEqual([i["status"] for i in items],
                         ["doing", "done", "doing", "doing", "doing", "todo", "blocked", "todo", "done"])
        self.assertEqual([i["depth"] for i in items], [0, 1, 1, 2, 2, 2, 1, 0, 0])
        self.assertEqual(items[3]["path"], "top goal / middle / deepest one")
        self.assertEqual(items[6]["path"], "top goal / waiting on review")

    def test_summary(self):
        s = rb.summarize_objectives(rb.parse_objectives(OBJECTIVES))
        self.assertEqual(s, {"total": 9, "done": 2, "blocked": 1, "doing": "deepest one", "doing_more": 1,
                             "doing_path": "top goal / middle / deepest one"})

    def test_tabs_and_empty(self):
        items = rb.parse_objectives("- doing: a\n\t- doing: b\n")
        self.assertEqual(rb.summarize_objectives(items)["doing"], "b")
        s = rb.summarize_objectives([])
        self.assertEqual((s["total"], s["doing"], s["blocked"]), (0, None, 0))

    def test_case_and_checkbox(self):
        items = rb.parse_objectives("- DONE: x\n- [ ] todo: y\n")
        self.assertEqual([i["status"] for i in items], ["done", "todo"])


class LogTest(TempState):
    def test_baseline_then_changes(self):
        items = rb.parse_objectives("- doing: a\n  - todo: b\n")
        log = rb.update_log(self.dir, items, {}, now=100)
        self.assertEqual(log["events"], [])
        items = rb.parse_objectives("- doing: a\n  - done: b\n  - blocked: c\n")
        log = rb.update_log(self.dir, items, {}, now=200)
        self.assertEqual([(e["status"], e["text"], e["t"]) for e in log["events"]],
                         [("done", "b", 200), ("blocked", "c", 200)])
        # Same content again: nothing new.
        log = rb.update_log(self.dir, items, {}, now=300)
        self.assertEqual(len(log["events"]), 2)

    def test_missing_objectives_is_not_removal(self):
        rb.update_log(self.dir, rb.parse_objectives("- done: a\n"), {}, now=1)
        rb.update_log(self.dir, None, {}, now=2)
        log = rb.update_log(self.dir, rb.parse_objectives("- done: a\n"), {}, now=3)
        self.assertEqual(log["events"], [])

    def test_reviews(self):
        rb.update_log(self.dir, None, {"last_reviewed": {"/r/app": "aaaa"}}, now=1)
        log = rb.update_log(self.dir, None, {"last_reviewed": {"/r/app": "bbbbbbbbbbbb"}}, now=5)
        self.assertEqual(log["events"], [{"t": 5, "kind": "review", "text": "app at bbbbbbbb"}])

    def test_malformed_log_is_replaced(self):
        (self.dir / "bar").mkdir()
        (self.dir / "bar" / "objectives-log.json").write_text("{not json")
        log = rb.update_log(self.dir, rb.parse_objectives("- todo: a\n"), {}, now=1)
        self.assertEqual(log["events"], [])
        self.assertIn("objectives", json.loads((self.dir / "bar" / "objectives-log.json").read_text()))


class EventsTest(unittest.TestCase):
    def test_merge_and_order(self):
        session = {"promoted_at": 50, "ticket": "TAL-1", "landed_at": 90}
        history = {"items": [{"time": 10, "title": "a"}, {"time": 70, "title": "b"}, {"title": "no time"}]}
        log = {"events": [{"t": 60, "kind": "objective", "status": "done", "text": "x"}, {"t": "bad"}]}
        kinds = [e["kind"] for e in rb.collect_events(session, history, log)]
        self.assertEqual(kinds, ["picture", "promote", "objective", "picture", "land"])

    def test_filing_time(self):
        events = rb.collect_events({"filing": {"at": 5, "ticket": "TAL-9"}}, {}, {})
        self.assertEqual(events[0]["text"], "promoted to TAL-9")

    def test_malformed(self):
        self.assertEqual(rb.collect_events([], "x", None), [])


class StripTest(unittest.TestCase):
    def events(self, n):
        return [{"t": i, "kind": "picture"} for i in range(n)]

    def test_fits_all(self):
        self.assertEqual(rb.strip_line(self.events(3), 10, color=False).plain(), "ppp")

    def test_drops_oldest(self):
        ev = [{"t": i, "kind": "objective", "status": "done"} for i in range(10)] + [{"t": 99, "kind": "review"}]
        line = rb.strip_line(ev, 5, color=False)
        self.assertEqual(line.plain(), "…+++r")
        self.assertEqual(line.width(), 5)

    def test_zero_and_one(self):
        self.assertEqual(rb.strip_line(self.events(3), 0).plain(), "")
        self.assertEqual(rb.strip_line(self.events(3), 1).plain(), "…")

    def test_color_cells(self):
        line = rb.strip_line([{"t": 1, "kind": "picture"}, {"t": 2, "kind": "land"}], 5)
        self.assertEqual(line.plain(), rb.CELL * 2)
        self.assertIn("\x1b[35m", line.render())


class TruncateTest(unittest.TestCase):
    def test_wide_and_ellipsis(self):
        line = rb.Line([("ab", None), ("漢字cd", "1")])
        self.assertEqual(line.width(), 8)
        cut = line.truncate(4)
        self.assertEqual(cut.plain(), "ab…")
        self.assertLessEqual(cut.width(), 4)
        self.assertEqual(line.truncate(8).plain(), line.plain())
        self.assertEqual(line.truncate(0).plain(), "")


def sample_model(n_events=30):
    items = rb.parse_objectives(OBJECTIVES)
    return {
        "session": {"ticket": "TAL-203", "branch": "u/tfriedman/bar"},
        "objectives": rb.summarize_objectives(items),
        "events": [{"t": i, "kind": "picture", "text": f"pic {i}"} for i in range(n_events)],
        "agents": {"working": 1, "waiting": 1, "idle": 2, "sub": 3,
                   "main": {"harness": "claude-code", "model_display": "Opus 5.5", "ctx_pct": 42}},
        "usage": {"claude": {"5h": 9.0, "wk": 56.0}, "codex": {"wk": 26.0}},
    }


class ComposeTest(unittest.TestCase):
    def test_widths_and_rows(self):
        for width in (20, 60, 120, 200):
            for rows in (1, 2, 3):
                lines = rb.compose(sample_model(), width, rows, color=False, now=100)
                self.assertEqual(len(lines), rows, (width, rows))
                for line in lines:
                    self.assertLessEqual(line.width(), width, (width, rows, line.plain()))
                if rows >= 3:
                    self.assertTrue(lines[2].plain(), (width, rows))
                top = lines[0].plain()
                self.assertTrue(top.startswith("TAL-203 2/9 !1"), (width, top))

    def test_important_first(self):
        top = rb.compose(sample_model(), 20, 1, color=False)[0].plain()
        self.assertTrue(top.endswith("…"))
        wide = rb.compose(sample_model(), 200, 1, color=False)[0].plain()
        for part in ("deepest one", "+1", "1 working", "1 need you", "5h 9%", "wk 56%", "Opus 5.5", "ctx 42%"):
            self.assertIn(part, wide)
        self.assertIn("p" * 30, wide)

    def test_two_rows_put_history_below(self):
        lines = rb.compose(sample_model(5), 120, 2, color=False, now=100)
        self.assertNotIn("ppppp", lines[0].plain())
        self.assertTrue(lines[1].plain().startswith("ppppp  picture: pic 4"))

    def test_three_and_four_rows(self):
        model = sample_model(5)
        model["objectives"]["doing_path"] = "top goal / middle / deepest one"
        model["agents"]["panes"] = rb.agent_panes([
            {"group": "quiet", "state": "idle", "title": "idle one", "updated_at": "3"},
            {"group": "needs_you", "state": "done", "title": "Claude", "msg": "pick one", "updated_at": "1"},
            {"group": "working", "state": "working", "title": "Codex", "msg": "running tests",
             "children": {"working": 2}, "updated_at": "2"},
        ])
        lines = rb.compose(model, 120, 4, color=False, now=100)
        row = lines[2].plain()
        self.assertTrue(row.startswith("need you Claude: pick one \u2502 working Codex +2: running"), row)
        self.assertTrue(row.endswith("idle idle one"), row)
        wide = rb.compose(model, 200, 3, color=False, now=100)[2].plain()
        self.assertIn("working Codex +2: running tests", wide)
        self.assertEqual(lines[3].plain(), "top goal \u203a middle \u203a deepest one")
        narrow = rb.compose(model, 30, 3, color=False, now=100)[2]
        self.assertTrue(narrow.plain().startswith("need you Claude"))
        self.assertLessEqual(narrow.width(), 30)

    def test_relative_time_advances(self):
        model = sample_model(1)
        self.assertIn("1m ago", rb.compose(model, 80, 2, color=False, now=60)[1].plain())
        self.assertIn("2m ago", rb.compose(model, 80, 2, color=False, now=120)[1].plain())

    def test_full_row_keeps_last_cell(self):
        full = rb.Line([("x" * 10, None)])
        short = rb.Line([("x" * 4, None)])
        frame = rb.paint([full, short], 10)
        self.assertEqual(frame, "\x1b[H" + "x" * 10 + "\r\n" + "xxxx\x1b[K")
        # A full last row is not followed by an erase either (ED would delete its last cell).
        self.assertTrue(rb.paint([short, full], 10).endswith("x" * 10))

    def test_scratch_and_empty(self):
        model = {"session": {}, "objectives": rb.summarize_objectives([]), "events": [],
                 "agents": None, "usage": None}
        lines = rb.compose(model, 60, 2, color=False)
        self.assertTrue(lines[0].plain().startswith("scratch"))
        self.assertIn("–", lines[0].plain())
        self.assertIn("no history yet", lines[1].plain())

    def test_color_render_has_no_newlines(self):
        for line in rb.compose(sample_model(), 120, 2):
            self.assertNotIn("\n", line.render())


class SourceTest(TempState):
    def test_malformed_files(self):
        (self.dir / "session.json").write_text("[1, 2")
        (self.dir / "objectives.md").write_bytes(b"\xff\xfe junk\n- doing: x\n")
        (self.dir / "show").mkdir()
        (self.dir / "show" / "history.json").write_text('{"items": "nope"}')
        src = rb.Source(self.dir)
        self.assertTrue(src.poll())
        model = src.model([{"session_id": SID, "group": "working"}, "junk"], {"claude": {}}, SID)
        lines = rb.compose(model, 80, 2, color=False)
        self.assertTrue(lines[0].plain().startswith("scratch 0/1"))
        self.assertFalse(src.poll())

    def test_missing_session_dir(self):
        src = rb.Source(None)
        self.assertFalse(src.poll())
        rb.compose(src.model(None, None, SID), 40, 1)

    def test_only_bar_dir_written(self):
        (self.dir / "objectives.md").write_text("- doing: a\n")
        rb.Source(self.dir).poll()
        self.assertEqual(sorted(os.listdir(self.dir)), ["bar", "objectives.md"])


class AgentsTest(unittest.TestCase):
    def test_counts_and_main(self):
        rows = [
            {"session_id": SID, "group": "working", "state": "working", "children": {"working": 3, "done": 2},
             "block_id": "block:m", "usage": {"model": "gpt-6", "ctx_pct": 10}, "harness": "codex"},
            {"session_id": SID, "group": "needs_you", "state": "done", "children": {}},
            {"session_id": SID, "group": "quiet", "state": "idle"},
            {"session_id": SID, "group": "working", "stale": True},
            {"session_id": "session:other", "group": "working"},
        ]
        s = rb.agent_summary(rb.board_rows(SID, rows), main_block="block:m")
        self.assertEqual((s["working"], s["waiting"], s["idle"], s["sub"]), (1, 1, 2, 3))
        self.assertEqual(s["main"]["model"], "gpt-6")
        self.assertEqual(rb.board_rows(SID, {"not": "a list"}), [])


class UsageTest(unittest.TestCase):
    SAMPLE = {"cards": [
        {"provider": "claude", "meters": [{"label": "Current session", "used_percent": 9.0},
                                          {"label": "This week", "used_percent": 56.0},
                                          {"label": "Today", "used_percent": None}]},
        {"provider": "codex", "meters": [{"label": "This week", "used_percent": 26.0}]},
        "junk"]}

    def test_parse(self):
        self.assertEqual(rb.parse_usage(self.SAMPLE), {"claude": {"5h": 9.0, "wk": 56.0}, "codex": {"wk": 26.0}})
        self.assertEqual(rb.parse_usage(None), {})

    def test_cache_ttl_and_failure(self):
        with tempfile.TemporaryDirectory() as t:
            script = Path(t) / "fake"
            count = Path(t) / "count"
            script.write_text("#!/bin/sh\necho x >> %s\necho '%s'\n" % (count, json.dumps(self.SAMPLE)))
            script.chmod(0o755)
            cache = rb.UsageCache(Path(t) / "c" / "u.json", [str(script)], ttl=60)
            self.assertEqual(cache.read(), (None, None))
            cache.refresh()
            cache.refresh()  # fresh: no second run
            usage, age = cache.read()
            self.assertEqual(usage["claude"]["wk"], 56.0)
            self.assertLess(age, 5)
            self.assertEqual(count.read_text().count("x"), 1)
            # A failing command is cached as a failure, shown as "–".
            bad = rb.UsageCache(Path(t) / "c" / "bad.json", [str(Path(t) / "missing")], ttl=60)
            bad.refresh()
            self.assertEqual(bad.read()[0], None)
            self.assertFalse(bad.stale())
            self.assertIn("–", rb.usage_line(bad.read()[0]).plain())


class PathTest(unittest.TestCase):
    def test_subprocesses_get_tool_dirs(self):
        out = rb.run(["/bin/sh", "-c", "echo $PATH"]).stdout.strip().split(":")
        for d in (str(Path.home() / ".cargo/bin"), str(Path.home() / ".local/bin"), "/opt/homebrew/bin"):
            self.assertIn(d, out)

    def test_tools_are_absolute(self):
        for t in (rb.REX, rb.REXBOARD, rb.AI_USAGE):
            self.assertTrue(os.path.isabs(t), t)


DAY = 86400
NOW = 1_800_000_000


def held_state(ticket, provider, path, age_days, landed=False, **extra):
    record = {"path": path, "provider": provider, "at": NOW - age_days * DAY}
    state = {"ticket": ticket, "label": f"{ticket} work", "kind": "ticketed", "worktree": path,
             "worktree_provider": provider, "worktree_created": record, "branch": f"u/tfriedman/{ticket.lower()}"}
    if provider == "treehouse":
        state["lease_id"] = record["lease_id"] = "lease-1"
    if landed:
        state["landed_at"] = NOW - DAY
    state.update(extra)
    return state


class WorktreeTest(TempState):
    def write(self, uuid, state):
        d = rb.STATE / uuid
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.json").write_text(json.dumps(state))

    def test_held_git_and_slot(self):
        info = rb.worktree_info(held_state("TAL-1", "git", "/tmp/wt-a", 3))
        self.assertEqual((info["held"], info["provider"], info["landed"]), (True, "git", False))
        self.assertEqual(rb.own_worktree_line(info, NOW).plain(), " wt 3d")
        slot = rb.worktree_info(held_state("TAL-2", "treehouse", "/tmp/slot/1/precog", 2))
        self.assertEqual(rb.own_worktree_line(slot, NOW).plain(), " slot 2d")

    def test_adopted_is_not_held(self):
        state = held_state("TAL-3", "git", "/tmp/wt-b", 1)
        state["worktree_created"]["provider"] = "adopted"
        self.assertIsNone(rb.worktree_info(state))
        # A path with no creation record, or a record for another path, is not held either.
        self.assertIsNone(rb.worktree_info({"worktree": "/tmp/wt-b", "worktree_provider": "adopted"}))
        state = held_state("TAL-3", "git", "/tmp/wt-b", 1)
        state["worktree"] = "/tmp/elsewhere"
        self.assertIsNone(rb.worktree_info(state))

    def test_landed_and_holding_warns(self):
        info = rb.worktree_info(held_state("TAL-4", "git", "/tmp/wt-c", 5, landed=True))
        line = rb.own_worktree_line(info, NOW)
        self.assertEqual(line.plain(), " wt 5d landed")
        self.assertIn("\x1b[1;31m", line.render())

    def test_pending_and_failed(self):
        pending = rb.worktree_info({"ticket": "TAL-5", "pending": ["worktree"]})
        self.assertEqual(rb.own_worktree_line(pending, NOW).plain(), " wt pending")
        self.assertFalse(pending["held"])
        failed = rb.worktree_info({"ticket": "TAL-5", "pending": ["worktree"], "worktree_error": "lease timed out"})
        self.assertEqual(rb.own_worktree_line(failed, NOW).plain(), " wt failed")
        self.assertIsNone(rb.worktree_info({"ticket": "TAL-6"}))
        self.assertIsNone(rb.worktree_info("junk"))

    def test_fleet_counts_and_ages(self):
        self.write("01a1fabd-0000-7000-8000-00000000000a", held_state("TAL-10", "git", "/tmp/wt-10", 11))
        self.write("01a1fabd-0000-7000-8000-00000000000b", held_state("TAL-11", "treehouse", "/tmp/s/1/p", 2,
                                                                      landed=True))
        self.write("01a1fabd-0000-7000-8000-00000000000c", held_state("TAL-12", "treehouse", "/tmp/s/2/p", 0.5))
        adopted = held_state("TAL-13", "git", "/tmp/wt-13", 30)
        adopted["worktree_created"]["provider"] = "adopted"
        self.write("01a1fabd-0000-7000-8000-00000000000d", adopted)
        (rb.STATE / "01a1fabd-0000-7000-8000-00000000000e").mkdir()  # no session.json
        (rb.STATE / "01a1fabd-0000-7000-8000-00000000000f").mkdir()
        (rb.STATE / "01a1fabd-0000-7000-8000-00000000000f" / "session.json").write_text("{bad")
        (rb.STATE / "locks").mkdir()
        held = rb.Fleet().scan(force=True)
        self.assertEqual(sorted(h["ticket"] for h in held), ["TAL-10", "TAL-11", "TAL-12"])
        self.assertEqual(rb.fleet_line(held, NOW).plain(), "held 3 (oldest 11d) slots 2, 1 landed")
        self.assertEqual(rb.fleet_line(held, NOW, compact=True).plain(), "held 3/11d")
        self.assertEqual(rb.held_list_line(held, NOW).plain(),
                         "TAL-11 slot 2d landed │ TAL-10 wt 11d │ TAL-12 slot 12h")

    def test_fleet_rereads_only_changed_files(self):
        uuid = "01a1fabd-0000-7000-8000-00000000000a"
        self.write(uuid, held_state("TAL-10", "git", "/tmp/wt-10", 1))
        fleet = rb.Fleet(every=0)
        self.assertEqual(len(fleet.scan()), 1)
        reads = []
        saved = rb.read_json
        rb.read_json = lambda *a: reads.append(a) or saved(*a)
        try:
            fleet.scan()
            self.assertEqual(reads, [])
            path = rb.STATE / uuid / "session.json"
            path.write_text(json.dumps({"ticket": "TAL-10"}))  # released
            os.utime(path, ns=(time.time_ns() + 10 ** 9, time.time_ns() + 10 ** 9))
            self.assertEqual(fleet.scan(), [])
        finally:
            rb.read_json = saved

    def test_none_held_skips_the_item(self):
        self.assertEqual(rb.Fleet().scan(force=True), [])
        lines = rb.compose(sample_model(), 200, 4, color=False, now=NOW)
        self.assertNotIn("held", lines[0].plain())
        self.assertEqual(lines[3].plain(), "top goal › middle › deepest one")  # no held list

    def test_compose_with_worktrees(self):
        held = [{**rb.worktree_info(held_state("TAL-11", "treehouse", "/tmp/s/1/p", 3)),
                 "session_id": SID}]
        model = sample_model(5)
        model["worktree"] = held[0]
        model["held"] = held
        model["objectives"]["doing_path"] = "top goal / middle / deepest one"
        lines = rb.compose(model, 140, 5, color=False, now=NOW)
        self.assertTrue(lines[0].plain().startswith("TAL-203 2/9 !1 blocked slot 3d ▸ deepest one"))
        self.assertIn("held 1/3d", lines[0].plain())  # short form when the goal would be squeezed
        wide = rb.compose(model, 220, 1, color=False, now=NOW)[0].plain()
        self.assertIn("held 1 (oldest 3d) slots 1", wide)
        self.assertEqual(lines[3].plain(), "TAL-11 slot 3d")
        self.assertEqual(lines[4].plain(), "top goal › middle › deepest one")
        # Narrow: the ticket and the own marker survive; the fleet item is cut first.
        narrow = rb.compose(model, 40, 1, color=False, now=NOW)[0].plain()
        self.assertTrue(narrow.startswith("TAL-203 2/9 !1 blocked slot 3d"), narrow)
        for width in (20, 60, 120, 200):
            for line in rb.compose(model, width, 4, color=False, now=NOW):
                self.assertLessEqual(line.width(), width)


class FakeRex:
    """Enough of `rex` for open/close/fit: one tab, two panes side by side."""

    def __init__(self):
        self.calls = []
        self.layer = {"layer_id": "layer:t", "kind": "tiled",
                      "layout": {"rect": [0, 0, 1, 1], "split": {
                          "split_id": "split:root", "direction": "horizontal", "ratio": 0.5,
                          "before": {"rect": [0, 0, 0.5, 1], "block_id": "block:a"},
                          "after": {"rect": [0.5, 0, 0.5, 1], "block_id": "block:b"}}},
                      "blocks": [{"block_id": "block:a", "label": "shell"},
                                 {"block_id": "block:b", "label": "shell"}]}
        self.n = 0
        self.tab_rows = None
        # block -> foreground process {"pid", "script"}; pid -> command line (for ps)
        self.procs = {"block:a": {"pid": 1, "script": None}, "block:b": {"pid": 2, "script": None}}
        self.args = {1: "-zsh", 2: "-zsh"}
        self.closed = []
        self.process_failures = 0  # the next N process calls fail like a timeout
        self.floor = 3  # the app never makes a tiled pane shorter than 3 rows

    def view(self):
        return {"windows": [{"window_id": "window:w", "layers": [json.loads(json.dumps(self.layer))]}]}

    def __call__(self, *args, timeout=20):
        self.calls.append(args)
        if args[:2] == ("session", "inspect"):
            return self.view()
        if args[:2] == ("block", "call") and args[-1] == "process":
            if self.process_failures:
                self.process_failures -= 1
                raise RuntimeError("rex block call: timed out")
            block = args[args.index("-b") + 1]
            if block not in {b["block_id"] for b in self.layer["blocks"]}:
                raise RuntimeError("not found")
            return {"foreground": self.procs.get(block)}
        if args[:2] == ("block", "close"):
            bar = args[-1]
            self.closed.append(bar)
            self.layer["blocks"] = [b for b in self.layer["blocks"] if b["block_id"] != bar]
            split = self.layer["layout"]["split"]
            if split["after"].get("block_id") == bar:
                self.layer["layout"] = split["before"]
            return {}
        if args[:2] == ("api", "call"):
            method, payload = args[2], json.loads(args[3])
            if method == "session.new_split":
                self.n += 1
                bar = f"block:bar{self.n}"
                old = self.layer["layout"]
                self.layer["layout"] = {"rect": [0, 0, 1, 1], "split": {
                    "split_id": f"split:new{self.n}", "direction": payload["direction"], "ratio": payload["ratio"],
                    "before": old, "after": {"rect": [0, 0.95, 1, 0.05], "block_id": bar}}}
                self.layer["blocks"].append({"block_id": bar, "label": payload["layout"]["block"]["label"]})
                command = payload["layout"]["block"]["options"]["command"]
                pid = 100 + self.n
                self.procs[bar] = {"pid": pid, "script": command[1]}
                self.args[pid] = " ".join(command)
                return {"block_ids": [bar], "split_ids": [f"split:new{self.n}"], "anchor_id": payload["anchor_id"]}
            if method == "session.resize_pane":
                self.layer["layout"]["split"]["ratio"] = payload["ratio"]
                return {"revision": 1}
            if method == "com.superlogical.terminal.size":
                if self.tab_rows is None:
                    return {"rows": 24, "columns": 80, "owner": None}
                # An app shows the tab: a pane's rows follow the layout, less a divider row.
                split = self.layer["layout"].get("split") or {}
                bar = (split.get("after") or {}).get("block_id")
                share = 1 - split.get("ratio", 0) if payload["block_id"] == bar else split.get("ratio", 1)
                rows = max(self.floor, round(share * self.tab_rows) - 1)
                return {"rows": rows, "columns": 100, "owner": "client:app"}
        raise AssertionError(f"unexpected rex call {args}")


class PaneTest(TempState):
    def setUp(self):
        super().setUp()
        self.fake = FakeRex()
        self.saved_rex, self.saved_sleep, self.saved_ps = rb.rex, rb.time.sleep, rb.process_args
        rb.rex = self.fake
        rb.time.sleep = lambda s: None
        rb.process_args = lambda pid: self.fake.args.get(pid, "")
        self.saved_owned_defaults = rb.owned_bar.__defaults__
        rb.owned_bar.__defaults__ = (None, 0)  # no retry delay in tests

    def tearDown(self):
        rb.rex, rb.time.sleep, rb.process_args = self.saved_rex, self.saved_sleep, self.saved_ps
        rb.owned_bar.__defaults__ = self.saved_owned_defaults
        super().tearDown()

    def cli(self, command):
        args = SimpleNamespace(command=command, session=SID, window="window:w", block=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rb.cmd_pane(args)
        return json.loads(buf.getvalue())

    def bars(self):
        return [b for b in self.fake.layer["blocks"] if b["label"] == rb.LABEL]

    def test_open_is_idempotent(self):
        first = self.cli("open")
        self.assertTrue(first["created"])
        split = [c for c in self.fake.calls if c[:3] == ("api", "call", "session.new_split")]
        payload = json.loads(split[0][3])
        self.assertEqual((payload["anchor_id"], payload["direction"], payload["side"], payload["focus"]),
                         ("split:root", "vertical", "after", False))
        self.assertIn("run", payload["layout"]["block"]["options"]["command"])
        self.assertAlmostEqual(1 - payload["ratio"], (rb.TARGET_ROWS + 1) / rb.DEFAULT_TAB_ROWS, places=3)
        second = self.cli("open")
        self.assertFalse(second["created"])
        self.assertEqual(second["pane"], first["pane"])
        self.assertEqual(len(self.bars()), 1)
        state = json.loads((self.dir / "bar" / "state.json").read_text())
        self.assertEqual(state["panes"], {"window:w": first["pane"]})

    def test_close_and_toggle(self):
        self.cli("open")
        self.assertTrue(self.cli("close")["closed"])
        self.assertEqual(self.bars(), [])
        self.assertFalse(self.cli("close")["closed"])
        self.assertEqual(self.cli("toggle")["action"], "open")
        self.assertEqual(len(self.bars()), 1)
        self.assertEqual(self.cli("toggle")["action"], "close")
        self.assertEqual(self.bars(), [])
        state = json.loads((self.dir / "bar" / "state.json").read_text())
        self.assertEqual(state["panes"], {})

    def state_panes(self):
        return json.loads((self.dir / "bar" / "state.json").read_text())["panes"]

    def test_single_block_root(self):
        self.fake.layer["layout"] = {"rect": [0, 0, 1, 1], "block_id": "block:a"}
        self.fake.layer["blocks"] = [{"block_id": "block:a", "label": "shell"}]
        self.cli("open")
        payload = json.loads([c for c in self.fake.calls if c[:3] == ("api", "call", "session.new_split")][0][3])
        self.assertEqual(payload["anchor_id"], "block:a")

    def test_foreign_bar_label_is_never_ours(self):
        # Someone else's pane labelled "Bar", running a job; no record of ours.
        self.fake.layer["blocks"][1]["label"] = rb.LABEL
        self.assertFalse(self.cli("close")["closed"])
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                self.cli("fit")
        self.assertEqual(self.cli("toggle")["action"], "open")  # opens a new bar
        out = self.cli("toggle")
        self.assertEqual(out["action"], "close")
        self.assertNotEqual(out["pane"], "block:b")
        self.assertNotIn("block:b", self.fake.closed)
        self.assertIn("block:b", {b["block_id"] for b in self.fake.layer["blocks"]})
        resizes = [json.loads(c[3]) for c in self.fake.calls if c[:3] == ("api", "call", "session.resize_pane")]
        self.assertTrue(all(r["after_block_id"] != "block:b" for r in resizes))

    def test_lost_record_opens_a_new_bar(self):
        first = self.cli("open")
        (self.dir / "bar" / "state.json").write_text("{}")
        again = self.cli("open")
        self.assertTrue(again["created"])
        self.assertNotEqual(again["pane"], first["pane"])

    def test_record_pointing_at_a_foreign_pane(self):
        # A stale record names block:b, which runs a shell, not rexbar.
        (self.dir / "bar").mkdir()
        (self.dir / "bar" / "state.json").write_text(json.dumps({"panes": {"window:w": "block:b"}}))
        self.assertFalse(self.cli("close")["closed"])
        self.assertEqual(self.fake.closed, [])
        self.assertEqual(self.state_panes(), {})
        (self.dir / "bar" / "state.json").write_text(json.dumps({"panes": {"window:w": "block:b"}}))
        out = self.cli("open")
        self.assertTrue(out["created"])
        self.assertEqual(self.state_panes(), {"window:w": out["pane"]})

    def test_rexbar_for_another_session_is_not_ours(self):
        out = self.cli("open")
        pid = self.fake.procs[out["pane"]]["pid"]
        self.fake.args[pid] = self.fake.args[pid].replace(SID, "session:01a1fabd-0000-7000-8000-00000000ffff")
        self.assertFalse(rb.runs_rexbar(SID, out["pane"]))
        self.fake.args[pid] = "python /x/rexbar once --session " + SID
        self.assertFalse(rb.runs_rexbar(SID, out["pane"]))

    def cli_fails(self, command):
        err = io.StringIO()
        import contextlib
        with self.assertRaises(SystemExit) as cm, redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.cli(command)
        return cm.exception.code, err.getvalue()

    def test_one_transient_failure_is_retried(self):
        out = self.cli("open")
        self.fake.process_failures = 1
        again = self.cli("open")
        self.assertEqual((again["pane"], again["created"]), (out["pane"], False))

    def test_unknown_keeps_record_and_changes_nothing(self):
        out = self.cli("open")
        bars = len(self.bars())
        for command in ("open", "toggle", "close", "fit"):
            self.fake.process_failures = 2
            self.fake.calls.clear()
            code, err = self.cli_fails(command)
            self.assertEqual(code, rb.EX_TEMPFAIL, command)
            self.assertIn("try again", err)
            changing = [c for c in self.fake.calls if c[:2] == ("block", "close")
                        or c[:3] in (("api", "call", "session.new_split"), ("api", "call", "session.resize_pane"))]
            self.assertEqual(changing, [], command)
            self.assertEqual(self.state_panes(), {"window:w": out["pane"]})
            self.assertEqual(len(self.bars()), bars)

    def test_ps_failure_is_unknown(self):
        out = self.cli("open")
        rb.process_args = lambda pid: None
        self.assertIsNone(rb.runs_rexbar(SID, out["pane"]))
        code, _ = self.cli_fails("toggle")
        self.assertEqual(code, rb.EX_TEMPFAIL)
        self.assertEqual(self.state_panes(), {"window:w": out["pane"]})

    def test_exited_program_is_a_confirmed_mismatch(self):
        out = self.cli("open")
        self.fake.procs[out["pane"]] = None  # Rex answered: nothing runs in the pane
        self.assertIs(rb.runs_rexbar(SID, out["pane"]), False)
        self.assertEqual(self.cli("toggle")["action"], "open")

    def test_dead_bar_is_dropped_from_state(self):
        out = self.cli("open")
        self.fake.procs[out["pane"]] = {"pid": 7, "script": None}  # rexbar exited, a shell is left
        self.assertFalse(self.cli("close")["closed"])
        self.assertNotIn(out["pane"], self.fake.closed)
        self.assertEqual(self.state_panes(), {})
        # A bar whose block is gone entirely is dropped too.
        out = self.cli("open")
        self.fake.layer["blocks"] = [b for b in self.fake.layer["blocks"] if b["block_id"] != out["pane"]]
        self.assertEqual(self.cli("toggle")["action"], "open")
        self.assertNotIn(out["pane"], self.state_panes().values())

    def test_fit_resizes_bottom_border(self):
        self.cli("open")
        self.fake.calls.clear()
        out = self.cli("fit")
        resize = [json.loads(c[3]) for c in self.fake.calls if c[:3] == ("api", "call", "session.resize_pane")]
        self.assertEqual(len(resize), 1)
        self.assertEqual((resize[0]["direction"], resize[0]["before_block_id"], resize[0]["after_block_id"]),
                         ("vertical", "block:a", out["block"]))

    def test_fit_with_app_sizes_reaches_two_rows(self):
        for rows in (24, 36, 50, 80):
            with self.subTest(tab_rows=rows):
                self.fake = FakeRex()
                rb.rex = self.fake
                self.fake.tab_rows = rows
                state = self.dir / "bar" / "state.json"
                if state.exists():
                    state.unlink()
                out = self.cli("open")
                self.assertEqual(out["fit"]["rows"], rb.TARGET_ROWS, out)
                self.assertTrue(out["fit"]["sized_by_app"])

    def test_fit_stops_at_the_apps_floor(self):
        self.fake.tab_rows = 36
        bar = self.cli("open")["pane"]
        self.fake.calls.clear()
        out = rb.fit(SID, "window:w", bar, target=2, settle=0, tries=5)
        resizes = [c for c in self.fake.calls if c[:3] == ("api", "call", "session.resize_pane")]
        self.assertEqual(out["rows"], 3)
        self.assertLessEqual(len(resizes), 2)

    def test_bad_session_refused(self):
        args = SimpleNamespace(command="open", session="../../x", window="window:w", block=None)
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                rb.cmd_pane(args)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
