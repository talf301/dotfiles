#!/usr/bin/env python3
"""inbox-migrate: one Inbox project per repo (precog-am8w W16).

A project is stray when its root is a treehouse slot, a ~/.worktrees checkout or a tmp folder, or when it
is a second project of a repo that has a canonical one (precog -> "Precog agents", dotfiles -> ~/dotfiles).
Each unresolved thread in a stray project gets one action:
  question  an unanswered :::question, unless the bead its subject names is closed: `iar/ask` files a question
            bead under that bead (no bead named: under the epic precog-am8w) and posts it to the canonical
            project, then the stray thread is resolved
  moot      no unanswered question, or the named bead is closed: the thread is resolved
Threads stay readable in the Inbox as Archived. The project folders stay: the Inbox has no tool to delete a
project, and a project with no unresolved thread has nothing in the list.

  inbox-migrate.py             dry run: list stray threads, the planned actions, before/after counts
  inbox-migrate.py --apply     do the actions, then write the before/after counts as a comment on precog-mmjm
                               (rerun-safe: resolved threads drop out, ask reuses its bead)
  inbox-migrate.py --self-test fake Inbox files, fake Inbox MCP, scratch br store
"""

import contextlib
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile

IAR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, IAR)
import ledger  # noqa: E402
import question  # noqa: E402
import reconcile  # noqa: E402

HOME = os.path.expanduser("~")
CANONICAL = {"precog": question.PROJECT, "precog-agents": question.PROJECT, "dotfiles": f"{HOME}/dotfiles"}
STRAY_PREFIXES = (f"{HOME}/.treehouse/", f"{HOME}/.worktrees/", "/tmp/", "/private/tmp/", "/var/folders/",
                  "/private/var/folders/")
BEAD = "precog-mmjm"  # W16: --apply writes its counts here
ORPHAN_PARENT = "precog-am8w"  # the parent of a question whose thread names no bead
SUBJECT_BEAD = re.compile(r"^(?:precog-)?([a-z0-9]{3,8}(?:\.\d+)*)[: ]")


def projects():
    """{project id: record} from each project's event log (last record wins)."""
    out = {}
    for path in glob.glob(f"{reconcile.INBOX}/projects/*/project.json"):
        with open(path) as f:
            for row in reconcile.strict_lines(f.read()):
                out[row["id"]] = row["record"]
    return out


def is_stray(p):
    root = p["root"].rstrip("/") + "/"
    canon = CANONICAL.get(p["name"])
    return root.startswith(STRAY_PREFIXES) or (canon is not None and p["root"] != canon)


def home_of(p):
    """The canonical project root of a project's repo (the name, or the repo folder under ~/.worktrees);
    the precog one when the repo has none."""
    rel = p["root"].removeprefix(f"{HOME}/.worktrees/")
    repo = rel.split("/")[0] if rel != p["root"] else p["name"]
    return CANONICAL.get(p["name"]) or CANONICAL.get(repo) or os.environ.get("IAR_INBOX_PROJECT", question.PROJECT)


def bead_of(subject):
    """The bead a thread subject names (`precog-x: ...`, `x: ...`, `precog-x W12: ...`), else None."""
    m = SUBJECT_BEAD.match(subject or "")
    if not m:
        return None
    try:
        return ledger.show(f"precog-{m[1]}")
    except ledger.LedgerError:
        return None


def options_of(q):
    """2 or 3 one-line options from the Inbox choices, and the default (the recommended one, else the first)."""
    opts, default = [], None
    for c in (q.get("parsed") or {}).get("choices") or []:
        label = (c.get("label") or "").strip().splitlines()
        label = label[0][:100].strip() if label else ""
        if label and label not in opts and len(opts) < 3:
            opts.append(label)
            if c.get("recommended") and default is None:
                default = label
    for pad in ("Yes", "No"):
        if len(opts) < 2 and pad not in opts:
            opts.append(pad)
    return opts, default or opts[0]


def plan():
    """(rows, before, after): one row per unresolved stray thread; unresolved threads per project root."""
    messages, questions, first_seq = reconcile.inbox_records()
    projs = projects()
    ids = {}
    for m in messages.values():
        ids.setdefault(m["thread_id"], set()).add(m["id"])
    rows, before, after = [], {}, {}
    for t in reconcile.inbox_threads(messages, questions, first_seq):
        root = messages.get(t["id"]) or next(m for m in messages.values() if m["thread_id"] == t["id"])
        p = projs.get(root["project_id"]) or {"root": root["project_id"], "name": "?", "key": "?"}
        before[p["root"]] = before.get(p["root"], 0) + 1
        if not is_stray(p):
            after[p["root"]] = after.get(p["root"], 0) + 1
            continue
        open_qs = [q for q in questions.values() if q["message_id"] in ids[t["id"]] and q.get("answer") is None
                   and not q.get("action_result") and not q.get("superseded_by")]
        bead = bead_of(t["subject"]) if open_qs else None
        action = "moot" if not open_qs or (bead and bead["status"] == "closed") else "question"
        rows.append({"thread": t["id"], "project": p, "subject": t["subject"], "session": t["session"],
                     "quiet": t["quiet"], "bead": bead["id"] if bead else ORPHAN_PARENT if open_qs else None, "action": action,
                     "questions": open_qs if action == "question" else []})
        if action == "question":  # ponytail: one new thread per question; the Inbox may group them by thread name
            after[home_of(p)] = after.get(home_of(p), 0) + len(open_qs)
    return rows, before, after


def ask(row, q):
    """File the question bead through iar/ask; returns its id."""
    opts, default = options_of(q)
    prompt = (q.get("prompt") or "").strip()
    text = (prompt.splitlines() or ["(no prompt)"])[0][:200]
    context = "\n\n".join(x for x in [prompt if prompt != text else "", (q.get("parsed") or {}).get("context") or "",
                                      f"Moved by inbox-migrate (W16) from Inbox thread {row['thread']} in the stray "
                                      f"project {row['project']['root']}."] if x)
    argv = [row["bead"], text, *[a for o in opts for a in ("-o", o)], "--default", default, "--reversible", "no",
            "--gated", "no", "--blocks", "no", "--context", context, "--project", home_of(row["project"])]
    session = row["session"] or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session:
        argv += ["--session", session]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = question.main_ask(argv)
    if rc != 0:
        raise RuntimeError(f"ask exit {rc}: {err.getvalue().strip()}")
    return out.getvalue().strip()


def run(apply):
    rows, before, after = plan()
    failed = 0
    for r in rows:
        note = "" if r["quiet"] else " (unread)"
        print(f"{r['action']:8} {r['thread']} {r['project']['root']} | {r['subject'][:80]}{note}")
        for q in r["questions"]:
            print(f"         question -> bead under {r['bead']}: {(q.get('prompt') or '').strip()[:80]}")
        if not apply:
            continue
        try:
            for q in r["questions"]:
                print(f"         filed {ask(r, q)}")
            reconcile.inbox("resolve_message", {"message_id": r["thread"], "resolved": True})
            print("         resolved")
        except (RuntimeError, ledger.LedgerError, OSError, subprocess.SubprocessError) as e:
            failed += 1
            print(f"         FAILED: {e}")
    if apply:  # the real after: the Inbox files once the actions ran
        _, after, _ = plan()
    lines = [f"Unresolved Inbox threads per project (before -> after{'' if apply else ', planned'}):"]
    lines += [f"  {before.get(root, 0):3} -> {after.get(root, 0):3}  {root}" for root in sorted(set(before) | set(after))]
    lines += [f"  {sum(before.values()):3} -> {sum(after.values()):3}  total; stray threads: {len(rows)}, "
              f"question: {sum(r['action'] == 'question' for r in rows)}, moot: {sum(r['action'] == 'moot' for r in rows)}"
              f"{f', failed: {failed}' if failed else ''}"]
    print("\n" + "\n".join(lines))
    if apply:
        ledger.comment(BEAD, "inbox-migrate --apply\n" + "\n".join(lines))
    return 1 if failed else 0


def self_test():
    global BEAD, ORPHAN_PARENT
    saved_ids = BEAD, ORPHAN_PARENT
    with tempfile.TemporaryDirectory() as d:
        saved_env = {k: os.environ.get(k) for k in ("BEADS_DIR", "IAR_INBOX_CMD", "IAR_STATE_DIR", "IAR_INBOX_PROJECT")}
        saved_inbox = reconcile.INBOX
        try:
            os.environ.pop("IAR_INBOX_PROJECT", None)
            os.environ.update(BEADS_DIR=os.path.join(d, ".beads"), IAR_STATE_DIR=d)
            subprocess.run(["br", "init", "--prefix", "precog"], cwd=d, check=True, capture_output=True)
            open_bead = ledger.create("open work", type="task")["id"]
            closed_bead = ledger.create("done work", type="task")["id"]
            ledger.close(closed_bead)
            BEAD, ORPHAN_PARENT = ledger.create("W16", type="task")["id"], ledger.create("epic", type="epic")["id"]
            log, fake = os.path.join(d, "inbox.log"), os.path.join(d, "fake-inbox")
            with open(fake, "w") as fh:
                fh.write(f"#!/bin/sh\ncat >> '{log}'\n"
                         """echo '{"jsonrpc":"2.0","id":2,"result":{"structuredContent":{"thread_id":"msg_new"}}}'\n""")
            os.chmod(fake, 0o755)
            os.environ["IAR_INBOX_CMD"] = fake
            reconcile.INBOX = os.path.join(d, "inbox")
            seq = iter(range(1, 1000))

            def write(key, kind, rows):
                os.makedirs(f"{reconcile.INBOX}/projects/{key}", exist_ok=True)
                with open(f"{reconcile.INBOX}/projects/{key}/{kind}", "a") as fh:
                    for r in rows:
                        fh.write(json.dumps({"v": 1, "seq": next(seq), "kind": "x", "id": r["id"], "record": r}) + "\n")

            def project(key, name, root):
                write(key, "project.json", [{"id": f"prj_{key}", "key": key, "name": name, "root": root}])

            def thread(key, tid, subject, resolved=False):
                write(key, "messages.jsonl", [{"id": tid, "project_id": f"prj_{key}", "thread_id": tid, "subject": subject,
                                               "author": {"kind": "agent", "session": "s-1"}, "body": "b",
                                               "created_at": f"2026-10-01T00:00:{next(seq):02d}Z",
                                               "resolved_at": "2026-10-02T00:00:00Z" if resolved else None}])

            def ask_q(key, tid, prompt, choices):
                write(key, "questions.jsonl", [{"id": f"{tid}/q", "message_id": tid, "prompt": prompt, "answer": None,
                                                "parsed": {"context": "ctx", "choices": choices}}])

            project("canon", "precog-agents", question.PROJECT)
            thread("canon", "m-keep", "keep me")
            project("slot", "precog", f"{HOME}/.treehouse/precog-x/1/precog")
            thread("slot", "m-q", f"{open_bead.removeprefix('precog-')}: which way?")
            ask_q("slot", "m-q", "Which way?\nmore words", [{"label": "Left\nlong"}, {"label": "Right", "recommended": True},
                                                            {"label": "Up"}, {"label": "Down"}])
            thread("slot", "m-closed", f"{closed_bead}: old question")
            ask_q("slot", "m-closed", "Old?", [{"label": "A"}, {"label": "B"}])
            thread("slot", "m-gone", "gone", resolved=True)
            project("tmp", "tmp", "/private/tmp")
            thread("tmp", "m-probe", "probe")
            thread("tmp", "m-ask", "Which port?")  # an open question with no bead: filed under the epic
            ask_q("tmp", "m-ask", "Port?", [{"label": "80"}, {"label": "443"}])
            project("main", "precog", f"{HOME}/Documents/precog")
            thread("main", "m-main", "a note")
            project("dots", "w16", f"{HOME}/.worktrees/dotfiles/u/x/w16")  # a dotfiles question goes to ~/dotfiles
            thread("dots", "m-dots", f"{open_bead}: dotfiles question")
            ask_q("dots", "m-dots", "Dots?", [{"label": "A"}, {"label": "B"}])
            project("other", "other-repo", f"{HOME}/src/other-repo")
            thread("other", "m-other", "other repo note")

            rows, before, after = plan()
            got = {r["thread"]: (r["action"], r["bead"]) for r in rows}
            assert got == {"m-q": ("question", open_bead), "m-closed": ("moot", closed_bead), "m-probe": ("moot", None),
                           "m-main": ("moot", None), "m-ask": ("question", ORPHAN_PARENT),
                           "m-dots": ("question", open_bead)}, got
            assert before[f"{HOME}/.treehouse/precog-x/1/precog"] == 2 and sum(before.values()) == 8, before
            assert after == {question.PROJECT: 3, f"{HOME}/dotfiles": 1, f"{HOME}/src/other-repo": 1}, after
            q = next(r for r in rows if r["questions"])["questions"][0]
            assert options_of(q) == (["Left", "Right", "Up"], "Right"), options_of(q)
            assert options_of({"parsed": {"choices": []}}) == (["Yes", "No"], "Yes")

            with contextlib.redirect_stdout(io.StringIO()) as out:
                assert run(apply=False) == 0
            assert "8 ->   5  total" in out.getvalue() and not os.path.exists(log), out.getvalue()

            with contextlib.redirect_stdout(io.StringIO()) as out:
                assert run(apply=True) == 0, out.getvalue()
            with open(log) as fh:
                calls = [json.loads(x)["params"] for x in fh.read().splitlines() if '"tools/call"' in x]
            resolved = sorted(c["arguments"]["message_id"] for c in calls if c["name"] == "resolve_message")
            assert resolved == ["m-ask", "m-closed", "m-dots", "m-main", "m-probe", "m-q"], resolved
            sends = [c["arguments"] for c in calls if c["name"] == "send_message"]
            assert sorted(x["project_path"] for x in sends) == sorted([question.PROJECT] * 2 + [f"{HOME}/dotfiles"]), sends
            assert [k["title"] for k in ledger.show(ORPHAN_PARENT)["dependents"]] == \
                [f"Q({question.short(kid)}): Port?" for kid in [ledger.show(ORPHAN_PARENT)["dependents"][0]["id"]]]
            assert ledger.show(BEAD)["comments"][0]["text"].startswith("inbox-migrate --apply\nUnresolved")
            kids = [k for k in ledger.show(open_bead)["dependents"] if k["title"].startswith("Q(")]
            assert sorted(k["title"].split(": ", 1)[1] for k in kids) == ["Dots?", "Which way?"], kids
            kids = [k for k in kids if k["title"].endswith("Which way?")]
            body = question.parse_body(ledger.show(kids[0]["id"])["description"])
            assert body["options"] == ["Left", "Right", "Up"] and body["default"] == "Right", body
            assert "m-q" in body["context"] and body["session"] == "s-1", body

            # Rerun (Inbox files unchanged by the fake): ask reuses the bead, resolve is repeated harmlessly.
            with contextlib.redirect_stdout(io.StringIO()):
                assert run(apply=True) == 0
            assert len([k for k in ledger.show(open_bead)["dependents"] if k["title"].startswith("Q(")]) == 2

            os.environ["IAR_INBOX_CMD"] = "false"  # Inbox down: failures are counted, the thread stays
            with contextlib.redirect_stdout(io.StringIO()) as out:
                assert run(apply=True) == 1
            assert "failed: 6" in out.getvalue(), out.getvalue()
        finally:
            reconcile.INBOX = saved_inbox
            BEAD, ORPHAN_PARENT = saved_ids
            for k, v in saved_env.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    print("inbox-migrate self-test: ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--self-test"]:
        self_test()
        sys.exit(0)
    if args not in ([], ["--apply"]):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(run(apply=args == ["--apply"]))
