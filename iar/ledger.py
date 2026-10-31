#!/usr/bin/env python3
"""ledger: the only code in iar/ and bin/ that calls the tracker (br).

Other tools import this module or run the `iar/ledger` wrapper. A switch to
another tracker (bd, Linear) changes this file only.

The store is the one `br` finds: set BEADS_DIR (for Precog:
/Users/tal/.local/share/beads/precog/.beads). Set LEDGER_ACTOR to record an
actor on each call. Every call returns parsed JSON; a failure raises
LedgerError (CLI: message on stderr, exit 1).

CLI (output is JSON on stdout):
  ledger create TITLE [-t TYPE] [-p N] [-l a,b] [--parent ID] [-d TEXT | --description-file F]
                [--assignee A] [-s STATUS]                    -> issue (has "id")
  ledger comment ID (TEXT | -f FILE) [--author A]               -> comment
  ledger label ID [--add L]... [--remove L]...                  -> list of changes
  ledger dep ID DEPENDS_ON [-t blocks|parent-child|related] [--remove]
  ledger ready [-l LABEL]... [-t TYPE] [--limit N]              -> list of issues
  ledger blocked [-l LABEL] [-t TYPE]                           -> list of issues
  ledger show ID                                                -> issue with labels, deps, comments
  ledger update ID [-s STATUS] [--assignee A] [--claim] [--title T] [-p N] [--notes T]
                [--add-label L]... [--remove-label L]... [-d TEXT]  -> issue
  ledger close ID [--reason R]                                  -> issue
  ledger --self-test                                            (uses a scratch store)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

TIMEOUT_S = 30  # one tracker call; W10 uses 30 s per source


class LedgerError(RuntimeError):
    pass


def _br(*args):
    cmd = ["br", *args, "--json"]
    if os.environ.get("LEDGER_ACTOR"):
        cmd += ["--actor", os.environ["LEDGER_ACTOR"]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise LedgerError(f"br {args[0]}: no answer in {TIMEOUT_S}s")
    except FileNotFoundError:
        raise LedgerError("br: not found on PATH")
    if p.returncode != 0:
        msg = (p.stdout + p.stderr).strip()
        try:
            msg = json.loads(p.stdout)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise LedgerError(f"br {args[0]} failed (exit {p.returncode}): {msg}")
    out = p.stdout.strip()
    return json.loads(out) if out else None


def _opt(flag, value):
    return [] if value is None else [f"{flag}={value}"]  # "=" keeps values that start with "-" safe


def create(title, type=None, priority=None, labels=(), parent=None,
           description=None, assignee=None, status=None):
    return _br("create", f"--title={title}", *_opt("--type", type), *_opt("--priority", priority),
               *_opt("--labels", ",".join(labels) or None), *_opt("--parent", parent),
               *_opt("--description", description), *_opt("--assignee", assignee),
               *_opt("--status", status))


def comment(issue, text, author=None):
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
        f.write(text)
        f.flush()
        return _br("comments", "add", issue, "-f", f.name, *_opt("--author", author))


def label(issue, add=(), remove=()):
    out = []
    for name in add:
        out += _br("label", "add", issue, name) or []
    for name in remove:
        out += _br("label", "remove", issue, name) or []
    return out


def dep(issue, depends_on, type="blocks", remove=False):
    if remove:
        return _br("dep", "remove", issue, depends_on)
    return _br("dep", "add", issue, depends_on, f"--type={type}")


def ready(labels=(), type=None, limit=0):
    args = ["ready", f"--limit={limit}", *_opt("--type", type)]
    for name in labels:
        args.append(f"--label={name}")
    return _br(*args) or []


def blocked(label=None, type=None):
    return _br("blocked", *_opt("--label", label), *_opt("--type", type)) or []


def list_issues(labels=(), all=False):
    """Issues that have every label in `labels` (no comments); `all` includes closed ones."""
    out = _br("list", "--limit=0", *(["--all"] if all else []), *[f"--label={n}" for n in labels])
    return (out or {}).get("issues", []) if isinstance(out, dict) else out or []


def show(issue):
    out = _br("show", issue)
    return out[0] if isinstance(out, list) else out


def show_many(ids):
    """One `br show` for many ids (with comments). Fails if any id is missing."""
    if not ids:
        return []
    out = _br("show", *ids)
    return out if isinstance(out, list) else [out]


def list_issues(status=None, labels=()):
    """All issues (no comments). status: open, in_progress, closed or all; default excludes closed."""
    out = _br("list", "--limit=0", *_opt("--status", status), *[f"--label={x}" for x in labels]) or []
    return out if isinstance(out, list) else out.get("issues", [])


def update(issue, status=None, assignee=None, claim=False, title=None,
           priority=None, notes=None, add_labels=(), remove_labels=(), description=None):
    args = ["update", issue, *_opt("--status", status), *_opt("--assignee", assignee),
            *_opt("--title", title), *_opt("--priority", priority), *_opt("--notes", notes),
            *_opt("--description", description)]
    args += ["--claim"] if claim else []
    for name in add_labels:
        args.append(f"--add-label={name}")
    for name in remove_labels:
        args.append(f"--remove-label={name}")
    out = _br(*args)
    return out[0] if isinstance(out, list) and out else out


def close(issue, reason=None):
    out = _br("close", issue, *_opt("--reason", reason))
    return out[0] if isinstance(out, list) and out else out


def self_test():
    """Run every verb against a scratch store. Never touches the caller's store."""
    with tempfile.TemporaryDirectory() as root:
        env_before = os.environ.get("BEADS_DIR")
        os.environ["BEADS_DIR"] = os.path.join(root, ".beads")
        try:
            subprocess.run(["br", "init", "--prefix", "lt"], cwd=root, check=True,
                           capture_output=True, timeout=TIMEOUT_S)
            a = create("-asking bead", type="task", priority=1, labels=["x", "auto"],
                       description="line 1\nline 2")
            q = create("question", labels=["question"])
            assert a["id"].startswith("lt-") and sorted(a["labels"]) == ["auto", "x"], a
            assert show(a["id"])["description"] == "line 1\nline 2"

            c = comment(a["id"], "-reconcile: nudge attempt=1\nsecond line")
            assert c["text"] == "-reconcile: nudge attempt=1\nsecond line", c
            assert [x["text"] for x in show(a["id"])["comments"]] == [c["text"]]

            label(a["id"], add=["decision:auto"], remove=["x"])
            assert sorted(show(a["id"])["labels"]) == ["auto", "decision:auto"]

            assert {i["id"] for i in ready(labels=["auto"])} == {a["id"]}
            dep(a["id"], q["id"])  # q blocks a
            assert [i["id"] for i in blocked()] == [a["id"]]
            assert ready(labels=["auto"]) == []
            dep(a["id"], q["id"], remove=True)
            assert blocked() == [] and {i["id"] for i in ready()} == {a["id"], q["id"]}

            u = update(a["id"], status="in_progress", assignee="agent:t#1",
                       add_labels=["y"], remove_labels=["auto"], description="-new\nbody")
            s = show(a["id"])
            assert s["description"] == "-new\nbody", s
            assert u["id"] == a["id"] and s["status"] == "in_progress", u
            assert s["assignee"] == "agent:t#1" and sorted(s["labels"]) == ["decision:auto", "y"]

            assert close(q["id"], reason="moot")["status"] == "closed"
            assert show(q["id"])["status"] == "closed"
            assert [i["id"] for i in show_many([a["id"], q["id"]])] == [a["id"], q["id"]]
            assert show_many([a["id"]])[0]["comments"][0]["text"] == c["text"]
            assert {i["id"] for i in list_issues(status="all")} == {a["id"], q["id"]}
            assert list_issues(labels=["question"]) == []  # closed is excluded by default
            assert [i["id"] for i in list_issues(status="closed", labels=["question"])] == [q["id"]]

            try:
                show("lt-nope")
                raise AssertionError("show of a missing id did not fail")
            except LedgerError as e:
                assert "not found" in str(e).lower(), e
        finally:
            if env_before is None:
                os.environ.pop("BEADS_DIR", None)
            else:
                os.environ["BEADS_DIR"] = env_before
    print("ledger self-test: ok")


def main(argv=None):
    p = argparse.ArgumentParser(prog="ledger", description=__doc__.split("\n")[0])
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("create")
    s.add_argument("title")
    s.add_argument("-t", "--type")
    s.add_argument("-p", "--priority", type=int)
    s.add_argument("-l", "--labels", default="")
    s.add_argument("--parent")
    g = s.add_mutually_exclusive_group()
    g.add_argument("-d", "--description")
    g.add_argument("--description-file")
    s.add_argument("--assignee")
    s.add_argument("-s", "--status")

    s = sub.add_parser("comment")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("text", nargs="?")
    g.add_argument("-f", "--file")
    s.add_argument("--author")

    s = sub.add_parser("label")
    s.add_argument("id")
    s.add_argument("--add", action="append", default=[])
    s.add_argument("--remove", action="append", default=[])

    s = sub.add_parser("dep")
    s.add_argument("id")
    s.add_argument("depends_on")
    s.add_argument("-t", "--type", default="blocks")
    s.add_argument("--remove", action="store_true")

    s = sub.add_parser("ready")
    s.add_argument("-l", "--label", action="append", default=[])
    s.add_argument("-t", "--type")
    s.add_argument("--limit", type=int, default=0)

    s = sub.add_parser("blocked")
    s.add_argument("-l", "--label")
    s.add_argument("-t", "--type")

    s = sub.add_parser("show")
    s.add_argument("id")

    s = sub.add_parser("update")
    s.add_argument("id")
    s.add_argument("-s", "--status")
    s.add_argument("--assignee")
    s.add_argument("--claim", action="store_true")
    s.add_argument("--title")
    s.add_argument("-p", "--priority", type=int)
    s.add_argument("--notes")
    s.add_argument("--add-label", action="append", default=[])
    s.add_argument("--remove-label", action="append", default=[])
    s.add_argument("-d", "--description")

    s = sub.add_parser("close")
    s.add_argument("id")
    s.add_argument("--reason")

    a = p.parse_args(argv)
    if a.self_test:
        self_test()
        return 0
    if not a.cmd:
        p.print_help(sys.stderr)
        return 2
    try:
        if a.cmd == "create":
            desc = a.description
            if a.description_file:
                with open(a.description_file) as f:
                    desc = f.read()
            out = create(a.title, a.type, a.priority, [x for x in a.labels.split(",") if x],
                         a.parent, desc, a.assignee, a.status)
        elif a.cmd == "comment":
            text = a.text
            if a.file:
                with open(a.file) as f:
                    text = f.read()
            out = comment(a.id, text, a.author)
        elif a.cmd == "label":
            out = label(a.id, a.add, a.remove)
        elif a.cmd == "dep":
            out = dep(a.id, a.depends_on, a.type, a.remove)
        elif a.cmd == "ready":
            out = ready(a.label, a.type, a.limit)
        elif a.cmd == "blocked":
            out = blocked(a.label, a.type)
        elif a.cmd == "show":
            out = show(a.id)
        elif a.cmd == "update":
            out = update(a.id, a.status, a.assignee, a.claim, a.title, a.priority,
                         a.notes, a.add_label, a.remove_label, a.description)
        else:
            out = close(a.id, a.reason)
    except (LedgerError, OSError) as e:
        print(f"ledger: {e}", file=sys.stderr)
        return 1
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
