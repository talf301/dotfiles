"""§17.1 workflow loading, reload and strict prompt rendering (workflow.py)."""

import os
from pathlib import Path

import pytest

from fakes import make_issue
from symphony.errors import WorkflowError
from symphony.models import BlockerRef
from symphony.workflow import (
    DEFAULT_CONTINUATION,
    DEFAULT_PROMPT,
    WorkflowStore,
    load_workflow,
    render_prompt,
    resolve_workflow_path,
)


def bump(path: Path, text: str) -> None:
    """Rewrite and force a distinct mtime so stat-based detection sees the change."""
    st = path.stat()
    path.write_text(text)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# --- path selection ---------------------------------------------------------------------------

def test_explicit_path_wins(tmp_path):
    assert resolve_workflow_path(tmp_path / "x.md", Path("/elsewhere")) == tmp_path / "x.md"


def test_explicit_relative_path_is_relative_to_cwd(tmp_path):
    assert resolve_workflow_path("conf/W.md", tmp_path) == tmp_path / "conf/W.md"


def test_cwd_default_is_workflow_md(tmp_path):
    assert resolve_workflow_path(None, tmp_path) == tmp_path / "WORKFLOW.md"


# --- loader -----------------------------------------------------------------------------------

def test_front_matter_and_body_split(write_workflow):
    d = load_workflow(write_workflow())
    assert d.config["tracker"]["kind"] == "linear"
    assert d.config["tracker"]["provider"]["team_key"] == "TAL"
    assert d.prompt_template == "Work on {{ issue.identifier }}: {{ issue.title }}."
    assert d.path.is_absolute()


def test_no_front_matter_means_empty_config(write_workflow):
    d = load_workflow(write_workflow("\n  Just a prompt.\n\n"))
    assert d.config == {} and d.prompt_template == "Just a prompt."


def test_empty_front_matter_is_empty_map(write_workflow):
    d = load_workflow(write_workflow("---\n---\nbody\n"))
    assert d.config == {} and d.prompt_template == "body"


def test_missing_file_typed_error(tmp_path):
    with pytest.raises(WorkflowError) as e:
        load_workflow(tmp_path / "nope.md")
    assert e.value.category == "missing_workflow_file"


def test_invalid_yaml_typed_error(write_workflow):
    with pytest.raises(WorkflowError) as e:
        load_workflow(write_workflow("---\ntracker: [unclosed\n---\nbody\n"))
    assert e.value.category == "workflow_parse_error"


def test_unclosed_front_matter_typed_error(write_workflow):
    with pytest.raises(WorkflowError) as e:
        load_workflow(write_workflow("---\ntracker: {}\nbody\n"))
    assert e.value.category == "workflow_parse_error"


@pytest.mark.parametrize("fm", ["- a\n- b", "just a string", "42"])
def test_non_map_front_matter_typed_error(write_workflow, fm):
    with pytest.raises(WorkflowError) as e:
        load_workflow(write_workflow(f"---\n{fm}\n---\nbody\n"))
    assert e.value.category == "workflow_front_matter_not_a_map"


# --- prompt rendering -------------------------------------------------------------------------

def test_renders_issue_and_attempt():
    issue = make_issue("TAL-7", title="Fix it", labels=("agent", "repo:x"),
                       blocked_by=(BlockerRef("b1", "TAL-3", "Done"),))
    tpl = ("{{ issue.identifier }} {{ issue.title }} a={{ attempt }} "
           "{% for l in issue.labels %}[{{ l }}]{% endfor %} "
           "{% for b in issue.blocked_by %}{{ b.identifier }}:{{ b.state }}{% endfor %}")
    assert render_prompt(tpl, issue, 2) == "TAL-7 Fix it a=2 [agent][repo:x] TAL-3:Done"


def test_attempt_null_on_first_run():
    out = render_prompt("{% if attempt %}retry {{ attempt }}{% else %}first{% endif %}",
                        make_issue(), None)
    assert out == "first"


def test_retry_kind_defined_and_passed():
    tpl = "{% if retry_kind %}{{ retry_kind }}{% else %}none{% endif %}"
    assert render_prompt(tpl, make_issue(), None) == "none"
    assert render_prompt(tpl, make_issue(), 1, retry_kind="continuation") == "continuation"


def test_extra_variables():
    assert render_prompt("{{ turn_number }}/{{ max_turns }}", make_issue(), 1,
                         turn_number=3, max_turns=20) == "3/20"


@pytest.mark.parametrize("tpl", ["{{ nope }}", "{{ issue.nope }}", "{% if nope %}x{% endif %}"])
def test_unknown_variable_fails(tpl):
    with pytest.raises(WorkflowError) as e:
        render_prompt(tpl, make_issue(), None)
    assert e.value.category == "template_render_error"


def test_unknown_filter_fails():
    with pytest.raises(WorkflowError) as e:
        render_prompt("{{ issue.title | no_such_filter }}", make_issue(), None)
    assert e.value.category in ("template_render_error", "template_parse_error")


def test_parse_error_typed():
    with pytest.raises(WorkflowError) as e:
        render_prompt("{% if %}", make_issue(), None)
    assert e.value.category == "template_parse_error"


def test_empty_template_uses_default():
    assert render_prompt("", make_issue(), None) == DEFAULT_PROMPT
    assert render_prompt("  \n", make_issue(), None) == DEFAULT_PROMPT


def test_default_continuation_renders():
    assert "TAL-5" in render_prompt(DEFAULT_CONTINUATION, make_issue("TAL-5"), 1)


# --- WorkflowStore reload ---------------------------------------------------------------------

def test_load_initial_and_current(write_workflow):
    store = WorkflowStore(write_workflow(), env=os.environ)
    defn, cfg = store.load_initial()
    assert store.current == (defn, cfg)
    assert cfg.tracker.kind == "linear"
    assert store.last_error is None


def test_load_initial_raises_on_missing(tmp_path):
    with pytest.raises(WorkflowError) as e:
        WorkflowStore(tmp_path / "WORKFLOW.md").load_initial()
    assert e.value.category == "missing_workflow_file"


def test_unchanged_file_is_not_reloaded(write_workflow):
    store = WorkflowStore(write_workflow())
    store.load_initial()
    assert store.reload_if_changed() is False


def test_change_detected_and_reapplied(write_workflow):
    from conftest import MINIMAL_WORKFLOW
    p = write_workflow()
    store = WorkflowStore(p)
    store.load_initial()
    bump(p, MINIMAL_WORKFLOW.replace("workspace:", "polling:\n  interval_ms: 1234\nworkspace:")
         .replace("Work on", "Now work on"))
    assert store.reload_if_changed() is True
    defn, cfg = store.current
    assert cfg.poll_interval_ms == 1234
    assert defn.prompt_template.startswith("Now work on")
    assert store.reload_if_changed() is False


def test_touch_without_content_change_is_not_a_reload(write_workflow):
    from conftest import MINIMAL_WORKFLOW
    p = write_workflow()
    store = WorkflowStore(p)
    store.load_initial()
    bump(p, MINIMAL_WORKFLOW)
    assert store.reload_if_changed() is False


@pytest.mark.parametrize("bad", [
    "---\ntracker: [unclosed\n---\nbody\n",            # YAML error
    "---\n- list\n---\nbody\n",                        # not a map
    "---\nagent:\n  max_turns: 0\n---\nbody\n",        # config error
])
def test_invalid_reload_keeps_last_good(write_workflow, bad, monkeypatch):
    logged = []
    monkeypatch.setattr("symphony.log.log", lambda event, level="info", **f:
                        logged.append((event, level, f)))
    p = write_workflow()
    store = WorkflowStore(p)
    good = store.load_initial()
    bump(p, bad)
    assert store.reload_if_changed() is False
    assert store.current == good
    assert store.last_error is not None
    assert logged and logged[-1][0] == "workflow_reload" and logged[-1][1] == "error"
    # Not re-reported while the file is unchanged.
    n = len(logged)
    assert store.reload_if_changed() is False and len(logged) == n


def test_deleted_file_keeps_last_good_and_recovers(write_workflow):
    from conftest import MINIMAL_WORKFLOW
    p = write_workflow()
    store = WorkflowStore(p)
    good = store.load_initial()
    p.unlink()
    assert store.reload_if_changed() is False
    assert store.last_error.category == "missing_workflow_file"
    assert store.current == good
    p.write_text(MINIMAL_WORKFLOW.replace("Work on", "Back to"))
    assert store.reload_if_changed() is True
    assert store.last_error is None
    assert store.current[0].prompt_template.startswith("Back to")


def test_fix_after_error_clears_error(write_workflow):
    from conftest import MINIMAL_WORKFLOW
    p = write_workflow()
    store = WorkflowStore(p)
    store.load_initial()
    bump(p, "---\n- list\n---\n")
    store.reload_if_changed()
    assert store.last_error is not None
    bump(p, MINIMAL_WORKFLOW)  # same as last good content
    assert store.reload_if_changed() is False
    assert store.last_error is None


def test_reload_never_raises_on_unexpected_error(write_workflow, monkeypatch):
    from conftest import MINIMAL_WORKFLOW
    p = write_workflow()
    store = WorkflowStore(p)
    store.load_initial()

    def boom(defn, env):
        raise RuntimeError("unexpected")
    monkeypatch.setattr("symphony.config.build_config", boom)
    bump(p, MINIMAL_WORKFLOW + "\nmore\n")
    assert store.reload_if_changed() is False
    assert store.last_error is not None
