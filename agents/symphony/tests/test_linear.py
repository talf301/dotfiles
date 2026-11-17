"""Linear adapter over MCP tools (SPEC §17.3), label-based waiting, wake rule, bridge writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fakes import FakeLinearBoard, FakeMcp, make_issue
from symphony import linear
from symphony.errors import ConfigError, TrackerError
from symphony.linear import (
    BlockerRef,
    LinearComment,
    LinearTracker,
    bridge_event,
    classify_comment,
    compute_dispatchable,
    is_parked,
    is_wake_comment,
    parse_issue,
    validate_provider_config,
)

VIEWER = "user-tal"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TERMINAL = linear.DEFAULT_TERMINAL_STATES
WAKE = linear.DEFAULT_WAKE_STATES


def make_tracker(tools, *, provider=None, turn_starts=None, required=("agent",)):
    prov = {"team_key": "TAL"}
    prov.update(provider or {})
    starts = turn_starts or {}
    return LinearTracker(prov, active_states=linear.DEFAULT_ACTIVE_STATES,
                         terminal_states=TERMINAL, tools=tools, required_labels=required,
                         turn_started_at=lambda ident: starts.get(ident))


@pytest.fixture
def logged(monkeypatch):
    events = []
    monkeypatch.setattr(linear, "log", lambda event, level="info", **f: events.append(
        (event, level, f)))
    return events


# --- profile ---------------------------------------------------------------------------

def test_profile_defaults_match_design():
    assert linear.DEFAULT_ACTIVE_STATES == ("Todo", "In Progress", "In Review")
    assert linear.DEFAULT_TERMINAL_STATES == ("Done", "Canceled", "Duplicate")
    assert linear.DEFAULT_WAKE_STATES == ("In Review",)
    assert linear.DEFAULT_WAITING_LABEL == "waiting"
    assert linear.DEFAULT_IN_PROGRESS_STATE == "In Progress"
    assert linear.DEFAULT_HUMAN_REVIEW_STATE == "In Review"
    assert linear.PAGE_SIZE == 50
    assert not hasattr(linear, "resolve_api_key") and not hasattr(linear, "HttpGraphQL")


def test_validate_provider_config():
    validate_provider_config({"team_key": "TAL"})
    for bad in ({}, {"team_key": " "}, {"team_key": "TAL", "wake_states": "In Review"},
                {"team_key": "TAL", "waiting_label": ""},
                {"team_key": "TAL", "keychain_service": "bad name; rm"},
                {"team_key": "TAL", "comment_window": 0}):
        with pytest.raises(ConfigError) as e:
            validate_provider_config(bad)
        assert e.value.category == "invalid_tracker_config"


# --- comment signatures and dispatchable --------------------------------------------------

@pytest.mark.parametrize("body,kind", [
    ("🤖 agent (Codex, TAL-1)\n\nwaiting on runs", "agent"),
    ("\n  🤖 experiment-run\nrun done", "bridge"),
    ("🤖 experiment-run status\n\nrunning", "status"),
    ("🤖 symphony\n\nstarted a new thread", "symphony"),
    ("looks good, go ahead", "human"),
    ("", "human"),
    ("note\n🤖 agent (Codex, TAL-1)", "human"),
])
def test_classify_comment(body, kind):
    assert classify_comment(body) == kind


def test_wake_comments_are_bridge_events_and_human_only():
    assert is_wake_comment(LinearComment(T0, "🤖 experiment-run\nRUNNING -> SUCCEEDED"))
    assert is_wake_comment(LinearComment(T0, "yes, use the larger batch"))
    assert not is_wake_comment(LinearComment(T0, "🤖 experiment-run status\nrunning"))
    assert not is_wake_comment(LinearComment(T0, "🤖 agent (Codex, TAL-1)\nparked"))
    assert not is_wake_comment(LinearComment(T0, "🤖 symphony\nescalated"))


def test_is_parked_waiting_label_or_in_review():
    assert is_parked(state="In Progress", labels=("agent", "Waiting"), wake_states=WAKE)
    assert is_parked(state="in review", labels=("agent",), wake_states=WAKE)
    assert not is_parked(state="In Progress", labels=("agent",), wake_states=WAKE)
    assert not is_parked(state="Todo", labels=("agent",), wake_states=WAKE)


def disp(**kw):
    base = dict(state="In Progress", assignee_id=VIEWER, viewer_id=VIEWER, blockers=(),
                comments=(), last_turn_started_at=None, terminal_states=TERMINAL,
                wake_states=WAKE, labels=("agent",))
    base.update(kw)
    return compute_dispatchable(**base)


def test_dispatchable_assignment():
    assert disp()
    assert not disp(assignee_id=None)
    assert not disp(assignee_id="someone-else")


def test_dispatchable_blockers():
    assert disp(blockers=[BlockerRef("b", "TAL-2", "done")])
    assert not disp(blockers=[BlockerRef("b", "TAL-2", "In Progress")])
    assert not disp(blockers=[BlockerRef("b", "TAL-2", None)])


PARKED = [dict(state="In Progress", labels=("agent", "waiting")),
          dict(state="In Review", labels=("agent",))]


@pytest.mark.parametrize("parked", PARKED, ids=["waiting-label", "in-review"])
def test_wake_rule(parked):
    start = T0
    after, before = start + timedelta(seconds=5), start - timedelta(seconds=5)
    assert not disp(**parked, last_turn_started_at=start)
    assert disp(**parked, last_turn_started_at=start, comments=[LinearComment(after, "go")])
    assert not disp(**parked, last_turn_started_at=start,
                    comments=[LinearComment(before, "go")])
    assert disp(**parked, last_turn_started_at=start,
                comments=[LinearComment(after, "🤖 experiment-run\nSUCCEEDED")])
    assert not disp(**parked, last_turn_started_at=start, comments=[
        LinearComment(after, "🤖 agent (Codex, TAL-1)\nwaiting"),
        LinearComment(after, "🤖 experiment-run status\nrunning"),
        LinearComment(after, "🤖 symphony\nnote")])
    assert disp(**parked, comments=[LinearComment(before, "go")])
    assert not disp(**parked, assignee_id=None, comments=[LinearComment(after, "go")])


def test_mid_turn_race_comment_before_park_still_wakes():
    bridge = LinearComment(T0 + timedelta(seconds=10), "🤖 experiment-run\nSUCCEEDED")
    park = LinearComment(T0 + timedelta(seconds=20), "🤖 agent (Codex, TAL-1)\nwaiting on runs")
    assert disp(**PARKED[0], last_turn_started_at=T0, comments=[park, bridge])


def test_unsigned_agent_comment_causes_spurious_wake():
    forgot = LinearComment(T0 + timedelta(seconds=20), "waiting on runs")
    assert disp(**PARKED[0], last_turn_started_at=T0, comments=[forgot])


# --- parsing tool results -------------------------------------------------------------------

def item(**over):
    d = {"id": "TAL-7", "uuid": "uuid-7", "title": "Ticket 7", "description": "desc",
         "priority": {"value": 2, "name": "High"}, "url": "https://linear.app/x/issue/TAL-7",
         "gitBranchName": "tal-7", "createdAt": "2026-01-01T00:00:00.000Z",
         "updatedAt": "2026-01-02T00:00:00.000Z", "status": "Todo",
         "labels": [" Agent ", "repo:Dotfiles", "agent", "  "], "assigneeId": VIEWER,
         "team": "TAL"}
    d.update(over)
    return d


def test_parse_issue_full_record_and_variants():
    raw = parse_issue(item())
    issue = linear.build_issue(raw, team_key="TAL", dispatchable=True)
    assert (issue.id, issue.identifier, issue.title, issue.state) == (
        "uuid-7", "TAL-7", "Ticket 7", "Todo")
    assert issue.labels == ("agent", "repo:dotfiles") and issue.priority == 2
    assert issue.branch_name == "tal-7" and issue.assignee_id == VIEWER
    assert issue.native_ref == {"linear_issue_id": "uuid-7", "team_key": "TAL"}
    assert issue.created_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    # GraphQL-like variants: uuid in id, identifier field, nested state/assignee/labels.
    uid = "5b0c1f9e-3c1a-4c7e-9a52-0d5f0d7a1b11"
    alt = parse_issue({"id": uid, "identifier": "TAL-9", "title": "t",
                       "state": {"name": "In Review"}, "assignee": {"id": "u"},
                       "labels": {"nodes": [{"name": "Waiting"}]}})
    assert (alt.uuid, alt.identifier, alt.state, alt.assignee_id, alt.labels) == (
        uid, "TAL-9", "In Review", "u", ("waiting",))


def test_parse_issue_bad_optional_metadata_falls_back():
    raw = parse_issue(item(priority=0, createdAt="not a date", updatedAt=None, description=5,
                           gitBranchName=None, url=None, labels=None, assigneeId=None))
    i = linear.build_issue(raw, team_key="TAL", dispatchable=False)
    assert i.priority is None and i.created_at is None and i.updated_at is None
    assert i.description is None and i.branch_name is None and i.url is None
    assert i.labels == () and i.assignee_id is None


@pytest.mark.parametrize("bad", [{"id": None, "uuid": None}, {"title": "  "}, {"status": None},
                                 {"status": {"name": ""}}])
def test_parse_issue_rejects_missing_required(bad):
    with pytest.raises(TrackerError) as e:
        parse_issue(item(**bad))
    assert e.value.category == "tracker_response"


# --- reads: MCP tool call mapping ------------------------------------------------------------

def test_empty_inputs_make_no_call():
    mcp = FakeMcp()
    t = make_tracker(mcp)
    assert t.fetch_issues_by_states([]) == [] and t.fetch_issues_by_ids([]) == []
    assert mcp.calls == []


def test_candidate_fetch_maps_to_list_issues_per_state():
    board = FakeLinearBoard()
    board.add("TAL-1")
    board.add("TAL-2", assignee="someone-else")
    board.add("TAL-3", labels=("other",))
    board.add("TAL-4", state="In Progress")
    issues = make_tracker(board.mcp).fetch_issues_by_states(["Todo", "In Progress"])
    assert [(i.identifier, i.dispatchable) for i in issues] == [
        ("TAL-1", True), ("TAL-2", False), ("TAL-4", True)]
    calls = board.mcp.called("list_issues")
    assert [c["state"] for c in calls] == ["Todo", "In Progress"]
    assert all(c["team"] == "TAL" and c["label"] == "agent" and c["limit"] == 50
               and set(c["fields"]) >= {"id", "uuid", "status", "labels", "assigneeId",
                                        "priority", "createdAt", "updatedAt"} for c in calls)
    assert board.mcp.called("get_user") == [{"query": "me"}]
    # Relations only for tickets assigned to the viewer; no comments for unparked tickets.
    assert [c["id"] for c in board.mcp.called("get_issue")] == ["uuid-TAL-1", "uuid-TAL-4"]
    assert all(c["includeRelations"] is True for c in board.mcp.called("get_issue"))
    assert board.mcp.called("list_comments") == []


def test_blocker_state_fetched_separately_and_cached():
    board = FakeLinearBoard()
    board.add("TAL-1", blocked_by=("TAL-9",))
    board.add("TAL-2", blocked_by=("TAL-9", "TAL-8"))
    board.add("TAL-9", state="Done", labels=())
    board.add("TAL-8", state="In Progress", labels=())
    issues = make_tracker(board.mcp).fetch_issues_by_states(["Todo"])
    assert [(i.identifier, i.dispatchable) for i in issues] == [("TAL-1", True),
                                                                ("TAL-2", False)]
    # blockedBy has id (the identifier) and title only; the state comes from get_issue.
    assert issues[1].blocked_by == (BlockerRef("TAL-9", "TAL-9", "Done"),
                                    BlockerRef("TAL-8", "TAL-8", "In Progress"))
    status_reads = [c for c in board.mcp.called("get_issue") if c.get("fields") == ["status"]]
    assert [c["id"] for c in status_reads] == ["TAL-9", "TAL-8"]  # TAL-9 read once


def test_missing_blocker_counts_as_blocking():
    board = FakeLinearBoard()
    board.add("TAL-1", blocked_by=("TAL-404",))
    assert not make_tracker(board.mcp).fetch_issues_by_states(["Todo"])[0].dispatchable


def test_pagination_follows_cursor_and_fails_without_one():
    mcp = FakeMcp().on("get_user", {"id": VIEWER}).on("get_issue", {"relations": {}})
    mcp.reply("list_issues", {"issues": [item(id="TAL-1", uuid="u1")], "hasNextPage": True,
                              "cursor": "c1"})
    mcp.reply("list_issues", {"issues": [item(id="TAL-2", uuid="u2")], "hasNextPage": False})
    issues = make_tracker(mcp).fetch_issues_by_states(["Todo"])
    assert [i.identifier for i in issues] == ["TAL-1", "TAL-2"]
    assert [c.get("cursor") for c in mcp.called("list_issues")] == [None, "c1"]
    mcp.reply("list_issues", {"issues": [], "hasNextPage": True})
    with pytest.raises(TrackerError) as e:
        make_tracker(mcp).fetch_issues_by_states(["Todo"])
    assert e.value.category == "tracker_pagination"


def test_state_list_omits_and_logs_malformed(logged):
    mcp = FakeMcp().on("get_user", {"id": VIEWER}).on("get_issue", {"relations": {}})
    mcp.on("list_issues", {"issues": [item(), item(id="TAL-8", uuid="u8", title=None)]})
    issues = make_tracker(mcp).fetch_issues_by_states(["Todo"])
    assert [i.identifier for i in issues] == ["TAL-7"]
    assert logged[0][0] == "linear_malformed_issue"


@pytest.mark.parametrize("prefix", ["uuid-", ""])
def test_id_refresh_maps_to_get_issue_and_omits_missing_and_other_teams(prefix):
    board = FakeLinearBoard()
    board.add("TAL-1", state="In Progress")
    board.add("ENG-5")
    issues = make_tracker(board.mcp).fetch_issues_by_ids(
        [prefix + ref for ref in ("TAL-1", "TAL-99", "ENG-5", "TAL-1")])
    assert [i.identifier for i in issues] == ["TAL-1"]
    first = board.mcp.called("get_issue")[0]
    assert first["includeRelations"] is True and "relations" in first["fields"]


def test_id_refresh_fails_on_malformed():
    mcp = FakeMcp().on("get_user", {"id": VIEWER}).on("get_issue", item(status=None))
    with pytest.raises(TrackerError) as e:
        make_tracker(mcp).fetch_issues_by_ids(["uuid-7"])
    assert e.value.category == "tracker_response"


def test_viewer_cached_and_explicit_assignee_skips_it():
    board = FakeLinearBoard(viewer="u-2")
    board.add("TAL-1", assignee="u-2")
    t = make_tracker(board.mcp)
    t.fetch_issues_by_states(["Todo"])
    t.fetch_issues_by_states(["Todo"])
    assert len(board.mcp.called("get_user")) == 1
    board2 = FakeLinearBoard(viewer="nobody")
    board2.add("TAL-1", assignee="u-2")
    issues = make_tracker(board2.mcp, provider={"assignee": "u-2"}).fetch_issues_by_states(
        ["Todo"])
    assert issues[0].dispatchable and board2.mcp.called("get_user") == []


def test_transport_errors_propagate():
    mcp = FakeMcp().on("get_user", {"id": VIEWER}).on(
        "get_issue", TrackerError("tracker_request", "x"))
    with pytest.raises(TrackerError) as e:
        make_tracker(mcp).fetch_issues_by_ids(["uuid-1"])
    assert e.value.category == "tracker_request"


# --- label-based waiting and wake through the adapter --------------------------------------------

def test_waiting_label_parks_until_a_newer_wake_comment():
    board = FakeLinearBoard()
    board.add("TAL-1", state="In Progress", labels=("agent", "waiting"))
    started = {"TAL-1": T0}
    t = make_tracker(board.mcp, turn_starts=started)
    board.comment("TAL-1", "🤖 agent (Codex, TAL-1)\nwaiting on runs", T0 + timedelta(seconds=1))
    board.comment("TAL-1", "🤖 experiment-run status\nrunning", T0 + timedelta(seconds=2))
    assert not t.fetch_issues_by_states(["In Progress"])[0].dispatchable
    assert board.mcp.called("list_comments")[0] == {"issueId": "uuid-TAL-1", "limit": 50,
                                                    "orderBy": "createdAt"}
    board.comment("TAL-1", "🤖 experiment-run\nSUCCEEDED", T0 + timedelta(seconds=3))
    assert t.fetch_issues_by_states(["In Progress"])[0].dispatchable


def test_in_review_wakes_on_tal_reply_across_comment_pages():
    board = FakeLinearBoard(page_size=2)
    board.add("TAL-1", state="In Review")
    for n in range(4):
        board.comment("TAL-1", f"🤖 agent (Codex, TAL-1)\nnote {n}", T0 - timedelta(minutes=n))
    t = make_tracker(board.mcp, turn_starts={"TAL-1": T0})
    assert not t.fetch_issues_by_states(["In Review"])[0].dispatchable
    board.comment("TAL-1", "Use option B.", T0 + timedelta(minutes=1))
    assert t.fetch_issues_by_states(["In Review"])[0].dispatchable
    assert len(board.mcp.called("list_comments")) >= 3


# --- writes (deviation 3) ------------------------------------------------------------------------

def test_prepare_dispatch_removes_waiting_label():
    board = FakeLinearBoard()
    board.add("TAL-1", state="In Progress", labels=("agent", "waiting"))
    t = make_tracker(board.mcp)
    fresh = t.prepare_dispatch(make_issue("TAL-1", id="uuid-TAL-1", state="In Progress",
                                          labels=("agent", "waiting")))
    assert board.mcp.called("save_issue") == [{"id": "uuid-TAL-1",
                                               "removeLabels": ["waiting"]}]
    assert fresh.labels == ("agent",) and fresh.state == "In Progress"


def test_prepare_dispatch_moves_in_review_to_in_progress():
    board = FakeLinearBoard()
    board.add("TAL-1", state="In Review")
    fresh = make_tracker(board.mcp).prepare_dispatch(
        make_issue("TAL-1", id="uuid-TAL-1", state="In Review"))
    assert board.mcp.called("save_issue") == [{"id": "uuid-TAL-1", "state": "In Progress"}]
    assert fresh.state == "In Progress"


def test_prepare_dispatch_leaves_unparked_alone():
    mcp = FakeMcp()
    issue = make_issue("TAL-1", state="In Progress")
    assert make_tracker(mcp).prepare_dispatch(issue) is issue
    assert mcp.calls == []


def test_escalate_posts_signed_comment_then_in_review():
    board = FakeLinearBoard()
    board.add("TAL-1", state="In Progress")
    make_tracker(board.mcp).escalate(make_issue("TAL-1", id="uuid-TAL-1"),
                                     "Question for Tal: ok?")
    assert [n for n, _ in board.mcp.calls] == ["save_comment", "save_issue"]
    body = board.mcp.called("save_comment")[0]["body"]
    assert body.startswith("🤖 symphony\n\n") and "Question for Tal: ok?" in body
    assert board.mcp.called("save_comment")[0]["issueId"] == "uuid-TAL-1"
    assert board.issues["TAL-1"]["status"] == "In Review"


def test_no_env_secrets():
    assert make_tracker(FakeMcp()).secret_environment_names() == []


# --- bridge-event: run-status comment and signed event comment ------------------------------------

def test_bridge_event_creates_then_updates_status_comment_in_place():
    board = FakeLinearBoard()
    board.add("TAL-5", state="In Progress")
    r1 = bridge_event(board.mcp, "TAL-5", {"run": "reg-1", "status": "**pilot / one**: running"})
    assert r1["status"] == "created"
    body = board.comments["TAL-5"][0]["body"]
    assert body.splitlines()[0] == "🤖 experiment-run status"
    assert "Run status: `reg-1`" in body and not is_wake_comment(
        LinearComment(T0, body))
    r2 = bridge_event(board.mcp, "TAL-5", {"run": "reg-1", "status": "**pilot / one**: completed"})
    assert r2 == {"issue": "TAL-5", "status_comment": r1["status_comment"], "status": "updated"}
    assert len(board.comments["TAL-5"]) == 1 and "completed" in board.comments["TAL-5"][0]["body"]
    assert board.mcp.called("save_comment")[-1] == {"id": r1["status_comment"],
                                                    "body": board.comments["TAL-5"][0]["body"]}
    r3 = bridge_event(board.mcp, "TAL-5", {"run": "reg-1", "status": "**pilot / one**: completed"})
    assert r3["status"] == "unchanged" and len(board.mcp.called("save_comment")) == 2
    bridge_event(board.mcp, "TAL-5", {"run": "reg-2", "status": "other run"})
    assert len(board.comments["TAL-5"]) == 2


def test_bridge_event_posts_event_once():
    board = FakeLinearBoard()
    board.add("TAL-5", state="In Progress", labels=("agent", "waiting"))
    event = {"event_id": "ev-1", "event": "🤖 experiment-run\n**pilot**: completed\n\nEvent: ev-1"}
    assert bridge_event(board.mcp, "TAL-5", event)["event"] == "posted"
    assert bridge_event(board.mcp, "TAL-5", event)["event"] == "exists"
    assert len(board.comments["TAL-5"]) == 1
    # Unsigned or marker-less bodies get both, so the event always wakes and dedupes.
    bridge_event(board.mcp, "TAL-5", {"event_id": "ev-2", "event": "failed"})
    body = board.comments["TAL-5"][1]["body"]
    assert classify_comment(body) == "bridge" and body.endswith("Event: ev-2")
    # Never touches state or labels.
    assert board.mcp.called("save_issue") == []
    assert board.issues["TAL-5"]["labels"] == ["agent", "waiting"]


def test_bridge_event_retry_after_lost_response_does_not_double_post():
    board = FakeLinearBoard()
    board.add("TAL-5")
    real = board.mcp.handlers["save_comment"]

    def lost(args):
        real(args)
        raise TrackerError("tracker_request", "connection reset")
    board.mcp.reply("save_comment", lost)
    event = {"run": "reg-1", "status": "running", "event_id": "ev-1", "event": "done"}
    with pytest.raises(TrackerError):
        bridge_event(board.mcp, "TAL-5", event)
    result = bridge_event(board.mcp, "TAL-5", event)
    assert result["status"] == "unchanged" and result["event"] == "posted"
    assert len(board.comments["TAL-5"]) == 2


@pytest.mark.parametrize("event", [[], {}, {"status": "x"}, {"run": "r", "status": 3},
                                   {"event": "x"}, {"event_id": "", "event": "x"}])
def test_bridge_event_rejects_bad_input(event):
    with pytest.raises(ValueError):
        bridge_event(FakeMcp(), "TAL-5", event)
