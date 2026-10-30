#!/usr/bin/env python3
"""question: question beads. `iar/ask` files a question, `iar/answer` records its answer.

Spec: precog-am8w (Concepts: Question bead; State machines: Question bead).

  ask ASKING_ID QUESTION -o A -o B [-o C] --default A --reversible yes|no --gated yes|no
      --blocks yes|no [--deadline 4h|30m|ISO-UTC] [--context TEXT] [--session SID]
      [--project PATH] [--thread NAME]
    Creates (or, for the same asking bead and question, reuses) the question bead, adds the
    blocks edge when --blocks yes, and posts the Inbox :::question. Prints the question id.
    Exit 0: posted. Exit 3: the bead exists with label inbox:pending (Inbox post failed; rerun
    or let the reconciler post). Exit 1: tracker error. Exit 2: bad arguments.

  answer QID TEXT [--at ISO-UTC] [--source NAME]
  answer QID --timeout
    The first valid answer wins: comment `answer: <text>` + label `answered` (+ `answer-freetext`
    when TEXT is not an option). Later answers are `late-answer:` comments + label `late-answer`.
    A human answer recorded (--at) before an undelivered timeout answer wins over it.
    --timeout writes `answer: <default> (timeout)` only when reversible: yes, gated: no and the
    deadline has passed; it labels the asking bead `decision:auto`.

State labels on the question bead: `inbox:pending`, `answered`, `delivered` (set by the
reconciler, W13), then the bead is closed (`moot` when the asking bead closed first).
The tracker is reached only through ledger.py. The Inbox command is `plannotator inbox mcp`;
IAR_INBOX_CMD overrides it (self-test). IAR_INBOX_PROJECT overrides the Inbox project.
One lock file, $IAR_STATE_DIR/question.lock (default ~/.local/state/iar), serializes the
find-or-create of `ask` and the read-and-write of `answer`.

  question.py --self-test   (scratch store and a fake Inbox)
"""

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger  # noqa: E402

PROJECT = "/Users/tal/.local/share/precog/Precog agents"  # inbox-etiquette.md
DEFAULT_DEADLINE = dt.timedelta(hours=4)
INBOX_TIMEOUT_S = 20
FIELDS = ("question", "asking", "session", "blocks", "default", "reversible", "gated",
          "deadline", "inbox_thread")


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


@contextlib.contextmanager
def locked():
    # ponytail: one global lock for all questions; per-question locks if it ever contends.
    d = os.environ.get("IAR_STATE_DIR") or os.path.expanduser("~/.local/state/iar")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "question.lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def short(bead_id):
    return bead_id.split("-", 1)[-1]


# --- body: `key: value` lines, `option:` repeated, then a blank line and free context ---

def render_body(f, options, context):
    lines = [f"{k}: {f[k]}" for k in FIELDS] + [f"option: {o}" for o in options]
    return "\n".join(lines) + ("\n\n" + context if context else "")


def parse_body(desc):
    head, _, context = (desc or "").partition("\n\n")
    f, options = {}, []
    for line in head.splitlines():
        k, _, v = line.partition(": ")
        if k == "option":
            options.append(v)
        elif k in FIELDS:
            f[k] = v
    f["options"], f["context"] = options, context
    return f


# --- Inbox ---

def inbox_post(qid, f, project, thread):
    blocks = [f["question"], ""]
    if f["context"]:
        blocks += [f["context"], ""]
    if f["gated"] == "yes":
        when = "Gated: no deadline, it waits for you."
    elif f["reversible"] == "yes":
        when = f"If no answer by {f['deadline']}, the default ({f['default']}) applies."
    else:
        when = f"Not reversible: no default applies; re-pinged at {f['deadline']}."
    blocks += [f"Question bead {qid}, asked by {f['asking']}. {when}"]
    if f["blocks"] == "yes":
        blocks += [f"Holds up: {f['asking']}"]
    if f["reversible"] == "no":
        blocks += ["Reversible: no — the asking agent declared it cannot be undone"]
    blocks += [f"Recommended: {f['default']}", ""] + [f"- [ ] {o}" for o in f["options"]]
    body = ":::question\n" + "\n".join(blocks) + "\n:::\n"
    if f["session"]:
        body += f"\n[Open session](claudego://s/{f['session']})\n"
    args = {"body": body, "subject": f"{qid}: {f['question']}"[:120], "thread": thread,
            "project_path": project, "idempotency_key": qid}
    if f["session"]:
        args["agent_session"] = f["session"]
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "iar-ask", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "send_message", "arguments": args}}]
    cmd = shlex.split(os.environ.get("IAR_INBOX_CMD", "plannotator inbox mcp"))
    try:
        p = subprocess.run(cmd, input="".join(json.dumps(m) + "\n" for m in msgs),
                           capture_output=True, text=True, timeout=INBOX_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Inbox not reachable: {e}")
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") != 2:
            continue
        r = m.get("result") or {}
        thread_id = (r.get("structuredContent") or {}).get("thread_id")
        if m.get("error") or r.get("isError") or not thread_id:
            raise RuntimeError(f"Inbox refused send_message: {json.dumps(m)[:400]}")
        return thread_id
    raise RuntimeError(f"Inbox gave no answer (exit {p.returncode}): {p.stderr.strip()[:400]}")


# --- ask ---

def find_open(asking, title):
    for d in ledger.show(asking).get("dependents") or []:
        if d.get("dependency_type") == "parent-child" and d["title"] == title \
                and d["status"] != "closed":
            return ledger.show(d["id"])
    return None


def ask(a):
    if a.gated == "yes" and a.deadline:
        raise ValueError("a gated question has no deadline")
    if a.default not in a.option:
        raise ValueError(f"--default must be one of the options {a.option}")
    if not 2 <= len(a.option) <= 3 or len(set(a.option)) != len(a.option):
        raise ValueError("give 2 or 3 different options")
    if "\n" in a.question or not a.question.strip() or any("\n" in o for o in a.option):
        raise ValueError("the question and each option are one non-empty line")
    deadline = ""
    if a.gated == "no":
        m = re.fullmatch(r"(\d+)([hm])", a.deadline or "")
        if m:
            unit = "hours" if m[2] == "h" else "minutes"
            deadline = iso(now() + dt.timedelta(**{unit: int(m[1])}))
        elif a.deadline:
            deadline = iso(parse_time(a.deadline))
        else:
            deadline = iso(now() + DEFAULT_DEADLINE)

    asking = ledger.show(a.asking)
    title = f"Q({short(a.asking)}): {a.question}"
    with locked():
        q = find_open(a.asking, title)
        if q is None:
            f = {"question": a.question, "asking": a.asking, "session": a.session or "",
                 "blocks": a.blocks, "default": a.default, "reversible": a.reversible,
                 "gated": a.gated, "deadline": deadline, "inbox_thread": "pending"}
            # inbox:pending from birth: a crash before the post leaves the bead for the reconciler.
            q = ledger.create(title, type="task", parent=a.asking,
                              labels=["question", "no-swarm", "inbox:pending"],
                              description=render_body(f, a.option, a.context or ""))
            q["labels"] = ["question", "no-swarm", "inbox:pending"]
    qid = q["id"]
    f = parse_body(q.get("description"))
    if f.get("blocks") == "yes":
        ledger.dep(a.asking, qid)  # idempotent: br reports already_exists
    print(qid)
    if "inbox:pending" not in (q.get("labels") or []):
        return 0
    thread = a.thread or f"{short(a.asking)}: {asking['title']}"
    try:
        thread_id = inbox_post(qid, f, a.project, thread)
    except RuntimeError as e:
        print(f"ask: {qid} filed with inbox:pending; {e}", file=sys.stderr)
        return 3
    f["inbox_thread"] = thread_id
    ledger.update(qid, description=render_body(f, f["options"], f["context"]),
                  remove_labels=["inbox:pending"])
    return 0


# --- answer ---

META = re.compile(r"\n-- at: (\S+) source: (\S+)\Z")  # last line of an answer comment


def answers(q):
    """`answer:` comments as dicts (text, at, source), oldest recorded time first.

    Comment: `answer: <text, may span lines>` then a last line `-- at: <iso> source: <name>`."""
    out = []
    for c in q.get("comments") or []:
        body = c["text"]
        if not body.startswith("answer: "):
            continue
        m = META.search(body)
        text = body[len("answer: "):m.start() if m else None]
        at, source = (m[1], m[2]) if m else (c["created_at"], "")
        if source == "timeout":
            text = text.removesuffix(" (timeout)")
        out.append({"text": text, "source": source, "at": parse_time(at)})
    return sorted(out, key=lambda x: x["at"])


def effective_answer(q):
    """The answer that counts: the earliest recorded one. None when unanswered."""
    a = answers(q)
    return a[0] if a else None


def answer(qid, text, at=None, source="human", timeout=False):
    """Returns one of: answered, overrode-timeout, late, noop."""
    if not re.fullmatch(r"\S+", source):
        raise ValueError("--source is one word")
    with locked():
        return _answer(qid, text, at, source, timeout)


def _answer(qid, text, at, source, timeout):
    q = ledger.show(qid)
    if "question" not in (q.get("labels") or []):
        raise ValueError(f"{qid} is not a question bead")
    f = parse_body(q.get("description"))
    t = now()
    prior = answers(q)
    if timeout:
        if f["reversible"] != "yes" or f["gated"] != "no":
            raise ValueError(f"{qid} never times out (reversible: {f['reversible']}, "
                             f"gated: {f['gated']})")
        if not f.get("deadline") or t < parse_time(f["deadline"]):
            raise ValueError(f"{qid}: deadline {f.get('deadline')} has not passed")
        if prior or q["status"] == "closed":
            return "noop"
        text, source, at = f["default"], "timeout", t
    else:
        text = text.strip()
        if not text:
            raise ValueError("empty answer")
        at = parse_time(at) if at else t
    labels = set(q.get("labels") or [])
    choice = next((o for o in f["options"] if o.lower() == text.lower()), None)
    meta = f"\n-- at: {iso(at)} source: {source}"
    if q["status"] == "closed" or "delivered" in labels:
        result = "late"
    elif not prior:
        result = "answered"
    elif all(p["source"] == "timeout" for p in prior) and at < prior[0]["at"]:
        # ponytail: the asking bead keeps decision:auto from the overridden timeout.
        result = "overrode-timeout"
    else:
        result = "late"
    if result == "late":
        ledger.comment(qid, f"late-answer: {text}{meta}")
        ledger.label(qid, add=["late-answer"])
        return result
    shown = choice or text
    ledger.comment(qid, f"answer: {shown}{' (timeout)' if timeout else ''}{meta}")
    ledger.label(qid, add=["answered"] + ([] if choice else ["answer-freetext"]))
    if timeout:
        ledger.label(f["asking"], add=["decision:auto"])
    return result


# --- CLI ---

def main_ask(argv):
    p = argparse.ArgumentParser(prog="ask", description="File a question bead and post it.")
    yn = ("yes", "no")
    p.add_argument("asking")
    p.add_argument("question")
    p.add_argument("-o", "--option", action="append", required=True)
    p.add_argument("--default", required=True)
    p.add_argument("--reversible", choices=yn, required=True)
    p.add_argument("--gated", choices=yn, required=True)
    p.add_argument("--blocks", choices=yn, required=True,
                   help="yes only if the answer can change what is built")
    p.add_argument("--deadline", help="4h, 30m or ISO-8601 UTC (default 4h; none if gated)")
    p.add_argument("--context")
    p.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    p.add_argument("--project", default=os.environ.get("IAR_INBOX_PROJECT", PROJECT))
    p.add_argument("--thread", help="Inbox thread name (default '<short id>: <asking title>')")
    a = p.parse_args(argv)
    try:
        return ask(a)
    except ValueError as e:
        print(f"ask: {e}", file=sys.stderr)
        return 2
    except ledger.LedgerError as e:
        print(f"ask: {e}", file=sys.stderr)
        return 1


def main_answer(argv):
    p = argparse.ArgumentParser(prog="answer", description="Record an answer to a question bead.")
    p.add_argument("qid")
    p.add_argument("text", nargs="?")
    p.add_argument("--timeout", action="store_true", help="apply the default at the deadline")
    p.add_argument("--at", help="when the answer was given (ISO-8601 UTC; default now)")
    p.add_argument("--source", default="human", help="human, inbox, linear, ...")
    a = p.parse_args(argv)
    if a.timeout == (a.text is not None):
        p.error("give TEXT or --timeout")
    try:
        print(answer(a.qid, a.text or "", a.at, a.source, a.timeout))
        return 0
    except ValueError as e:
        print(f"answer: {e}", file=sys.stderr)
        return 2
    except ledger.LedgerError as e:
        print(f"answer: {e}", file=sys.stderr)
        return 1


def self_test():
    import contextlib
    import io

    def run(fn, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = fn(argv)
        return rc, out.getvalue().strip()

    with tempfile.TemporaryDirectory() as root:
        saved = {k: os.environ.get(k) for k in ("BEADS_DIR", "IAR_INBOX_CMD", "IAR_STATE_DIR")}
        os.environ["BEADS_DIR"] = os.path.join(root, ".beads")
        os.environ["IAR_STATE_DIR"] = root
        log = os.path.join(root, "inbox.log")
        fake = os.path.join(root, "fake-inbox")
        with open(fake, "w") as fh:
            fh.write(f"#!/bin/sh\ncat >> '{log}'\n"
                     """echo '{"jsonrpc":"2.0","id":2,"result":{"structuredContent":"""
                     """{"thread_id":"msg_T1"}}}'\n""")
        os.chmod(fake, 0o755)
        try:
            subprocess.run(["br", "init", "--prefix", "qt"], cwd=root, check=True,
                           capture_output=True)
            base = ["-o", "Keep", "-o", "Drop", "--default", "Keep", "--session", "s1",
                    "--reversible", "yes", "--gated", "no"]
            a1 = ledger.create("asking one", type="task")["id"]
            a2 = ledger.create("asking two", type="task")["id"]
            os.environ["IAR_INBOX_CMD"] = fake

            # E1: blocking puts the asking bead in br blocked; non-blocking does not.
            rc, q1 = run(main_ask, [a1, "Keep the cache?", "--blocks", "yes", *base])
            assert rc == 0, rc
            rc, q2 = run(main_ask, [a2, "Rename it?", "--blocks", "no", *base])
            assert rc == 0, rc
            blocked = {i["id"] for i in ledger.blocked()}
            assert a1 in blocked and a2 not in blocked, blocked
            s = ledger.show(q1)
            assert s["title"] == f"Q({short(a1)}): Keep the cache?" and s["parent"] == a1, s
            assert sorted(s["labels"]) == ["no-swarm", "question"], s["labels"]
            f = parse_body(s["description"])
            assert f["inbox_thread"] == "msg_T1" and f["options"] == ["Keep", "Drop"], f
            with open(log) as fh:
                call = [json.loads(x) for x in fh.read().splitlines()][2]["params"]["arguments"]
            assert call["subject"].startswith(q1 + ":") and call["idempotency_key"] == q1, call
            assert "Holds up: " + a1 in call["body"] and "- [ ] Drop" in call["body"], call
            assert run(main_ask, [a1, "Keep the cache?", "--blocks", "yes", *base]) == (0, q1)

            # E2: answer moves the question to answered; a second answer is a comment only.
            assert run(main_answer, [q1, "drop"]) == (0, "answered")
            assert run(main_answer, [q1, "Keep"]) == (0, "late")
            s = ledger.show(q1)
            assert effective_answer(s)["text"] == "Drop", answers(s)
            assert {"answered", "late-answer"} <= set(s["labels"]), s["labels"]
            assert "answer-freetext" not in s["labels"]
            assert [c["text"].split("\n")[0] for c in s["comments"]] == \
                ["answer: Drop", "late-answer: Keep"], s["comments"]
            free = "something else\nsource: timeout\n-- not meta"
            assert run(main_answer, [q2, free]) == (0, "answered")
            s = ledger.show(q2)
            assert "answer-freetext" in s["labels"]
            assert effective_answer(s)["text"] == free and answers(s)[0]["source"] == "human", s

            # The read-and-write of an answer waits for the lock, so two answers cannot both win.
            rc, qc = run(main_ask, [a2, "Race?", "--blocks", "no", *base])
            with locked():
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "answer", qc,
                                      "Keep"], stdout=subprocess.PIPE, text=True)
                time.sleep(2)
                assert p.poll() is None and answers(ledger.show(qc)) == []
            assert p.communicate(timeout=30)[0].strip() == "answered"
            assert run(main_answer, [qc, "Drop"]) == (0, "late")

            # Timeout: refused before the deadline and when gated; applies the default once;
            # a human answer recorded before the timeout write wins over it.
            past = iso(now() - dt.timedelta(minutes=1))
            rc, q3 = run(main_ask, [a2, "Port?", "--blocks", "no", "--deadline", past, *base])
            assert run(main_answer, [q3, "--timeout"]) == (0, "answered")
            assert run(main_answer, [q3, "--timeout"]) == (0, "noop")
            assert "decision:auto" in ledger.show(a2)["labels"]
            assert answers(ledger.show(q3))[0] == {"text": "Keep", "source": "timeout",
                                                   "at": answers(ledger.show(q3))[0]["at"]}
            assert run(main_answer, [q3, "Drop", "--at", past]) == (0, "overrode-timeout")
            assert effective_answer(ledger.show(q3))["text"] == "Drop"
            gated = [x if x != "no" else "yes" for x in base]  # --gated yes
            rc, q4 = run(main_ask, [a2, "Spend?", "--blocks", "no", *gated])
            assert parse_body(ledger.show(q4)["description"])["deadline"] == ""
            assert run(main_answer, [q4, "--timeout"])[0] == 2
            rc, q5 = run(main_ask, [a2, "Later?", "--blocks", "no", *base])
            assert run(main_answer, [q5, "--timeout"])[0] == 2  # deadline not passed

            # E3: Inbox down -> bead with inbox:pending, non-zero exit, rerun reuses the bead.
            os.environ["IAR_INBOX_CMD"] = "false"
            rc, q6 = run(main_ask, [a2, "Inbox down?", "--blocks", "yes", *base])
            assert rc == 3 and "inbox:pending" in ledger.show(q6)["labels"], (rc, q6)
            rc, again = run(main_ask, [a2, "Inbox down?", "--blocks", "yes", *base])
            assert rc == 3 and again == q6, (rc, again)
            os.environ["IAR_INBOX_CMD"] = fake
            assert run(main_ask, [a2, "Inbox down?", "--blocks", "yes", *base]) == (0, q6)
            s = ledger.show(q6)
            assert "inbox:pending" not in s["labels"]
            assert parse_body(s["description"])["inbox_thread"] == "msg_T1"
            kids = [d for d in ledger.show(a2)["dependents"] if d["title"].endswith("Inbox down?")]
            assert len(kids) == 1, kids
            assert run(main_ask, [a2, "Bad", "--blocks", "no", "-o", "A", "-o", "B",
                                  "--default", "C", "--reversible", "yes", "--gated", "no"])[0] == 2
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    print("question self-test: ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        sys.exit(0)
    cmds = {"ask": main_ask, "answer": main_answer}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(cmds[sys.argv[1]](sys.argv[2:]))
