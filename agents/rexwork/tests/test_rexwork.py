import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
_loader = SourceFileLoader("rexwork", str(HERE.parent / "rexwork"))
rw = importlib.util.module_from_spec(importlib.util.spec_from_loader("rexwork", _loader))
_loader.exec_module(rw)

UUID = "01a1fabd-0000-7000-8000-000000000001"
SID = "session:" + UUID

# Never read Tal's real config, run the real rexbar or the real treehouse.
os.environ["REXWORK_CONFIG"] = str(HERE / "no-such-config.json")
os.environ["REXWORK_TREEHOUSE"] = "/nonexistent/treehouse"
REAL_OPEN_BAR = rw.open_bar
rw.open_bar = lambda session, window: {"pane": "block:bar"}


class Names(unittest.TestCase):
    def test_normalize_ticket(self):
        self.assertEqual(rw.normalize_ticket("tal-012"), "TAL-12")
        self.assertEqual(rw.normalize_ticket("123"), "TAL-123")
        self.assertEqual(rw.normalize_ticket("TAL-7"), "TAL-7")
        self.assertIsNone(rw.normalize_ticket("fix-login"))
        self.assertIsNone(rw.normalize_ticket(""))

    def test_ticket_of_label(self):
        self.assertEqual(rw.ticket_of_label("TAL-201 Manual banking annotation"), "TAL-201")
        self.assertEqual(rw.ticket_of_label("TAL-201"), "TAL-201")
        self.assertIsNone(rw.ticket_of_label("dawn-grotto"))
        self.assertIsNone(rw.ticket_of_label("symphony"))

    def test_short_name(self):
        self.assertEqual(rw.short_name("Manual banking annotation"), "Manual banking annotation")
        self.assertEqual(
            rw.short_name("Train Gemma/APT on six-search Opus Banking goldens and evaluate along the curve"),
            "Train Gemma/APT on six-search")
        self.assertEqual(rw.short_name("[Hydra] Banking exp4: larger golden corpus to find the ceiling"),
                         "Banking exp4: larger golden")
        self.assertEqual(rw.short_name("Fix: "), "Fix")
        self.assertEqual(rw.short_name("Supercalifragilisticexpialidocious-and-more-words-here"),
                         "Supercalifragilisticexpialidocious-and-more-words-here")
        self.assertEqual(rw.short_name(""), "")

    def test_label(self):
        self.assertEqual(rw.ticket_label("TAL-9", "Fix login redirect loop"), "TAL-9 Fix login redirect loop")
        self.assertEqual(rw.ticket_label("TAL-9", ""), "TAL-9")

    def test_slug_and_branch(self):
        self.assertEqual(rw.slugify("Train Gemma/APT on six-search!"), "train-gemma-apt-on-six-search")
        self.assertEqual(rw.slugify("a" * 50), "a" * 40)
        self.assertEqual(rw.slugify("one two three four five six seven eight nine ten", 20), "one-two-three-four")
        self.assertEqual(rw.branch_name("TAL-123", "Fix login: redirect loop"),
                         "u/tfriedman/tal-123-fix-login-redirect-loop")
        self.assertEqual(rw.branch_name("TAL-5", "***"), "u/tfriedman/tal-5")

    def test_generated(self):
        self.assertTrue(rw.is_generated_label("dawn-grotto"))
        self.assertFalse(rw.is_generated_label("TAL-12 Fix"))
        self.assertFalse(rw.is_generated_label("symphony"))
        self.assertFalse(rw.is_generated_label("fix-login-redirect"))


class Drafts(unittest.TestCase):
    def test_parse(self):
        title, body = rw.parse_draft("\n# Fix the login loop\n\nUsers bounce.\n\n## Done when\n- ok\n")
        self.assertEqual(title, "Fix the login loop")
        self.assertEqual(body, "Users bounce.\n\n## Done when\n- ok\n")

    def test_strips_comments(self):
        title, body = rw.parse_draft("# T1\n\nBody\n<!-- objectives:\n- todo: x\n-->\n")
        self.assertEqual(title, "T1")
        self.assertEqual(body, "Body\n")

    def test_template_round_trip_rejected(self):
        with self.assertRaises(ValueError):
            rw.parse_draft("# Title\n\nx\n")

    def test_errors(self):
        for bad in ["", "No heading\n\nbody", "## Sub heading\n\nbody", "# Only a title\n\n"]:
            with self.assertRaises(ValueError):
                rw.parse_draft(bad)

    def test_template_has_objectives_and_parses_after_edit(self):
        text = rw.draft_template("# Objectives\n- doing: thing -->")
        self.assertIn("doing: thing", text)
        edited = text.replace("# Title: what this ticket delivers, in a few words", "# Real title")
        title, body = rw.parse_draft(edited)
        self.assertEqual(title, "Real title")
        self.assertNotIn("doing: thing", body)
        self.assertIn("## Done when", body)


class State(unittest.TestCase):
    def test_merge(self):
        old = {"last_reviewed": {"/r": "abc"}, "label": "a", "ticket": "TAL-1"}
        new = rw.merge_state(old, {"label": "b", "ticket": None, "kind": "scratch"})
        self.assertEqual(new, {"last_reviewed": {"/r": "abc"}, "label": "b", "kind": "scratch"})
        self.assertEqual(old["label"], "a")

    def test_update_keeps_unknown_keys(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "s"
            d.mkdir()
            (d / "session.json").write_text(json.dumps({"last_reviewed": {"/r": "abc"}}))
            rw.update_state(d, label="x", kind="scratch")
            data = json.loads((d / "session.json").read_text())
            self.assertEqual(data, {"last_reviewed": {"/r": "abc"}, "label": "x", "kind": "scratch"})
            self.assertEqual([p.name for p in d.iterdir() if p.name.endswith(".tmp")], [])

    def test_bad_json_is_left_alone(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "session.json").write_text("[1, 2]")
            with self.assertRaises(rw.Failure):
                rw.update_state(d, label="x")
            self.assertEqual((d / "session.json").read_text(), "[1, 2]")

    def test_objectives_written_once(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            rw.write_objectives(d, "TAL-1 X", "TAL-1", "https://linear.app/x")
            self.assertIn("Ticket: TAL-1 (https://linear.app/x)", (d / "objectives.md").read_text())
            (d / "objectives.md").write_text("mine")
            rw.write_objectives(d, "other")
            self.assertEqual((d / "objectives.md").read_text(), "mine")


class Commands(unittest.TestCase):
    def test_agent_command(self):
        os.environ.pop("REXWORK_AGENT_STUB", None)
        argv = rw.agent_command("claude", "see @STATE_DIR@/x")
        self.assertEqual(argv[-1], "claude")
        self.assertIn("REXWORK_PROMPT=see @STATE_DIR@/x", argv)
        # The wrapper fills in the state dir from REX_SESSION and passes the prompt last.
        import subprocess
        script = argv[argv.index("-c") + 1]
        out = subprocess.run(["/bin/sh", "-c", script.replace('exec "${SHELL:-/bin/zsh}" -l', "true"),
                              "sh", "printf", "%s"],
                             env={"REXWORK_PROMPT": "see @STATE_DIR@/x", "REX_SESSION": "session:abc",
                                  "HOME": "/h", "PATH": os.environ["PATH"]},
                             capture_output=True, text=True)
        self.assertEqual(out.stdout, "see /h/.local/state/rexwork/sessions/abc/x")
        self.assertEqual(rw.agent_command("codex")[1:3], ["-u", "REXWORK_PROMPT"])

    def test_reuse_tab_leaves_existing_tabs_alone(self):
        calls, emitted = [], []
        saved = {k: getattr(rw, k) for k in ("SESSIONS", "rex", "open_show", "live_sessions", "emit", "note")}
        with tempfile.TemporaryDirectory() as t:
            try:
                rw.SESSIONS = Path(t)
                live = {SID: {"session_id": SID, "label": "TAL-9 Hand made"}}
                rw.live_sessions = lambda: live

                def fake_rex(*args, **kw):
                    calls.append(args)
                    return {"block_ids": ["block:new"], "window_id": "window:new"}
                rw.rex = fake_rex
                rw.open_show = lambda s, b: calls.append(("show", s, b)) or {"pane": "block:show2"}
                rw.emit, rw.note = emitted.append, lambda m: None
                (Path(t) / UUID).mkdir()
                before = json.dumps({"main_block": "block:tal", "show_block": "block:show1"})
                (Path(t) / UUID / "session.json").write_text(before)
                rc = rw.main(["start", "TAL-9", "--title", "X", "--no-linear", "--cwd", t,
                              "--owner", "symphony", "--reuse-tab", "symphony", "--", "codex", "resume", "th"])
                self.assertEqual(rc, 0)
                self.assertEqual(calls[0][:5], ("window", "new", "-s", SID, "symphony"))
                self.assertIn("--focus=false", calls[0])
                self.assertEqual(calls[0][-3:], ("codex", "resume", "th"))
                self.assertEqual(calls[1], ("show", SID, "block:new"))
                self.assertEqual(len(calls), 2)
                out = emitted[0]
                self.assertTrue(out["reused"])
                self.assertEqual(out["tab"], {"window_id": "window:new", "main_block": "block:new",
                                              "show_block": "block:show2", "bar_block": "block:bar"})
                # --reuse-tab never writes the host session's state.
                self.assertEqual((Path(t) / UUID / "session.json").read_text(), before)
                self.assertEqual(sorted(p.name for p in (Path(t) / UUID).iterdir()), ["session.json"])
                self.assertEqual(out["ticket"], "TAL-9")
                self.assertEqual(out["warnings"], [])
            finally:
                for k, v in saved.items():
                    setattr(rw, k, v)

    def test_token(self):
        self.assertEqual(rw.token_hash("a"), rw.token_hash("a"))
        self.assertNotEqual(rw.token_hash("a"), rw.token_hash("b"))


@contextlib.contextmanager
def patched(**attrs):
    saved = {k: getattr(rw, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(rw, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(rw, k, v)


@contextlib.contextmanager
def sandbox():
    """A temporary SESSIONS/ARCHIVE, quiet output, and a non-tty stdin."""
    with tempfile.TemporaryDirectory() as t:
        emitted, notes = [], []
        root = Path(t)
        old_stdin = rw.sys.stdin
        rw.sys.stdin = io.StringIO("")
        try:
            with patched(SESSIONS=root / "sessions", ARCHIVE=root / "archive", emit=emitted.append,
                         note=notes.append):
                (root / "sessions").mkdir()
                yield root, emitted, notes
        finally:
            rw.sys.stdin = old_stdin


def make_state(root, data, uuid=UUID):
    d = root / "sessions" / uuid
    d.mkdir(parents=True, exist_ok=True)
    (d / "session.json").write_text(json.dumps(data))
    return d


class SessionIds(unittest.TestCase):
    def test_uuid_of(self):
        self.assertEqual(rw.uuid_of(SID), UUID)
        self.assertEqual(rw.uuid_of(UUID), UUID)
        self.assertEqual(rw.session_id(UUID), SID)
        for bad in ["../../x", "session:../../x", "/etc", "session:/tmp/x", "abc", "", "a" * 8 + "/..",
                    "session:", "0123456789abcdef/../x", "x" * 10]:
            with self.assertRaises(rw.Failure, msg=bad):
                rw.uuid_of(bad)

    def test_state_dir_is_direct_child(self):
        with sandbox() as (root, _, _):
            self.assertEqual(rw.state_dir(SID), root / "sessions" / UUID)
            # A child that is a link to somewhere else is refused.
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (root / "sessions" / UUID).symlink_to(elsewhere)
            with self.assertRaises(rw.Failure):
                rw.state_dir(SID)
            with self.assertRaises(rw.Failure):
                rw.archive(root / "sessions" / UUID)
            with self.assertRaises(rw.Failure):
                rw.archive(elsewhere)

    def test_crafted_session_touches_nothing(self):
        with sandbox() as (root, _, notes):
            victim = root / "victim"
            victim.mkdir()
            (victim / "session.json").write_text(json.dumps({"kind": "scratch", "label": "x"}))
            with patched(live_sessions=lambda: {}, rex=lambda *a, **k: self.fail("rex called")):
                for cmd in (["land", "--yes"], ["land", "--keep"], ["status"], ["promote", "--file", "--stdin"]):
                    for bad in ("../../victim", str(victim), "session:../victim"):
                        self.assertEqual(rw.main([*cmd, "--session", bad]), 1)
            self.assertTrue((victim / "session.json").exists())
            self.assertFalse((root / "archive").exists())
            self.assertIn("not a Rex session id", notes[-1])


class Boundaries(unittest.TestCase):
    def test_branch(self):
        m = rw.branch_matches_ticket
        self.assertTrue(m("u/tfriedman/tal-1", "TAL-1"))
        self.assertTrue(m("u/tfriedman/tal-1-fix-login", "TAL-1"))
        self.assertFalse(m("u/tfriedman/tal-10-fix", "TAL-1"))
        self.assertFalse(m("u/tfriedman/tal-10", "TAL-1"))
        self.assertFalse(m("u/someone/tal-1", "TAL-1"))
        self.assertFalse(m(None, "TAL-1"))
        self.assertFalse(m("u/tfriedman/tal-1", None))

    def test_label(self):
        self.assertTrue(rw.label_matches_ticket("TAL-1 Fix", "TAL-1"))
        self.assertTrue(rw.label_matches_ticket("TAL-1", "TAL-1"))
        self.assertFalse(rw.label_matches_ticket("TAL-10 Fix", "TAL-1"))
        self.assertFalse(rw.label_matches_ticket("TAL-10", "TAL-1"))

    def test_find_ticket_session_by_label(self):
        other = "01a1fabd-0000-7000-8000-000000000010"
        live = {"session:" + other: {"session_id": "session:" + other, "label": "TAL-10 Other"},
                "session:bad/../x": {"session_id": "session:bad/../x", "label": "TAL-1 crafted"}}
        with sandbox(), patched(live_sessions=lambda: live):
            self.assertIsNone(rw.find_ticket_session("TAL-1"))
            self.assertEqual(rw.find_ticket_session("TAL-10"), "session:" + other)

    def test_ticket_change_clears_old_fields(self):
        state = {"ticket": "TAL-1", "branch": "u/tfriedman/tal-1-x", "worktree": "/w", "repo": "/r", "title": "T"}
        cleared = rw.ticket_change(state, "TAL-2")
        merged = rw.merge_state(state, {**cleared, "ticket": "TAL-2"})
        self.assertEqual(merged, {"ticket": "TAL-2"})
        self.assertEqual(rw.ticket_change(state, "TAL-1"), {})
        self.assertEqual(rw.ticket_change({}, "TAL-1"), {})

    def test_split_command(self):
        self.assertEqual(rw.split_command(["start", "TAL-1", "--title", "--", "--", "sh", "-c", "a -- b"]),
                         (["start", "TAL-1", "--title=--"], ["sh", "-c", "a -- b"]))
        self.assertEqual(rw.split_command(["start", "TAL-1"]), (["start", "TAL-1"], []))
        # Titles that look like options still parse.
        with patched(start_ticket=lambda a: setattr(self, "seen", (a.title, a.command))):
            self.assertEqual(rw.main(["start", "TAL-1", "--title", "- fix", "--", "x", "--title", "y"]), 0)
        self.assertEqual(self.seen, ("- fix", ["x", "--title", "y"]))

    def test_names(self):
        self.assertEqual(rw.check_name("  a  b "), "a b")
        for bad in ["", "  ", "-x", "--focus"]:
            with self.assertRaises(rw.Failure):
                rw.check_name(bad)


DRAFT = "# Fix the login loop\n\nUsers bounce between pages.\n\n## Done when\n- ok\n"


class Filing(unittest.TestCase):
    """promote --file --stdin with a stubbed Linear."""

    def setUp(self):
        self.creates = []
        self.ctx = contextlib.ExitStack()
        self.root, self.emitted, self.notes = self.ctx.enter_context(sandbox())
        self.dir = make_state(self.root, {"kind": "scratch", "label": "x", "window_id": "window:w"})

        def create(payload):
            self.creates.append(payload)
            time.sleep(0.05)
            return {"id": payload["id"], "identifier": "TAL-77", "title": payload["title"], "url": "https://l/77"}
        self.found = None
        self.ctx.enter_context(patched(
            build_issue_input=lambda t, d: ({"teamId": "t", "title": t, "description": d, "assigneeId": "me",
                                             "stateId": "s"}, {"viewer": {"name": "Tal"}}),
            create_issue=create, find_filed_issue=lambda f: self.found,
            rex=lambda *a, **k: {}, live_sessions=lambda: {}))

    def tearDown(self):
        self.ctx.close()

    def authorize(self, dry_run=False, token="tok"):
        (self.dir / "promote.json").write_text(json.dumps(
            {"token_sha256": rw.token_hash(token), "created_at": int(time.time()), "dry_run": dry_run,
             "repo": None, "detected_repo": None}))

    def file(self, content=DRAFT, digest=None, token="tok", dry_run=False, flag_dry=None):
        req = {"token": token, "content": content, "dry_run": dry_run,
               "digest": digest or hashlib.sha256(content.encode()).hexdigest()}
        argv = ["promote", "--file", "--stdin", "--session", SID]
        if dry_run if flag_dry is None else flag_dry:
            argv.append("--dry-run")
        with patched(read_json_stdin=lambda: dict(req)):
            return rw.main(argv)

    def state(self):
        return json.loads((self.dir / "session.json").read_text())

    def test_digest_binding(self):
        self.authorize()
        # Content changed after Tal confirmed: refused, authorization kept.
        self.assertEqual(self.file(digest=hashlib.sha256(b"other").hexdigest()), 1)
        self.assertIn("does not match the digest", self.notes[-1])
        self.assertEqual(self.creates, [])
        self.assertTrue((self.dir / "promote.json").exists())
        # The draft on disk is never read: a rewrite there changes nothing.
        (self.dir / "ticket-draft.md").write_text("# Evil\n\nrewritten\n")
        self.assertEqual(self.file(), 0)
        self.assertEqual(self.creates[0]["title"], "Fix the login loop")
        self.assertEqual((self.dir / "ticket-filed.md").read_text(), DRAFT)

    def test_bad_token_and_mode(self):
        self.authorize(dry_run=True)
        self.assertEqual(self.file(token="nope", dry_run=True), 1)
        self.assertIn("token does not match", self.notes[-1])
        self.assertEqual(self.file(dry_run=False), 1)  # real request, dry-run authorization
        self.assertIn("mode mismatch", self.notes[-1])
        self.assertEqual(self.file(dry_run=True, flag_dry=False), 1)
        self.assertIn("mode mismatch", self.notes[-1])
        self.authorize(dry_run=False)
        self.assertEqual(self.file(dry_run=True), 1)
        self.assertEqual(self.creates, [])

    def test_dry_run_consumes_and_writes_nothing(self):
        self.authorize(dry_run=True)
        self.assertEqual(self.file(dry_run=True), 0)
        self.assertEqual(self.creates, [])
        self.assertTrue(self.emitted[-1]["dry_run"])
        self.assertEqual(self.emitted[-1]["would_create"]["title"], "Fix the login loop")
        self.assertIn("id", self.emitted[-1]["would_create"])
        self.assertFalse((self.dir / "promote.json").exists())
        self.assertNotIn("filing", self.state())
        self.assertEqual(self.state()["kind"], "scratch")
        self.assertEqual(self.file(dry_run=True), 1)  # the same authorization again
        self.assertIn("no open authorization", self.notes[-1])

    def test_no_confirmation_refused(self):
        self.assertEqual(rw.main(["promote", "--file", "--session", SID]), 1)
        self.assertIn("refusing to file", self.notes[-1])

    def test_one_confirmation_one_issue(self):
        self.authorize()
        self.assertEqual(self.file(), 0)
        self.assertEqual(len(self.creates), 1)
        st = self.state()
        self.assertEqual((st["kind"], st["ticket"]), ("ticketed", "TAL-77"))
        self.assertEqual(st["filing"]["ticket"], "TAL-77")
        self.assertEqual(st["filing"]["issue_uuid"], self.creates[0]["id"])
        self.assertEqual(self.file(), 1)
        self.assertEqual(len(self.creates), 1)

    def test_overlapping_files(self):
        self.authorize()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.file())) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.creates), 1)
        self.assertEqual(sorted(results), [0, 1, 1, 1])

    def test_timeout_then_retry_never_creates_twice(self):
        self.authorize()

        def timeout(payload):
            self.creates.append(payload)
            raise rw.Failure("Linear: timed out")
        with patched(create_issue=timeout):
            self.assertEqual(self.file(), 1)
        self.assertIn("filing record stays", self.notes[-1])
        filing = self.state()["filing"]
        self.assertFalse((self.dir / "promote.json").exists())
        # A new confirmation later: Linear has nothing, so nothing is created; Tal is told how to clear.
        self.authorize()
        self.assertEqual(self.file(), 1)
        self.assertIn("--clear-filing", self.notes[-1])
        self.assertEqual(len(self.creates), 1)
        # Linear did accept it: the issue is adopted, not created again.
        self.found = {"id": filing["issue_uuid"], "identifier": "TAL-78", "title": "Fix the login loop",
                      "url": "https://l/78"}
        self.assertEqual(self.file(), 0)
        self.assertEqual(len(self.creates), 1)
        self.assertEqual(self.state()["ticket"], "TAL-78")
        self.assertTrue(self.emitted[-1]["adopted"])

    def test_created_but_unfinished_is_finished_not_recreated(self):
        st = self.state()
        st["filing"] = {"at": 1, "title": "T", "digest": "d", "issue_uuid": "u", "dry_run": False,
                        "ticket": "TAL-79", "issue_id": "u"}
        (self.dir / "session.json").write_text(json.dumps(st))
        self.authorize()
        with patched(get_issue=lambda t: {"id": "u", "identifier": "TAL-79", "title": "T", "url": "https://l/79"}):
            self.assertEqual(self.file(), 0)
        self.assertEqual(self.creates, [])
        self.assertEqual(self.state()["ticket"], "TAL-79")

    def test_preview(self):
        self.authorize(dry_run=True)
        with patched(read_json_stdin=lambda: {"content": DRAFT}):
            self.assertEqual(rw.main(["promote", "--preview", "--session", SID]), 0)
        out = self.emitted[-1]
        self.assertEqual(out["title"], "Fix the login loop")
        self.assertEqual(out["digest"], hashlib.sha256(DRAFT.encode()).hexdigest())
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["description_head"][0], "Users bounce between pages.")


HEAD = "c0ffee" * 6 + "abcd"
PUSH = ("git", "push", "origin", f"{HEAD}:refs/heads/u/tfriedman/tal-1-x")


class Land(unittest.TestCase):
    """The confirmation matrix, with Rex, git and Linear stubbed."""

    def setUp(self):
        self.calls = []
        self.ctx = contextlib.ExitStack()
        self.root, self.emitted, self.notes = self.ctx.enter_context(sandbox())
        self.ctx.enter_context(patched(
            live_sessions=lambda: {SID: {"session_id": SID}},
            rex=lambda *a, **k: self.calls.append(("rex",) + a) or {},
            get_issue=lambda t: self.calls.append(("get_issue", t)) or {"id": "i", "identifier": t,
                                                                         "team": {"key": "TAL"}},
            set_issue_state=lambda i, n, info=None: self.calls.append(("state", n)),
            comment_issue=lambda i, b: self.calls.append(("comment", b)),
            verify_branch=lambda *a: self.calls.append(("verify",)),
            git=lambda *a, **k: "origin" if a[0] == "remote" else (HEAD if a[0] == "rev-parse" else ""),
            run=lambda args, **k: self.calls.append(tuple(args)) or
            type("R", (), {"returncode": 0, "stdout": "https://pr/1\n", "stderr": ""})(),
            open_pr=lambda w, b: "https://pr/1"))
        os.environ.pop("REX_SESSION", None)

    def tearDown(self):
        self.ctx.close()

    def land(self, *flags):
        return rw.main(["land", "--session", SID, *flags])

    def scratch(self):
        return make_state(self.root, {"kind": "scratch", "label": "s"})

    def ticketed(self, **extra):
        return make_state(self.root, {"kind": "ticketed", "label": "TAL-1 X", "ticket": "TAL-1", **extra})

    def test_scratch_keep_is_a_noop(self):
        d = self.scratch()
        self.assertEqual(self.land("--keep"), 0)
        self.assertTrue(d.exists())
        self.assertTrue(self.emitted[-1]["noop"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.land("--keep", "--yes"), 0)
        self.assertTrue(d.exists())

    def test_scratch_needs_confirmation(self):
        d = self.scratch()
        self.assertEqual(self.land(), 1)
        self.assertTrue(d.exists())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.land("--yes"), 0)
        self.assertFalse(d.exists())
        self.assertTrue((self.root / "archive" / UUID).exists())
        self.assertIn(("rex", "kill", SID), self.calls)

    def test_ticketed_keep_still_needs_confirmation(self):
        self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        self.assertEqual(self.land("--keep"), 1)
        self.assertIn("pass --yes", self.notes[-1])
        self.assertEqual([c for c in self.calls if c[0] != "verify"], [])
        self.assertEqual(self.land("--keep", "--yes"), 0)
        self.assertIn(PUSH, self.calls)
        self.assertIn(("state", "In Review"), self.calls)
        self.assertNotIn(("rex", "kill", SID), self.calls)
        # verify_branch runs at planning time and again just before the push
        self.assertEqual(self.calls.count(("verify",)), 2 + 1)

    def test_no_branch_needs_linear_only(self):
        self.ticketed()
        self.assertEqual(self.land("--yes"), 1)
        self.assertIn("--linear-only", self.notes[-1])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.land("--dry-run"), 0)
        self.assertTrue(self.emitted[-1]["no_branch"])
        self.assertTrue(any("no branch or PR" in p for p in self.emitted[-1]["plan"]))
        self.assertEqual(self.land("--linear-only"), 1)  # still needs --yes
        self.assertEqual(self.calls, [])
        self.assertEqual(self.land("--linear-only", "--yes", "--keep"), 0)
        self.assertEqual(self.calls, [("get_issue", "TAL-1"), ("state", "In Review")])

    def test_dry_run_changes_nothing(self):
        d = self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        self.assertEqual(self.land("--dry-run"), 0)
        self.assertEqual([c for c in self.calls if c[0] != "verify"], [])
        self.assertNotIn("landed_at", json.loads((d / "session.json").read_text()))

    def test_symphony_sessions_are_refused(self):
        self.ticketed(owner="symphony", worktree="/w", branch="u/tfriedman/tal-1-x")
        for flags in (["--yes"], ["--dry-run"], ["--keep", "--yes"]):
            self.assertEqual(self.land(*flags), 1)
            self.assertIn("owned by Symphony", self.notes[-1])
        self.assertEqual(self.calls, [])


class VerifyBranch(unittest.TestCase):
    def test_wrong_ticket_branch_refused(self):
        with self.assertRaises(rw.Failure):
            rw.verify_branch({}, "TAL-1", "/nonexistent", "u/tfriedman/tal-10-fix")



class TTY(io.StringIO):
    def isatty(self):
        return True


@contextlib.contextmanager
def terminal(answer="yes\n"):
    """A tty stdin that answers ANSWER, and a tty stderr."""
    old_in, old_err = rw.sys.stdin, rw.sys.stderr
    rw.sys.stdin, rw.sys.stderr = TTY(answer), TTY()
    try:
        yield rw.sys.stderr
    finally:
        rw.sys.stdin, rw.sys.stderr = old_in, old_err


OTHER = "01a1fabd-0000-7000-8000-000000000002"


class SymlinkedId(unittest.TestCase):
    """G: a link from id A to sibling B passes the parent check but not the name check."""

    def test_sibling_link_refused(self):
        with sandbox() as (root, _, _):
            b = make_state(root, {"kind": "scratch", "label": "b"}, uuid=OTHER)
            (root / "sessions" / UUID).symlink_to(b)
            with self.assertRaises(rw.Failure):
                rw.state_dir(SID)
            with self.assertRaises(rw.Failure):
                rw.archive(root / "sessions" / UUID)
            self.assertEqual(rw.state_dir("session:" + OTHER), root / "sessions" / OTHER)
            with patched(live_sessions=lambda: {}):
                self.assertEqual(rw.main(["land", "--yes", "--session", SID]), 1)
            self.assertTrue((b / "session.json").exists())


class FindFiledIssue(unittest.TestCase):
    """A: reconcile only by the recorded issue id, never by title."""

    def test_uuid_missing_is_none_and_no_title_search(self):
        queries = []

        def linear(q, v=None):
            queries.append((q, v))
            if "issues(" in q:  # a title search would find Tal's own same-title issue
                return {"issues": {"nodes": [{"id": "other", "identifier": "TAL-5", "title": "T"}]}}
            raise rw.Failure("Linear: Entity not found: Issue")
        with patched(linear=linear):
            self.assertIsNone(rw.find_filed_issue({"issue_uuid": "u-1", "title": "T", "at": 1}))
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0][1], {"id": "u-1"})

    def test_found_by_uuid(self):
        with patched(linear=lambda q, v=None: {"issue": {"id": "u-1", "identifier": "TAL-6"}}):
            self.assertEqual(rw.find_filed_issue({"issue_uuid": "u-1"})["identifier"], "TAL-6")
        # A different id back from Linear is not that attempt's issue.
        with patched(linear=lambda q, v=None: {"issue": {"id": "zzz", "identifier": "TAL-6"}}):
            self.assertIsNone(rw.find_filed_issue({"issue_uuid": "u-1"}))

    def test_no_uuid_refuses(self):
        with patched(linear=lambda *a, **k: self.fail("queried Linear")):
            with self.assertRaises(rw.Failure) as e:
                rw.find_filed_issue({"title": "T", "at": 1})
        self.assertIn("--clear-filing", str(e.exception))


class FilingRecovery(Filing):
    """B and C, on top of Filing's stubs."""

    def setUp(self):
        super().setUp()
        self.rex_calls, self.trees, self.lookups = [], [], []
        self.rex_fail = False

        def rex(*a, **k):
            self.rex_calls.append(a)
            if self.rex_fail:
                raise rw.Failure("rex session rename: boom")
            return {}

        def find(f):
            self.lookups.append(f)
            return self.found
        self.ctx.enter_context(patched(
            rex=rex, find_filed_issue=find,
            ensure_worktree=lambda *a: self.trees.append(a) or self.fail("worktree created"),
            get_issue=lambda t: {"id": "u", "identifier": "TAL-80", "title": "Fix the login loop",
                                 "url": "https://l/80"}))

    def pend(self, **extra):
        st = self.state()
        st["filing"] = {"at": 1, "title": "Fix the login loop", "digest": "d", "issue_uuid": "uuid-1",
                        "dry_run": False, "repo": None, "detected_repo": None, **extra}
        (self.dir / "session.json").write_text(json.dumps(st))
        return (self.dir / "session.json").read_bytes()

    def test_dry_run_with_pending_filing_writes_nothing(self):
        before = self.pend()
        self.found = {"id": "uuid-1", "identifier": "TAL-80", "title": "Fix the login loop", "url": "u"}
        # Bad token: refused before anything else.
        self.authorize(dry_run=True)
        self.assertEqual(self.file(token="bad", dry_run=True), 1)
        self.assertIn("token does not match", self.notes[-1])
        # Mode mismatch: refused too.
        self.assertEqual(self.file(dry_run=False), 1)
        self.assertIn("mode mismatch", self.notes[-1])
        # A valid dry run: reports the pending attempt, recovers nothing, writes nothing.
        self.assertEqual(self.file(dry_run=True), 0)
        self.assertIn("earlier filing attempt", self.emitted[-1]["pending"])
        self.assertIsNone(self.emitted[-1]["would_create"])
        self.assertEqual((self.dir / "session.json").read_bytes(), before)
        self.assertEqual((self.rex_calls, self.trees, self.lookups, self.creates), ([], [], [], []))
        self.assertFalse((self.dir / "ticket-filed.md").exists())

    def test_plain_dry_run_writes_no_state(self):
        before = (self.dir / "session.json").read_bytes()
        self.authorize(dry_run=True)
        self.assertEqual(self.file(dry_run=True), 0)
        self.assertEqual((self.dir / "session.json").read_bytes(), before)
        self.assertFalse((self.dir / "ticket-filed.md").exists())
        self.assertEqual(self.rex_calls, [])

    def test_real_confirmed_attempt_reconciles_by_uuid(self):
        self.pend()
        self.authorize()
        self.assertEqual(self.file(), 1)  # Linear has no issue with that id
        self.assertIn("no issue with id uuid-1", self.notes[-1])
        self.assertIn("--clear-filing", self.notes[-1])
        self.assertEqual(self.rex_calls, [])
        self.assertTrue((self.dir / "promote.json").exists())  # not used up by a failed reconcile
        self.found = {"id": "uuid-1", "identifier": "TAL-80", "title": "Fix the login loop", "url": "u"}
        self.assertEqual(self.file(), 0)
        self.assertEqual(self.creates, [])
        self.assertEqual(self.state()["ticket"], "TAL-80")
        self.assertFalse((self.dir / "promote.json").exists())

    def test_recover_entry_point(self):
        before = self.pend()
        self.found = {"id": "uuid-1", "identifier": "TAL-80", "title": "Fix the login loop", "url": "u"}
        argv = ["promote", "--recover", "--session", SID]
        self.assertEqual(rw.main(argv), 1)  # not a terminal
        self.assertIn("run it in a terminal", self.notes[-1])
        self.assertEqual(rw.main(argv + ["--dry-run"]), 0)
        self.assertIn("adopt TAL-80", self.emitted[-1]["recover"])
        with terminal("no\n"):
            self.assertEqual(rw.main(argv), 1)
        self.assertEqual((self.dir / "session.json").read_bytes(), before)
        self.assertEqual(self.rex_calls, [])
        with terminal("yes\n"):
            self.assertEqual(rw.main(argv), 0)
        st = self.state()
        self.assertEqual((st["kind"], st["ticket"], st["label"]), ("ticketed", "TAL-80", "TAL-80 Fix the login loop"))
        self.assertEqual(st["filing"]["done"], ["label"])
        self.assertEqual(self.creates, [])
        self.assertEqual(rw.main(argv), 1)
        self.assertIn("nothing to recover", self.notes[-1])

    def test_partial_promotion_finishes_remaining_steps(self):
        self.authorize()
        self.rex_fail = True
        self.assertEqual(self.file(), 1)
        self.assertIn("--recover", self.notes[-1])
        st = self.state()
        self.assertEqual((st["kind"], st["ticket"]), ("ticketed", "TAL-77"))
        self.assertEqual(st["filing"]["done"], [])
        self.assertEqual(self.emitted[-1]["pending"], ["label"])
        # Opening a new draft is refused with a pointer to --recover, not "already ticketed".
        self.assertEqual(rw.main(["promote", "--session", SID]), 1)
        self.assertIn("--recover", self.notes[-1])
        self.rex_fail = False
        with terminal("yes\n"):
            self.assertEqual(rw.main(["promote", "--recover", "--session", SID]), 0)
        self.assertEqual(len(self.creates), 1)
        st = self.state()
        self.assertEqual(st["filing"]["done"], ["label"])
        self.assertEqual(st["label"], "TAL-80 Fix the login loop")
        self.assertIn("promoted_at", st)
        # Finished: now it is simply ticketed.
        self.assertEqual(rw.main(["promote", "--session", SID]), 1)
        self.assertIn("already ticketed", self.notes[-1])

    def test_worktree_step_retried_without_relabel(self):
        self.authorize()
        attempts = []

        def tree(repo, ticket, title, state=None):
            attempts.append(ticket)
            if len(attempts) == 1:
                raise rw.Failure("worktree path exists")
            return {"repo": "/r", "worktree": "/w", "branch": "u/tfriedman/tal-77-x", "action": "create branch"}
        rec = json.loads((self.dir / "promote.json").read_text())
        rec["repo"] = "/r"
        (self.dir / "promote.json").write_text(json.dumps(rec))
        with patched(ensure_worktree=tree, get_issue=lambda t: {"id": "x", "identifier": "TAL-77",
                                                                 "title": "Fix the login loop", "url": "u"}):
            self.assertEqual(self.file(), 1)
            self.assertEqual(self.state()["filing"]["done"], ["label"])
            renames = len(self.rex_calls)
            with terminal("yes\n"):
                self.assertEqual(rw.main(["promote", "--recover", "--session", SID]), 0)
        self.assertEqual(len(self.rex_calls), renames)  # label step not repeated
        st = self.state()
        self.assertEqual(st["filing"]["done"], ["label", "worktree"])
        self.assertEqual((st["worktree"], st["branch"]), ("/w", "u/tfriedman/tal-77-x"))
        self.assertEqual(len(self.creates), 1)


class LandDigest(Land):
    """D: the plan Tal saw is the plan that runs."""

    def dry(self, *flags):
        self.assertEqual(self.land("--dry-run", "--json", *flags), 0)
        return self.emitted[-1]["digest"]

    def test_expect_matches(self):
        self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        digest = self.dry("--keep")
        self.assertEqual(self.land("--yes", "--keep", "--expect", digest), 0)
        self.assertIn(PUSH, self.calls)

    def test_stale_digest_refused(self):
        d = self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        digest = self.dry()  # close variant
        self.assertEqual(self.land("--yes", "--keep", "--expect", digest), 1)  # keep variant differs
        self.assertIn("the plan changed; look again", self.notes[-1])
        # The session is re-adopted for another ticket while the confirmation is open.
        digest = self.dry("--keep")
        st = json.loads((d / "session.json").read_text())
        st.update(ticket="TAL-2", label="TAL-2 Y", branch="u/tfriedman/tal-2-y")
        (d / "session.json").write_text(json.dumps(st))
        self.assertEqual(self.land("--yes", "--keep", "--expect", digest), 1)
        self.assertIn("the plan changed", self.notes[-1])
        self.assertEqual([c for c in self.calls if c[0] not in ("verify",)], [])

    def test_head_change_refused(self):
        self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        heads = iter(["aaa", "bbb"])
        git = lambda *a, **k: "origin" if a[0] == "remote" else (next(heads) if a[0] == "rev-parse" else "")
        with patched(git=git):
            digest = self.dry("--keep")
            self.assertEqual(self.land("--yes", "--keep", "--expect", digest), 1)
        self.assertIn("the plan changed", self.notes[-1])
        self.assertFalse([c for c in self.calls if c[:2] == ("git", "push")])

    def test_head_moves_after_plan_stops_before_push(self):
        self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        heads = iter(["aaa", "bbb"])
        git = lambda *a, **k: "origin" if a[0] == "remote" else (next(heads) if a[0] == "rev-parse" else "")
        with patched(git=git):
            self.assertEqual(self.land("--yes", "--keep"), 1)
        self.assertIn("moved from aaa to bbb", self.notes[-1])
        self.assertFalse([c for c in self.calls if c[:2] == ("git", "push")])

    def test_terminal_confirm_runs_the_shown_plan(self):
        d = self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        real_plan = rw.land_plan

        def plan_then_change(args, session, directory):
            out = real_plan(args, session, directory)
            st = json.loads((d / "session.json").read_text())
            st["ticket"] = "TAL-3"  # changes after the plan was shown
            (d / "session.json").write_text(json.dumps(st))
            return out
        with terminal("y\n") as err, patched(land_plan=plan_then_change):
            self.assertEqual(self.land("--keep"), 1)
        self.assertIn("Proceed?", err.getvalue())
        self.assertIn("the plan changed", self.notes[-1])
        self.assertFalse([c for c in self.calls if c[:2] == ("git", "push")])

    def edit_state(self, d, **changes):
        st = json.loads((d / "session.json").read_text())
        st.update(changes)
        (d / "session.json").write_text(json.dumps(st))

    def test_linear_only_digest_covers_the_live_association(self):
        d = self.ticketed()
        digest = self.dry("--linear-only", "--keep")
        # A repo recorded on the session changes the digest even though Linear-only ignores it.
        self.edit_state(d, repo="/r")
        self.assertNotEqual(self.dry("--linear-only", "--keep"), digest)
        self.assertEqual(self.land("--yes", "--keep", "--linear-only", "--expect", digest), 1)
        self.assertIn("the plan changed", self.notes[-1])
        self.assertEqual(self.calls, [])

    def test_branch_attached_after_linear_only_confirmation(self):
        d = self.ticketed()
        digest = self.dry("--linear-only")
        self.edit_state(d, repo="/r", worktree="/w", branch="u/tfriedman/tal-1-x")  # rexwork adopt --repo
        self.assertEqual(self.land("--yes", "--linear-only", "--expect", digest), 1)
        self.assertEqual([c for c in self.calls if c[0] != "verify"], [])

    def test_linear_only_refused_with_a_branch(self):
        self.ticketed(worktree="/w", branch="u/tfriedman/tal-1-x")
        for flags in (["--dry-run"], ["--yes"], ["--yes", "--keep"]):
            self.assertEqual(self.land("--linear-only", *flags), 1)
            self.assertIn("--linear-only is only for a ticket without a branch", self.notes[-1])
        self.assertEqual(self.calls, [])


class LandPushesConfirmedCommit(unittest.TestCase):
    """The push sends the confirmed commit, not whatever the branch points at by then."""

    BRANCH = "u/tfriedman/tal-1-x"

    def git(self, *args, cwd=None):
        import subprocess
        return subprocess.run(["git", *args], cwd=cwd or self.work, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, name):
        (self.work / name).write_text(name)
        self.git("add", name)
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        self.ctx = contextlib.ExitStack()
        self.root, self.emitted, self.notes = self.ctx.enter_context(sandbox())
        tmp = Path(self.ctx.enter_context(tempfile.TemporaryDirectory())).resolve()
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.ctx.callback(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                   for k, v in saved.items()])
        self.remote, self.work = tmp / "remote.git", tmp / "work"
        self.git("init", "-q", "--bare", str(self.remote), cwd=tmp)
        self.git("init", "-q", "-b", self.BRANCH, str(self.work), cwd=tmp)
        self.git("remote", "add", "origin", str(self.remote))
        self.calls = []
        self.ctx.enter_context(patched(
            live_sessions=lambda: {SID: {"session_id": SID}},
            rex=lambda *a, **k: self.calls.append(("rex",) + a) or {},
            get_issue=lambda t: self.fail("Linear read"), open_pr=lambda w, b: self.fail("PR")))
        os.environ.pop("REX_SESSION", None)
        make_state(self.root, {"kind": "ticketed", "label": "TAL-1 X", "ticket": "TAL-1", "repo": str(self.work),
                               "worktree": str(self.work), "branch": self.BRANCH})

    def tearDown(self):
        self.ctx.close()

    def land(self, *flags):
        return rw.main(["land", "--session", SID, "--no-pr", "--no-linear", "--keep", *flags])

    def test_commit_after_confirmation_is_not_pushed(self):
        confirmed = self.commit("a")
        self.assertEqual(self.land("--dry-run"), 0)
        digest = self.emitted[-1]["digest"]
        self.assertEqual(self.emitted[-1]["planned"]["head"], confirmed)
        real_verify, seen = rw.verify_branch, []

        def verify_then_commit(*a):
            real_verify(*a)
            seen.append(a)
            if len(seen) == 2:  # the check just before the push; a commit lands right after it
                self.newer = self.commit("b")
        with patched(verify_branch=verify_then_commit):
            self.assertEqual(self.land("--yes", "--expect", digest), 0)
        self.assertEqual(self.git("rev-parse", f"refs/heads/{self.BRANCH}", cwd=self.remote), confirmed)
        self.assertEqual(self.git("rev-parse", self.BRANCH), self.newer)
        self.assertEqual(self.emitted[-1]["pushed_sha"], confirmed)
        self.assertEqual(self.emitted[-1]["local_ahead"], self.newer)
        self.assertIn(f"pushed {confirmed[:12]}; local {self.BRANCH} is now at {self.newer[:12]}, not pushed",
                      self.notes[-1])
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", f"{self.BRANCH}@{{upstream}}"),
                         f"origin/{self.BRANCH}")

    def test_push_is_never_forced(self):
        base = self.commit("a")
        self.git("push", "-q", "origin", f"{self.commit('remote-only')}:refs/heads/{self.BRANCH}")
        self.git("reset", "-q", "--hard", base)
        self.commit("c")  # diverged from the remote branch
        self.assertEqual(self.land("--yes"), 1)
        self.assertIn("git push", self.notes[-1])
        self.assertNotEqual(self.git("rev-parse", f"refs/heads/{self.BRANCH}", cwd=self.remote),
                            self.git("rev-parse", "HEAD"))


class ReuseByState(unittest.TestCase):
    """E: durable state wins over the label; --reuse-tab never writes the host's state."""

    def test_label_match_with_other_ticket_is_not_reused(self):
        live = {SID: {"session_id": SID, "label": "TAL-1 Fix"}}
        new_sid = "session:" + OTHER
        calls = []

        def rex(*a, **k):
            calls.append(a)
            return {"session_id": new_sid, "initial_windows": [{"window_id": "window:n", "block_ids": ["block:n"]}]}
        with sandbox() as (root, emitted, notes), patched(
                live_sessions=lambda: live, rex=rex, open_show=lambda s, b: {"pane": "block:s"}):
            d = make_state(root, {"kind": "ticketed", "ticket": "TAL-2", "branch": "u/tfriedman/tal-2-x",
                                  "worktree": "/w2", "filing": {"ticket": "TAL-2"}})
            before = (d / "session.json").read_bytes()
            self.assertIsNone(rw.find_ticket_session("TAL-1"))
            rc = rw.main(["start", "TAL-1", "--title", "Fix", "--no-linear", "--cwd", str(root),
                          "--owner", "symphony", "--reuse-tab", "symphony", "--", "codex", "resume", "t"])
            self.assertEqual(rc, 0)
            self.assertEqual(calls[0][0], "new")  # a new session, not a tab in TAL-2's
            self.assertEqual(emitted[-1]["session_id"], new_sid)
            self.assertFalse(emitted[-1]["reused"])
            self.assertEqual((d / "session.json").read_bytes(), before)

    def test_reuse_tab_without_state_creates_none(self):
        live = {SID: {"session_id": SID, "label": "TAL-1 Hand made"}}
        with sandbox() as (root, emitted, _), patched(
                live_sessions=lambda: live, open_show=lambda s, b: {"pane": "block:s"},
                rex=lambda *a, **k: {"block_ids": ["block:t"], "window_id": "window:t"}):
            rc = rw.main(["start", "TAL-1", "--title", "X", "--no-linear", "--cwd", str(root),
                          "--reuse-tab", "symphony", "--", "true"])
            self.assertEqual(rc, 0)
            self.assertFalse((root / "sessions" / UUID).exists())
            out = emitted[-1]
            for key in ("session_id", "window_id", "main_block", "show_block", "state_dir", "label", "reused", "tab"):
                self.assertIn(key, out)
            self.assertEqual(out["tab"]["main_block"], "block:t")


class ShowFailure(unittest.TestCase):
    """F: once the command runs, a failed Show pane is a warning."""

    def boom(self, *a):
        raise rw.Failure("show --open-pane: no such program")

    def test_new_session(self):
        rex = lambda *a, **k: {"session_id": SID,
                               "initial_windows": [{"window_id": "window:n", "block_ids": ["block:n"]}]}
        with sandbox() as (root, emitted, notes), patched(rex=rex, live_sessions=lambda: {}, open_show=self.boom):
            rc = rw.main(["start", "TAL-4", "--title", "X", "--no-linear", "--cwd", str(root), "--", "sleep", "1"])
            self.assertEqual(rc, 0)
            out = emitted[-1]
            self.assertIsNone(out["show_block"])
            self.assertEqual(out["main_block"], "block:n")
            self.assertIn("Show pane not opened", out["warnings"][0])
            self.assertTrue(any("warning" in n for n in notes))
            self.assertEqual(json.loads((root / "sessions" / UUID / "session.json").read_text())["ticket"], "TAL-4")

    def test_reuse_tab(self):
        live = {SID: {"session_id": SID, "label": "TAL-4 X"}}
        with sandbox() as (root, emitted, _), patched(
                live_sessions=lambda: live, open_show=self.boom,
                rex=lambda *a, **k: {"block_ids": ["block:t"], "window_id": "window:t"}):
            rc = rw.main(["start", "TAL-4", "--title", "X", "--no-linear", "--cwd", str(root),
                          "--reuse-tab", "symphony", "--", "true"])
            self.assertEqual(rc, 0)
            self.assertIsNone(emitted[-1]["tab"]["show_block"])
            self.assertEqual(len(emitted[-1]["warnings"]), 1)

    def test_bad_rex_new_result_closes_and_fails(self):
        calls = []

        def rex(*a, **k):
            calls.append(a)
            return {"session_id": SID} if a[0] == "new" else {}
        with sandbox() as (root, emitted, notes), patched(rex=rex, live_sessions=lambda: {}):
            rc = rw.main(["start", "TAL-4", "--title", "X", "--no-linear", "--cwd", str(root), "--", "true"])
            self.assertEqual(rc, 1)
            self.assertIn(("kill", SID), calls)
            self.assertEqual(emitted, [])
            self.assertFalse((root / "sessions" / UUID).exists())


class PromoteViewer(unittest.TestCase):
    """The draft opens as a tab of the session's shared editor Neovim."""

    def test_prepare_viewer_uses_the_shared_editor(self):
        calls = []
        fake = type("DV", (), {"ensure_server": staticmethod(
            lambda session, cwd=None: calls.append((session, cwd)) or (Path("/s/nvim.sock"), True))})
        with patched(importlib_docview=lambda: fake):
            sock = rw.prepare_viewer(Path("/d/ticket-draft.md"), SID, "block:01a1fabd-0000")
        self.assertEqual(sock, Path("/s/nvim.sock"))
        self.assertEqual(calls, [(SID, Path("/d"))])


FAKE_REXWORK = """
import hashlib, json, sys
argv, req = sys.argv[1:], json.loads(sys.stdin.read())
if "--preview" in argv:
    c = req["content"]
    print(json.dumps({"title": c.splitlines()[0], "digest": hashlib.sha256(c.encode()).hexdigest(),
                      "description_lines": 1, "description_chars": 1, "description_head": ["x"], "branch": "b"}))
else:
    with open(sys.argv[0] + ".log", "a") as f:
        f.write(json.dumps({"argv": argv, "req": req}) + "\\n")
    print(json.dumps({"ticket": "TAL-9", "url": "https://l/9"}))
"""


class PromoteDrafts(unittest.TestCase):
    """promote.lua in a shared Neovim: two drafts at once, a non-blocking confirm, and
    exactly the confirmed snapshot and its own token filed."""

    def setUp(self):
        import shutil
        import subprocess
        if not shutil.which("nvim"):
            self.skipTest("no nvim")
        self.dv = rw.importlib_docview()
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="rwp-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sock = self.tmp / "s.sock"
        proc = subprocess.Popen(["nvim", "--clean", "--headless", "--listen", str(self.sock)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        for _ in range(50):
            if self.dv.alive(self.sock):
                break
            time.sleep(0.1)
        self.fake = self.tmp / "fake_rexwork.py"
        self.fake.write_text(FAKE_REXWORK)

    def load(self, n, session):
        draft = self.tmp / f"draft{n}.md"
        draft.write_text(f"# Draft {n}\n\nbody {n}\n")
        token = self.tmp / f"token{n}"
        token.write_text(f"token-{n}")
        params = {"draft": str(draft), "token_file": str(token), "session": session, "rexwork": str(self.fake),
                  "dry_run": False, "python": rw.sys.executable}
        code = ("(function() local ok, r = pcall(function() return dofile(_A.lua)(_A.params) end) "
                "if ok then return r end return 'error: ' .. tostring(r) end)()")
        self.assertEqual(self.dv.lua(self.sock, code, arg={"lua": str(rw.PROMOTE_LUA), "params": params}), 1)
        self.assertFalse(token.exists(), "the token file was not consumed")
        return str(draft)

    def lua(self, code, arg=None):
        return self.dv.lua(self.sock, code, arg=arg)

    def log(self):
        path = Path(str(self.fake) + ".log")
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def test_two_drafts_coexist(self):
        one, two = self.load(1, SID), self.load(2, "session:01a1fabd-0000-7000-8000-000000000002")
        bufs = self.lua("(function() local r = {} for _, b in ipairs(vim.api.nvim_list_bufs()) do"
                        " r[vim.api.nvim_buf_get_name(b)] = b end return r end)()")
        b1, b2 = bufs[one], bufs[two]
        for b in (b1, b2):
            self.assertIn("RexworkFile", self.lua("vim.tbl_keys(vim.api.nvim_buf_get_commands(_A, {}))", arg=b))
            self.assertEqual(self.lua("#vim.api.nvim_get_autocmds({group = 'rexwork_promote_' .. _A})", arg=b), 1)
        # The question does not block Neovim: hold it open and check Neovim still answers.
        self.lua("(function() _G.answers = {} vim.ui.input = function(_, cb) _G.answers[#_G.answers + 1] = cb end"
                 " vim.api.nvim_set_current_buf(_A) vim.cmd('RexworkFile') return 1 end)()", arg=b2)
        self.assertEqual(self.lua("#_G.answers"), 1)
        # Edits after the snapshot are not filed.
        self.lua("(function() vim.api.nvim_buf_set_lines(_A, 0, 1, false, {'# Changed later'}) return 1 end)()", arg=b2)
        self.lua("(function() _G.answers[1]('yes') return 1 end)()")
        for _ in range(50):
            if self.log():
                break
            time.sleep(0.1)
        filed = self.log()
        self.assertEqual(len(filed), 1)
        req = filed[0]["req"]
        self.assertEqual(req["token"], "token-2")
        self.assertEqual(req["content"], "# Draft 2\n\nbody 2\n")
        self.assertEqual(req["digest"], hashlib.sha256(req["content"].encode()).hexdigest())
        self.assertIn("session:01a1fabd-0000-7000-8000-000000000002", filed[0]["argv"])
        for _ in range(50):
            if self.lua("vim.b[_A].rexwork_filed", arg=b2):
                break
            time.sleep(0.1)
        self.assertEqual(self.lua("vim.b[_A].rexwork_filed", arg=b2), "TAL-9")
        # The other draft is untouched: anything but "yes" files nothing, then it files with its own token.
        self.lua("(function() vim.ui.input = function(_, cb) cb('y') end"
                 " vim.api.nvim_set_current_buf(_A) vim.cmd('RexworkFile') return 1 end)()", arg=b1)
        self.assertEqual(len(self.log()), 1)
        self.lua("(function() vim.ui.input = function(_, cb) cb('yes') end"
                 " vim.api.nvim_set_current_buf(_A) vim.cmd('RexworkFile') return 1 end)()", arg=b1)
        for _ in range(50):
            if len(self.log()) == 2:
                break
            time.sleep(0.1)
        self.assertEqual(self.log()[1]["req"]["token"], "token-1")
        self.assertEqual(self.log()[1]["req"]["content"], "# Draft 1\n\nbody 1\n")
        # One confirmation, one filing.
        self.lua("(function() vim.cmd('RexworkFile') return 1 end)()")
        time.sleep(0.3)
        self.assertEqual(len(self.log()), 2)


# A stand-in for `treehouse`: a pool in $TREEHOUSE_STUB_DIR/pool.json whose slots are git
# worktrees of the repo it runs in. $TREEHOUSE_STUB_FAIL lists subcommands that fail.
TREEHOUSE_STUB = r'''
import json, os, pathlib, subprocess, sys
d = pathlib.Path(os.environ["TREEHOUSE_STUB_DIR"])
state = d / "pool.json"
pool = json.loads(state.read_text()) if state.exists() else []
args = sys.argv[1:]
with open(d / "calls.log", "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "args": args}) + "\n")
if args[0] in os.environ.get("TREEHOUSE_STUB_FAIL", "").split(","):
    sys.exit("treehouse: pool exhausted")
opt = lambda name: args[args.index(name) + 1] if name in args else None
if args[0] == "status":
    print(json.dumps(pool))
elif args[0] == "get":
    n = len(pool) + 1
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    path = d / "pool" / str(n) / pathlib.Path(top).name
    path.parent.mkdir(parents=True)
    branch = ["-b", opt("-b")] if opt("-b") else ["--detach"]
    r = subprocess.run(["git", "worktree", "add", "-q", *branch, str(path), "HEAD"], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr)
    row = {"name": str(n), "path": str(path), "status": "leased", "lease_id": "lease%d" % n,
           "lease_holder": opt("--lease-holder") or ""}
    pool.append(row)
    state.write_text(json.dumps(pool))
    print(json.dumps({k: row[k] for k in ("name", "path", "lease_id", "lease_holder")}))
elif args[0] == "return":
    for row in pool:
        if row["path"] == args[-1]:
            if opt("--if-lease-id") and row["lease_id"] != opt("--if-lease-id"):
                sys.exit("lease mismatch")
            row.update(lease_id="", lease_holder="", status="available")
            state.write_text(json.dumps(pool))
            sys.exit(0)
    sys.exit("no such worktree")
else:
    sys.exit("unexpected treehouse call")
'''

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}
VIEW = {"active_window_id": "window:n",
        "windows": [{"window_id": "window:n", "layers": [{"kind": "tiled", "blocks": [{"block_id": "block:n"}]}]}]}


class Worktrees(unittest.TestCase):
    """Default-on worktrees, the treehouse provider, land by recorded path, and release,
    with Rex stubbed, a stub `treehouse` and real git in temp repos."""

    BRANCH = "u/tfriedman/tal-5-fix-it"

    def git(self, *args, cwd=None):
        import subprocess
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def setUp(self):
        self.ctx = contextlib.ExitStack()
        self.root, self.emitted, self.notes = self.ctx.enter_context(sandbox())
        tmp = Path(self.ctx.enter_context(tempfile.TemporaryDirectory())).resolve()
        env = {**GIT_ENV, "TREEHOUSE_STUB_DIR": str(tmp / "th"), "REXWORK_TREEHOUSE": str(tmp / "treehouse"),
               "REXWORK_CONFIG": str(tmp / "config.json"), "TREEHOUSE_STUB_FAIL": ""}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.ctx.callback(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                   for k, v in saved.items()])
        (tmp / "th").mkdir()
        (tmp / "treehouse").write_text(f"#!{os.sys.executable}\n" + TREEHOUSE_STUB)
        (tmp / "treehouse").chmod(0o755)
        self.tmp, self.th = tmp, tmp / "th"
        self.remote, self.repo = tmp / "remote.git", tmp / "proj"
        self.git("init", "-q", "--bare", str(self.remote), cwd=tmp)
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=tmp)
        (self.repo / "a").write_text("a")
        self.git("add", "a")
        self.git("commit", "-q", "-m", "a")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        self.new_cwds, self.live, self.bars, self.tabs = [], {}, [], []

        def rex(*a, **k):
            if a[0] == "new":
                self.new_cwds.append(a[a.index("-c") + 1])
                return {"session_id": SID, "initial_windows": [{"window_id": "window:n", "block_ids": ["block:n"]}]}
            if a[:2] == ("session", "inspect"):
                return VIEW
            if a[:2] == ("window", "new"):
                self.tabs.append(a)
                return {"block_ids": ["block:t"], "window_id": "window:t"}
            return {}
        self.ctx.enter_context(patched(
            rex=rex, live_sessions=lambda: self.live, open_show=lambda s, b: {"pane": "block:show"},
            open_bar=lambda s, w: self.bars.append((s, w)) or {"pane": "block:bar"},
            WORKTREES=tmp / "worktrees"))
        os.environ.pop("REX_SESSION", None)

    def tearDown(self):
        self.ctx.close()

    def treehouse_on(self):
        Path(os.environ["REXWORK_CONFIG"]).write_text(json.dumps({"repos": {str(self.repo): {"worktrees": "treehouse"}}}))

    def calls(self, sub=None):
        log = self.th / "calls.log"
        rows = [json.loads(line)["args"] for line in log.read_text().splitlines()] if log.exists() else []
        return [r for r in rows if sub is None or r[0] == sub]

    def start(self, *flags, cwd=None):
        return rw.main(["start", "TAL-5", "--title", "Fix it", "--no-linear", "--cwd", str(cwd or self.repo),
                        *flags, "--", "true"])

    def state(self):
        return json.loads((self.root / "sessions" / UUID / "session.json").read_text())

    def release(self, *flags):
        return rw.main(["release", "--session", SID, *flags])

    # -- start ---------------------------------------------------------------

    def test_default_on_in_a_repo_makes_a_git_worktree(self):
        self.assertEqual(self.start(), 0)
        st = self.state()
        path = self.tmp / "worktrees" / "proj-tal-5"
        self.assertEqual((st["worktree"], st["branch"], st["worktree_provider"]), (str(path), self.BRANCH, "git"))
        self.assertEqual(st["repo"], str(self.repo))
        self.assertEqual(self.new_cwds, [str(path)])  # the agent starts in the worktree
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=path), self.BRANCH)
        self.assertEqual(self.calls(), [])  # no treehouse without opt-in, even with treehouse.toml
        self.assertEqual(st["bar_block"], "block:bar")
        self.assertEqual(self.bars, [(SID, "window:n")])

    def test_no_worktree_and_no_bar(self):
        self.assertEqual(self.start("--no-worktree", "--no-bar"), 0)
        st = self.state()
        self.assertNotIn("worktree", st)
        self.assertNotIn("bar_block", st)
        self.assertEqual(self.bars, [])
        self.assertEqual(self.new_cwds, [str(self.repo)])
        self.assertFalse((self.tmp / "worktrees").exists())

    def test_outside_a_repo_no_worktree(self):
        self.assertEqual(self.start(cwd=self.tmp), 0)
        self.assertNotIn("worktree", self.state())

    def test_scratch_never(self):
        rc = rw.main(["start", "--scratch", "notes", "--cwd", str(self.repo), "--", "true"])
        self.assertEqual(rc, 0)
        st = self.state()
        self.assertEqual(st["kind"], "scratch")
        self.assertNotIn("worktree", st)
        self.assertEqual(st["bar_block"], "block:bar")
        self.assertEqual(rw.main(["start", "--draft", "x", "--repo", str(self.repo)]), 1)
        self.assertFalse((self.tmp / "worktrees").exists())

    def test_symphony_owner_never(self):
        self.treehouse_on()
        self.assertEqual(self.start("--owner", "symphony"), 0)
        self.assertNotIn("worktree", self.state())
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.tmp / "worktrees").exists())
        self.assertEqual(self.start("--owner", "symphony", "--repo", str(self.repo)), 1)
        self.assertIn("never creates a worktree", self.notes[-1])

    def test_cwd_already_on_the_ticket_branch_is_adopted(self):
        here = self.tmp / "mine"
        self.git("worktree", "add", "-q", "-b", self.BRANCH, str(here), "main")
        self.assertEqual(self.start(cwd=here), 0)
        st = self.state()
        self.assertEqual((st["worktree"], st["worktree_provider"]), (str(here), "adopted"))
        self.assertFalse((self.tmp / "worktrees").exists())
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("did not create", self.notes[-1])
        self.assertTrue(here.exists())

    def test_treehouse_opt_in_leases_with_a_new_branch(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        get = self.calls("get")
        self.assertEqual(len(get), 1)
        self.assertEqual(get[0], ["get", "--lease", "--json", "--lease-holder", "rexwork:TAL-5", "-b", self.BRANCH])
        st = self.state()
        slot = self.th / "pool" / "1" / "proj"
        self.assertEqual((st["worktree"], st["worktree_provider"], st["lease_id"]), (str(slot), "treehouse", "lease1"))
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=slot), self.BRANCH)
        self.assertEqual(self.new_cwds, [str(slot)])
        self.assertFalse((self.tmp / "worktrees").exists())  # never plain git as well

    def test_treehouse_existing_branch_is_checked_out_in_the_slot(self):
        self.treehouse_on()
        self.git("branch", self.BRANCH, "main")
        self.assertEqual(self.start(), 0)
        get = self.calls("get")
        self.assertEqual(len(get), 1)
        self.assertNotIn("-b", get[0])
        slot = self.th / "pool" / "1" / "proj"
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD", cwd=slot), self.BRANCH)

    def test_treehouse_reuses_the_lease_it_holds(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        first = self.state()["worktree"]
        # The session is gone and its state dir moved aside: a new start must not lease again.
        (self.root / "sessions" / UUID).rename(self.root / "sessions" / OTHER)
        self.assertEqual(self.start(), 0)
        self.assertEqual(len(self.calls("get")), 1)
        st = self.state()
        self.assertEqual((st["worktree"], st["lease_id"]), (first, "lease1"))
        old = json.loads((self.root / "sessions" / OTHER / "session.json").read_text())
        self.assertNotIn("lease_id", old)  # handed over: only one session claims the lease
        self.assertEqual(old["worktree_moved_to"], SID)

    def test_other_holders_leases_are_left_alone(self):
        self.treehouse_on()
        (self.th / "pool.json").write_text(json.dumps([{"name": "1", "path": "/elsewhere", "status": "leased",
                                                        "lease_id": "x", "lease_holder": "agent:TAL-159-evals"}]))
        self.assertEqual(self.start(), 0)
        self.assertEqual(len(self.calls("get")), 1)
        self.assertEqual(self.state()["lease_id"], "lease2")
        self.assertEqual(self.calls("return"), [])

    def test_lease_failure_launches_nothing_then_rerun_succeeds(self):
        self.treehouse_on()
        os.environ["TREEHOUSE_STUB_FAIL"] = "get"
        self.assertEqual(self.start(), 1)
        self.assertIn("pool exhausted", self.notes[-1])
        self.assertIn("--no-worktree", self.notes[-1])
        self.assertIn("rexwork start TAL-5", self.notes[-1])
        self.assertEqual(self.new_cwds, [])  # no session, no agent in the main checkout
        self.assertFalse((self.root / "sessions" / UUID).exists())
        self.assertFalse((self.tmp / "worktrees").exists())  # no silent fallback to git
        os.environ["TREEHOUSE_STUB_FAIL"] = ""
        self.assertEqual(self.start(), 0)
        slot = self.th / "pool" / "1" / "proj"
        self.assertEqual(self.new_cwds, [str(slot)])
        st = self.state()
        self.assertEqual((st["worktree"], st["worktree_provider"], st["lease_id"]), (str(slot), "treehouse", "lease1"))
        self.assertNotIn("pending", st)

    def promote(self, *flags):
        d = make_state(self.root, {"kind": "scratch", "label": "x", "window_id": "window:w", "cwd": str(self.repo)})
        (d / "ticket-draft.md").write_text("# Fix it\n\nBody.\n")
        create = lambda p: {"id": p["id"], "identifier": "TAL-5", "title": p["title"], "url": "https://l/5"}
        with terminal("yes\n"), patched(create_issue=create, build_issue_input=lambda t, desc: (
                {"teamId": "t", "title": t, "description": desc, "assigneeId": "me", "stateId": "s"},
                {"viewer": {"name": "Tal"}})):
            return rw.main(["promote", "--file", "--session", SID, *flags])

    def test_promote_default_on_creates_the_worktree(self):
        self.assertEqual(self.promote(), 0)
        st = self.state()
        self.assertEqual((st["worktree"], st["worktree_provider"], st["branch"]),
                         (str(self.tmp / "worktrees" / "proj-tal-5"), "git", self.BRANCH))
        self.assertEqual(st["filing"]["done"], ["label", "worktree"])

    def test_promote_no_worktree(self):
        self.assertEqual(self.promote("--no-worktree"), 0)
        self.assertNotIn("worktree", self.state())
        self.assertFalse((self.tmp / "worktrees").exists())

    # -- land ----------------------------------------------------------------

    def test_land_goes_by_the_recorded_path(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        slot = self.state()["worktree"]
        (Path(slot) / "b").write_text("b")
        self.git("add", "b", cwd=slot)
        self.git("commit", "-q", "-m", "b", cwd=slot)
        self.live = {SID: {"session_id": SID}}
        self.assertEqual(rw.main(["land", "--session", SID, "--dry-run", "--no-pr", "--no-linear", "--keep"]), 0)
        out = self.emitted[-1]
        self.assertEqual(out["planned"]["worktree"], slot)
        self.assertTrue(any(f"in {slot}" in a for a in out["plan"]))
        self.assertEqual(out["worktree_kept"], slot)
        self.assertTrue(any("rexwork release" in a for a in out["plan"]))
        self.assertEqual(rw.main(["land", "--session", SID, "--yes", "--no-pr", "--no-linear", "--keep"]), 0)
        self.assertEqual(self.git("rev-parse", f"refs/heads/{self.BRANCH}", cwd=self.remote),
                         self.git("rev-parse", "HEAD", cwd=slot))
        self.assertTrue(Path(slot).exists())  # land never releases
        self.assertEqual(self.calls("return"), [])

    # -- release -------------------------------------------------------------

    def test_git_release_refuses_dirty_and_unpushed_then_removes(self):
        self.assertEqual(self.start(), 0)
        path = Path(self.state()["worktree"])
        (path / "b").write_text("b")
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("uncommitted changes", self.notes[-1])
        self.git("add", "b", cwd=path)
        self.git("commit", "-q", "-m", "b", cwd=path)
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("commits on no origin branch", self.notes[-1])
        self.assertEqual(self.release("--yes", "--force"), 1)  # --force needs a terminal
        self.assertIn("type yes", self.notes[-1])
        self.assertTrue(path.exists())
        self.git("push", "-q", "origin", f"{self.BRANCH}:refs/heads/{self.BRANCH}", cwd=path)
        self.git("fetch", "-q", "origin")
        self.assertEqual(self.release(), 1)  # no --yes and no terminal
        self.assertEqual(self.release("--dry-run"), 0)
        self.assertTrue(any("git worktree remove" in a for a in self.emitted[-1]["actions"]))
        self.assertTrue(path.exists())
        self.assertEqual(self.release("--yes"), 0)
        self.assertFalse(path.exists())
        self.assertTrue(self.git("rev-parse", "--verify", f"refs/heads/{self.BRANCH}"))  # branch kept
        st = self.state()
        self.assertNotIn("worktree", st)
        self.assertEqual(st["released"]["provider"], "git")
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("holds no worktree", self.notes[-1])

    def test_release_force_after_typed_yes(self):
        self.assertEqual(self.start(), 0)
        path = Path(self.state()["worktree"])
        (path / "b").write_text("b")
        with terminal("no\n"):
            self.assertEqual(self.release("--force"), 1)
        self.assertTrue(path.exists())
        with terminal("yes\n"):
            self.assertEqual(self.release("--force"), 0)
        self.assertFalse(path.exists())
        self.assertTrue(self.git("rev-parse", "--verify", f"refs/heads/{self.BRANCH}"))

    def test_treehouse_release_returns_by_lease_id(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        slot = self.state()["worktree"]
        self.assertEqual(rw.main(["ls", "--json"]), 0)
        held = self.emitted[-1][0]["holds_worktree"]
        self.assertEqual((held["provider"], held["lease_id"]), ("treehouse", "lease1"))
        self.assertIn("age", held)
        self.assertEqual(rw.main(["release", "TAL-5", "--yes"]), 0)  # found by ticket
        self.assertEqual(self.calls("return"), [["return", "--if-lease-id", "lease1", slot]])
        self.assertNotIn("lease_id", self.state())
        rw.main(["ls", "--json"])
        self.assertIsNone(self.emitted[-1][0]["holds_worktree"])

    def test_release_never_touches_a_lease_it_does_not_hold(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        pool = json.loads((self.th / "pool.json").read_text())
        pool[0].update(lease_id="theirs", lease_holder="agent:TAL-159-evals")
        (self.th / "pool.json").write_text(json.dumps(pool))
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("not touching it", self.notes[-1])
        self.assertEqual(self.calls("return"), [])

    # -- review fixes ----------------------------------------------------------

    def foreign_slot(self, holder="agent:TAL-159-evals"):
        """A pool slot on the ticket's branch that someone else leased."""
        slot = self.th / "pool" / "9" / "proj"
        slot.parent.mkdir(parents=True)
        self.git("worktree", "add", "-q", "-b", self.BRANCH, str(slot), "main")
        (self.th / "pool.json").write_text(json.dumps([{"name": "9", "path": str(slot), "status": "leased",
                                                        "lease_id": "theirs", "lease_holder": holder}]))
        os.environ["REXWORK_TREEHOUSE_ROOT"] = str(self.th / "pool")
        self.ctx.callback(os.environ.pop, "REXWORK_TREEHOUSE_ROOT", None)
        return slot

    def test_someone_elses_slot_on_the_branch_is_never_used(self):
        self.foreign_slot()
        slot = self.th / "pool" / "9" / "proj"
        self.assertEqual(self.start(), 1)  # discovered via git worktree list
        self.assertIn("leaves it alone", self.notes[-1])
        self.assertEqual(self.start(cwd=slot), 1)  # started from inside that slot
        self.assertIn("--no-worktree", self.notes[-1])
        self.assertEqual(self.new_cwds, [])  # nothing launched, there or in the main checkout
        self.assertFalse((self.root / "sessions" / UUID).exists())
        self.assertEqual(self.calls("return"), [])

    def test_recorded_slot_needs_the_recorded_lease(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        st = self.state()
        pool = json.loads((self.th / "pool.json").read_text())
        pool[0].update(lease_id="theirs", lease_holder="agent:other")
        (self.th / "pool.json").write_text(json.dumps(pool))
        with self.assertRaises(rw.WorktreeBlocked):
            rw.ensure_worktree(str(self.repo), "TAL-5", "Fix it", st)
        pool[0].update(lease_id="lease-new", lease_holder="rexwork:TAL-5")  # ours, but a different lease
        (self.th / "pool.json").write_text(json.dumps(pool))
        with self.assertRaises(rw.WorktreeBlocked):
            rw.ensure_worktree(str(self.repo), "TAL-5", "Fix it", st)

    def test_hand_made_worktree_at_rexworks_path_is_adopted(self):
        path = self.tmp / "worktrees" / "proj-tal-5"
        self.git("worktree", "add", "-q", "-b", self.BRANCH, str(path), "main")
        self.assertEqual(self.start(), 0)
        st = self.state()
        self.assertEqual((st["worktree"], st["worktree_provider"]), (str(path), "adopted"))
        self.assertNotIn("worktree_created", st)
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("did not create", self.notes[-1])
        self.assertTrue(path.exists())

    def test_release_refuses_a_different_checkout_at_the_recorded_path(self):
        self.assertEqual(self.start(), 0)
        path = Path(self.state()["worktree"])
        self.git("worktree", "remove", str(path))
        self.git("worktree", "add", "-q", str(path), self.BRANCH)  # someone recreates it by hand
        self.assertEqual(self.release("--yes"), 1)
        self.assertIn("not the worktree rexwork created", self.notes[-1])
        self.assertTrue(path.exists())

    def test_release_of_a_gone_worktree_never_prunes(self):
        import shutil
        self.assertEqual(self.start(), 0)
        path = Path(self.state()["worktree"])
        other = self.tmp / "unmounted"
        self.git("worktree", "add", "-q", "--detach", str(other), "main")
        shutil.rmtree(other)  # like a worktree on an unmounted volume
        shutil.rmtree(path)
        self.assertEqual(self.release("--yes"), 0)
        self.assertTrue(any("git worktree prune" in a and "yourself" in a for a in self.emitted[-1]["actions"]))
        self.assertIn(str(other), self.git("worktree", "list", "--porcelain"))
        self.assertNotIn("worktree", self.state())

    def test_concurrent_start_waits_for_the_ticket_lock(self):
        import fcntl
        locks = self.root / "locks"
        locks.mkdir()
        with open(locks / "TAL-5.session.lock", "w") as held, patched(SESSION_LOCK_WAIT=0.3):
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.start(), 1)
            self.assertIn("registering its session", self.notes[-1])
        self.assertEqual(self.new_cwds, [])
        with open(locks / "TAL-5.worktree.lock", "w") as held, patched(WORKTREE_LOCK_WAIT=0.3):
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.start(), 1)
            self.assertIn("acquiring its worktree", self.notes[-1])
            # Symphony's path takes no worktree lock, so a long lease never blocks it.
            self.assertEqual(self.start("--owner", "symphony"), 0)
        self.assertEqual(len(self.new_cwds), 1)

    def test_session_that_appears_during_worktree_acquisition_is_reused(self):
        real = rw.worktree_for_start

        def racing(*a):
            out = real(*a)
            make_state(self.root, {"kind": "ticketed", "ticket": "TAL-5", "label": "TAL-5 Fix it"})
            self.live = {SID: {"session_id": SID, "label": "TAL-5 Fix it"}}
            return out
        with patched(worktree_for_start=racing):
            self.assertEqual(self.start(), 0)
        self.assertEqual(self.new_cwds, [])  # no second session
        self.assertTrue(self.emitted[-1]["reused"])
        self.assertEqual(self.state()["worktree"], str(self.tmp / "worktrees" / "proj-tal-5"))

    def test_failed_checkout_keeps_the_lease_for_the_rerun(self):
        self.treehouse_on()

        def broken(path, plan):
            raise rw.Failure("checkout failed")
        with patched(checkout_ticket_branch=broken):
            self.assertEqual(self.start(), 1)
        self.assertIn("checkout failed", self.notes[-1])
        self.assertEqual(self.new_cwds, [])
        self.assertEqual(self.start(), 0)  # reuses the lease it already took
        self.assertEqual(len(self.calls("get")), 1)
        self.assertEqual(self.new_cwds, [str(self.th / "pool" / "1" / "proj")])

    def test_release_clears_a_pending_worktree_retry(self):
        self.treehouse_on()
        self.assertEqual(self.start(), 0)
        rw.update_state(self.root / "sessions" / UUID, pending=["worktree"], worktree_error="checkout failed")
        self.assertEqual(self.release("--yes"), 0)
        self.assertEqual(self.calls("return")[0][:3], ["return", "--if-lease-id", "lease1"])
        st = self.state()
        for key in ("pending", "worktree_error", "worktree", "lease_id", "worktree_created"):
            self.assertNotIn(key, st)

    def test_launch_dir_never_inside_another_holders_slot(self):
        self.treehouse_on()
        slot = self.th / "pool" / "9" / "proj"
        slot.parent.mkdir(parents=True)
        self.git("worktree", "add", "-q", "-b", "u/tfriedman/tal-159-x", str(slot), "main")
        (self.th / "pool.json").write_text(json.dumps([{"name": "9", "path": str(slot), "status": "leased",
                                                        "lease_id": "theirs", "lease_holder": "agent:TAL-159-x"}]))
        os.environ["TREEHOUSE_STUB_FAIL"] = "get"  # a generic failure: no blocked path
        self.assertEqual(self.start(cwd=slot), 1)
        self.assertEqual(self.new_cwds, [])  # a ticket start launches nothing
        # A scratch session expects no worktree: it is redirected to the main checkout.
        rc = rw.main(["start", "--scratch", "notes", "--cwd", str(slot), "--no-bar", "--", "true"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.new_cwds, [str(self.repo)])
        self.assertEqual(self.calls("return"), [])

    def test_no_worktree_still_launches_in_cwd(self):
        self.treehouse_on()
        os.environ["TREEHOUSE_STUB_FAIL"] = "get"
        self.assertEqual(self.start("--no-worktree"), 0)
        self.assertEqual(self.new_cwds, [str(self.repo)])
        self.assertEqual(self.calls("get"), [])
        self.assertNotIn("worktree", self.state())

    def test_racing_symphony_start_still_runs_its_command_in_a_new_tab(self):
        lookups = []

        def find(ticket):
            lookups.append(ticket)
            if len(lookups) == 1:
                return None  # nothing yet; another start registers one before the recheck
            make_state(self.root, {"kind": "ticketed", "ticket": "TAL-5", "label": "TAL-5 Fix it"})
            return SID
        with patched(find_ticket_session=find):
            rc = rw.main(["start", "TAL-5", "--title", "Fix it", "--no-linear", "--cwd", str(self.tmp),
                          "--owner", "symphony", "--reuse-tab", "symphony", "--", "codex", "resume", "t"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.new_cwds, [])
        self.assertEqual(len(self.tabs), 1)
        self.assertEqual(self.tabs[0][-3:], ("codex", "resume", "t"))
        self.assertEqual(self.emitted[-1]["tab"]["main_block"], "block:t")

    def test_lease_acquired_during_a_race_is_given_back(self):
        self.treehouse_on()
        real = rw.worktree_for_start

        def racing(*a):
            out = real(*a)
            make_state(self.root, {"kind": "ticketed", "ticket": "TAL-5", "label": "TAL-5 Fix it",
                                   "owner": "symphony"})
            self.live = {SID: {"session_id": SID, "label": "TAL-5 Fix it"}}
            return out
        with patched(worktree_for_start=racing):
            self.assertEqual(self.start(), 0)
        self.assertEqual(self.new_cwds, [])
        self.assertEqual(self.calls("return")[0][:3], ["return", "--if-lease-id", "lease1"])
        self.assertNotIn("worktree", self.state())  # Symphony's session does not take it
        self.assertTrue(any("returned treehouse slot" in w for w in self.emitted[-1]["warnings"]))
        pool = json.loads((self.th / "pool.json").read_text())
        self.assertEqual(pool[0]["lease_id"], "")  # nothing stranded

    def racing(self, **extra):
        real = rw.worktree_for_start

        def race(*a):
            out = real(*a)
            make_state(self.root, {"kind": "ticketed", "ticket": "TAL-5", "label": "TAL-5 Fix it", **extra})
            self.live = {SID: {"session_id": SID, "label": "TAL-5 Fix it"}}
            return out
        return race

    def inspect_fails(self, *a):
        raise rw.Failure("rex session inspect: timed out")

    def test_winning_session_takes_the_lease_before_inspection_can_fail(self):
        self.treehouse_on()
        with patched(worktree_for_start=self.racing(), main_block_of=self.inspect_fails):
            self.assertEqual(self.start(), 1)
        self.assertEqual(self.state()["lease_id"], "lease1")  # recorded, so `release TAL-5` finds it
        self.assertEqual(self.calls("return"), [])

    def test_lease_declined_by_symphony_is_returned_even_if_inspection_fails(self):
        self.treehouse_on()
        with patched(worktree_for_start=self.racing(owner="symphony"), main_block_of=self.inspect_fails):
            self.assertEqual(self.start(), 1)
        self.assertEqual([c[:3] for c in self.calls("return")], [["return", "--if-lease-id", "lease1"]])
        self.assertNotIn("worktree", self.state())

    def test_fresh_lease_given_back_when_linear_fails(self):
        self.treehouse_on()
        with patched(get_issue=lambda t: (_ for _ in ()).throw(rw.Failure("Linear: down"))):
            rc = rw.main(["start", "TAL-5", "--title", "Fix it", "--cwd", str(self.repo), "--", "true"])
        self.assertEqual(rc, 1)
        self.assertEqual([c[:3] for c in self.calls("return")], [["return", "--if-lease-id", "lease1"]])
        self.assertEqual(self.new_cwds, [])

    def test_fresh_git_worktree_removed_when_the_session_can_not_be_made(self):
        def bad_rex(*a, **k):
            return {"session_id": SID} if a[0] == "new" else {}
        with patched(rex=bad_rex):
            self.assertEqual(self.start(), 1)
        self.assertFalse((self.tmp / "worktrees" / "proj-tal-5").exists())
        self.assertTrue(self.git("rev-parse", "--verify", f"refs/heads/{self.BRANCH}"))  # branch kept

    def test_stale_worktree_entry_is_not_a_usable_worktree(self):
        import shutil
        stale = self.tmp / "stale"
        self.git("worktree", "add", "-q", "-b", self.BRANCH, str(stale), "main")
        shutil.rmtree(stale)  # deleted outside git, then reused for other work
        stale.mkdir()
        (stale / "notes.txt").write_text("unrelated")
        self.assertEqual(self.start(), 1)
        self.assertIn("git worktree prune", self.notes[-1])
        self.assertIn("--no-worktree", self.notes[-1])
        self.assertEqual(self.new_cwds, [])

    def test_clone_nested_in_another_holders_slot_is_still_that_slot(self):
        self.treehouse_on()
        slot = self.th / "pool" / "9" / "proj"
        slot.parent.mkdir(parents=True)
        self.git("worktree", "add", "-q", "-b", "u/tfriedman/tal-159-x", str(slot), "main")
        (self.th / "pool.json").write_text(json.dumps([{"name": "9", "path": str(slot), "status": "leased",
                                                        "lease_id": "theirs", "lease_holder": "agent:TAL-159-x"}]))
        os.environ["REXWORK_TREEHOUSE_ROOT"] = str(self.th / "pool")
        self.ctx.callback(os.environ.pop, "REXWORK_TREEHOUSE_ROOT", None)
        inner = slot / "inner"
        self.git("init", "-q", "-b", "main", str(inner))
        self.assertEqual(rw.main(["start", "--scratch", "notes", "--cwd", str(inner), "--", "true"]), 0)
        self.assertEqual(self.new_cwds, [str(self.repo)])  # the slot's main checkout, not the clone

    def test_ask_offers_the_agent(self):
        from unittest import mock
        for answers, flags, agent in ((["1", "notes", "codex"], [], "codex"), (["1", "notes", ""], [], "claude"),
                                      (["2", "draft"], ["--agent", "codex"], "codex")):
            with mock.patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
                rc = rw.main(["start", "--ask", "--cwd", str(self.tmp), *flags])
            self.assertEqual(rc, 0)
            self.assertEqual(self.state()["agent"], agent)
            (self.root / "sessions" / UUID / "session.json").unlink()

    # -- bar -----------------------------------------------------------------

    def test_bar_failure_is_a_warning(self):
        with patched(open_bar=REAL_OPEN_BAR):
            os.environ["REXWORK_BAR"] = "/nonexistent/rexbar"
            try:
                self.assertEqual(self.start("--no-worktree"), 0)
            finally:
                os.environ.pop("REXWORK_BAR")
        out = self.emitted[-1]
        self.assertIsNone(out["bar_block"])
        self.assertTrue(any("status bar not opened" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
