from datetime import datetime, timedelta, timezone

import pytest

from symphony import board, cli
from symphony.config import TrackerConfig

from fakes import FakeLinearBoard

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
CFG = TrackerConfig(kind="linear", provider={"team_key": "TAL", "waiting_label": "waiting"},
                    required_labels=("agent",), active_states=("Todo", "In Progress",
                                                               "In Review"),
                    terminal_states=("Done", "Canceled"))


def _board():
    b = FakeLinearBoard()
    b.add("TAL-1", state="In Review", title="Needs a decision")
    b.comment("TAL-1", "🤖 agent (Codex, TAL-1)\n\nQuestion for Tal: A or B?",
              NOW - timedelta(minutes=5))
    b.add("TAL-2", state="In Progress", labels=("agent", "waiting"), title="Waiting on runs")
    b.add("TAL-3", state="In Progress", title="Working")
    b.add("TAL-4", state="Done", title="Finished")
    b.add("TAL-9", state="In Review", labels=(), title="Not an agent ticket")
    return b


def test_board_groups_review_first_with_question_and_flags():
    out = board.render_board(_board().mcp, CFG, running=["TAL-3"], now=NOW)
    lines = out.splitlines()
    assert lines[0].startswith("In Review — needs you (1)")
    assert "TAL-1" in lines[1] and "Question for Tal: A or B?" in lines[2]
    assert "🤖" not in out
    assert "[waiting on runs]" in next(ln for ln in lines if "TAL-2" in ln)
    assert "[running]" in next(ln for ln in lines if "TAL-3" in ln)
    assert out.index("In Progress") < out.index("Done (last 5)") < out.index("TAL-4")
    assert "TAL-9" not in out


def test_show_prints_thread_oldest_first_with_authors():
    b = _board()
    b.comment("TAL-1", "1 is fine", NOW - timedelta(minutes=1))
    out = board.render_issue(b.mcp, "TAL-1", now=NOW)
    assert out.index("[agent, 5m ago]") < out.index("[Tal, 1m ago]")
    assert "Question for Tal" in out and "1 is fine" in out


def test_show_limits_comments():
    b = _board()
    for i in range(4):
        b.comment("TAL-3", f"note {i}", NOW - timedelta(minutes=10 - i))
    out = board.render_issue(b.mcp, "TAL-3", last=2, now=NOW)
    assert "showing last 2" in out and "note 3" in out and "note 1" not in out


def test_reply_posts_unsigned_and_refuses_signed_or_empty():
    b = _board()
    board.reply(b.mcp, "TAL-1", "  go with A ")
    assert b.comments["TAL-1"][-1]["body"] == "go with A"
    for bad in ("", "🤖 agent (Codex, TAL-1)\nhi"):
        with pytest.raises(ValueError):
            board.reply(b.mcp, "TAL-1", bad)


def test_cli_reply_and_board(tmp_path, monkeypatch, capsys):
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\ntracker:\n  kind: linear\n  provider:\n    team_key: TAL\n"
                  "  required_labels: [agent]\n---\nprompt\n")
    monkeypatch.setattr(cli, "_read_status", lambda: None)
    b = _board()
    assert cli.main(["reply", "tal-1", "go", "with", "A", "--workflow", str(wf)],
                    tools=b.mcp) == 0
    assert b.comments["TAL-1"][-1]["body"] == "go with A"
    assert cli.main(["board", "--workflow", str(wf)], tools=b.mcp) == 0
    assert "TAL-1" in capsys.readouterr().out
    assert cli.main(["reply", "TAL-1", "🤖 x", "--workflow", str(wf)], tools=b.mcp) == 1
