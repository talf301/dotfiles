#!/usr/bin/env python3
"""workflow: read iar/WORKFLOW.md (front matter + brief template) and render the brief.

  workflow.py --self-test       render the fixture bead and compare to fixtures/brief-golden.md
  workflow.py --write-golden    rewrite the golden file (after an intended template or include change)

WORKFLOW.md is `---`, the config, `---`, then the brief template. The config is JSON, which is
valid YAML, so the stdlib reads it: PyYAML is not installed for every python3 (launchd, brew).
The template has `{{var}}` (an unknown var fails the render) and `{{include <file next to WORKFLOW.md>}}`.
Env: IAR_WORKFLOW overrides the WORKFLOW.md path (tests).
"""

import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "fixtures", "brief-golden.md")
REQUIRED = ("tracker.kind", "tracker.beads_dir", "tracker.dispatch_label", "tracker.active_states",
            "tracker.terminal_states", "polling.interval_ms", "agent.max_concurrent",
            "agent.max_concurrent_by_state", "stall_after_min", "max_retry_backoff_ms",
            "compute_allowlist", "actions")
FIXTURE = {"bead": "precog-fix1", "title": "Fixture bead", "name": "precog-fix1: fixture bead",
           "attempt": 2, "slot": "/slots/precog/3", "base_sha": "0123456789abcdef0123456789abcdef01234567",
           "branch": "u/tfriedman/iar-precog-fix1", "branch_action": "create it at 0123456",
           "pr_group": "u/tfriedman/pr/fix",
           "done_when": "iar-run-tests.sh /slots/precog/3 tests/test_fix.py"}


class WorkflowError(Exception):
    pass


def load(path=None):
    """Return (config, template). Fails on a missing field or a non-boolean action switch."""
    path = path or os.environ.get("IAR_WORKFLOW") or os.path.join(HERE, "WORKFLOW.md")
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        raise WorkflowError(f"cannot read {path}: {e}")
    parts = text.split("\n---\n", 1)
    if not text.startswith("---\n") or len(parts) != 2:
        raise WorkflowError(f"{path}: no front matter between --- lines")
    try:
        config = json.loads(parts[0][4:])
        for key in REQUIRED:
            node = config
            for k in key.split("."):
                node = node[k]
    except (ValueError, KeyError, TypeError) as e:
        raise WorkflowError(f"{path}: bad front matter: {type(e).__name__}: {e}")
    if not all(isinstance(v, bool) for v in config["actions"].values()):
        raise WorkflowError(f"{path}: every actions switch must be true or false")
    return config, parts[1]


def render(values, path=None):
    """Expand includes (files next to WORKFLOW.md), then variables. Any token left fails."""
    path = path or os.environ.get("IAR_WORKFLOW") or os.path.join(HERE, "WORKFLOW.md")

    def include(m):
        try:
            with open(os.path.join(os.path.dirname(path), m.group(1))) as f:
                return f.read().strip()
        except OSError as e:
            raise WorkflowError(f"brief: include {m.group(1)}: {e}")

    def var(m):
        if m.group(1) not in values:
            raise WorkflowError(f"brief: unknown variable {{{{{m.group(1)}}}}}")
        return str(values[m.group(1)])
    text = re.sub(r"\{\{include ([\w.-]+)\}\}", include, load(path)[1])
    if "{{" in re.sub(r"\{\{\w+\}\}", "", text):
        raise WorkflowError("brief: malformed {{...}} in template or include")
    return re.sub(r"\{\{(\w+)\}\}", var, text)  # one pass: values are not templated


def self_test():
    config, template = load()
    assert config["actions"], "no action switches"  # reconcile.py --enable/--kill set them; load checks booleans
    for var in ("bead", "attempt", "base_sha", "slot", "branch"):
        assert "{{%s}}" % var in template, var
    # W9: the approved path with a PR group integrates and asks nothing; without a group it asks.
    ok, ask = template.split("   - **Approved, PR group is not `none`**", 1)[1].split("\n   - **", 2)[:2]
    assert ":::question" not in ok and "?" not in ok and "READY: integrated <sha>" in template, ok
    # W9b: integrate.sh (group lock, merge the group in, done-when, push) runs before the close.
    assert "iar/integrate.sh {{bead}} {{slot}} {{pr_group}}" in ok and ok.index("integrate.sh") < ok.index("br close"), ok
    assert "without a `pr:` label still asks" in ask and ":::question" in ask, ask
    out = render(FIXTURE)
    with open(GOLDEN) as f:
        assert out == f.read(), "render differs from fixtures/brief-golden.md (--write-golden if intended)"
    for inc in ("autonomy.md", "scope-self-review.md", "inbox-etiquette.md"):
        with open(os.path.join(HERE, inc)) as f:
            assert f.read().strip() in out, inc
    with tempfile.TemporaryDirectory() as d:
        def fails(text, values=FIXTURE):
            p = os.path.join(d, "W.md")
            with open(p, "w") as f:
                f.write(text)
            try:
                render(values, p)
            except WorkflowError as e:
                return str(e)
        head = "---\n" + json.dumps(config) + "\n---\n"
        assert "unknown variable {{nope}}" in fails(head + "{{bead}} {{nope}}\n")
        assert "unknown variable {{slot}}" in fails(head + "{{slot}}\n", {"bead": "x"})
        assert "malformed" in fails(head + "{{ bead }}\n")
        assert "include" in fails(head + "{{include no-such.md}}\n")
        with open(os.path.join(d, "inc.md"), "w") as f:
            f.write("included {{nope}}\n")
        assert "unknown variable {{nope}}" in fails(head + "{{include inc.md}}\n")
        assert "no front matter" in fails("{{bead}}\n")
        bad = dict(config, actions=dict(config["actions"], zombie="yes"))
        assert "true or false" in fails("---\n" + json.dumps(bad) + "\n---\nx\n")
        assert "bad front matter" in fails("---\n" + json.dumps({"tracker": {}}) + "\n---\nx\n")
    print("workflow self-test: ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    elif sys.argv[1:] == ["--write-golden"]:
        with open(GOLDEN, "w") as f:
            f.write(render(FIXTURE))
    else:
        sys.exit(__doc__)
