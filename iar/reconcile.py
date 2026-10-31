#!/usr/bin/env python3
"""reconcile: one snapshot of the factory, one class per item, the actions a tick would take.

Spec: precog-am8w W10 (read-only core), with the live/zombie predicates of
iar/research/factory-scan.md 1.4. The executor (W11) is not here: only --dry-run exists.

  reconcile.py --dry-run [--board]   take the lock, snapshot, classify, append one line to
                                     $IAR_STATE_DIR/reconcile.jsonl (default ~/.local/state/iar);
                                     --board also writes board.md from the same snapshot
  reconcile.py --self-test           fixtures only; touches no store, session or lease

Sources (30 s timeout and a schema check each): br (through ledger.py), `claude agents --json`
with transcripts and `ps`, `treehouse status --json`, git (branch head vs `ls-remote`), and the
Inbox files. Any failed source makes every item `source-error`: the tick proposes no action.

Items are beads. A bead is loop-owned when its latest `attempt: <n> session: ...` comment (bin/bg)
matches its assignee `agent:<bead>#<n>`. Leases count only when the holder is exactly
`agent:<bead>#<n>`. Everything else is `external` and gets no action.

Classes, first match wins: source-error, external, answer-pending, question-deadline,
malformed-question, stalled, zombie, stale-lease, closed-bead-thread, working, waiting, idle.
An action is `reconcile: <action> <key>` on the bead; a key already in its comments is not proposed.
Exit 75: another tick holds $IAR_STATE_DIR/reconcile.lock.
"""

import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

IAR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, IAR)
import ledger  # noqa: E402
import question  # noqa: E402
import workflow  # noqa: E402

HOME = os.path.expanduser("~")
BIN = f"{HOME}/.local/bin"
INBOX = f"{HOME}/.plannotator/inbox"
PRECOG = f"{HOME}/Documents/precog"
TIMEOUT_S = 30  # per source: its commands share one deadline, and a source that ends late is an error
_deadline = [float("inf")]
CPU_WINDOW = timedelta(minutes=5)
BEAD_RE = re.compile(r"^([a-z0-9]+-[a-z0-9.]+): ")
HOLDER_RE = re.compile(r"^agent:([a-z0-9]+-[a-z0-9.]+)#(\d+)$")
ATTEMPT_RE = re.compile(r"^attempt: (\d+) session: (\S+) slot: (\S+) branch: (\S+)")
QID_RE = re.compile(r"^QUESTION:\s*precog-[A-Za-z0-9.]+")
DEFAULTS = {"sessions": [], "slots": [], "inbox": [], "inbox_url": "http://localhost/", "plannotator": [],
            "beads": [], "questions": [], "issues": {}, "git": {}}


class SourceError(RuntimeError):
    pass


def need(ok, what):
    if not ok:
        raise SourceError(f"schema: {what}")


def sh(cmd, cwd=None, check=True):
    try:
        left = min(TIMEOUT_S, _deadline[0] - time.monotonic())
        if left <= 0:
            raise subprocess.TimeoutExpired(cmd, 0)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=left, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SourceError(f"{os.path.basename(cmd[0])}: {e}")
    if check and p.returncode != 0:
        raise SourceError(f"{os.path.basename(cmd[0])} {cmd[1]} exit {p.returncode}: {(p.stderr or p.stdout).strip()[-200:]}")
    return p.stdout if p.returncode == 0 else None


def json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def parse_time(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- sources: each fills its snapshot keys or raises ---

def processes():
    """{pid: (ppid, cpu seconds)} from ps."""
    table = {}
    for line in sh(["/bin/ps", "-axo", "pid=,ppid=,time="]).splitlines():
        pid, ppid, cpu = line.split()
        table[int(pid)] = (int(ppid), sum(float(x) * 60 ** i for i, x in enumerate(reversed(cpu.split(":")))))
    return table


def transcript_facts(sid):
    """What agents-digest.sh reads from a transcript tail, plus the file's last write."""
    paths = glob.glob(f"{HOME}/.claude/projects/*/{sid}.jsonl")
    if not paths:
        return {}
    facts = {"transcript_at": datetime.fromtimestamp(os.path.getmtime(paths[0]), timezone.utc).isoformat()}
    with open(paths[0], "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 400_000))
        tail = f.read().decode(errors="replace")
    for line in tail.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        kind = r.get("type")
        if kind == "ai-title":
            facts["title"] = r.get("aiTitle")
        elif kind == "last-prompt":
            facts["last_prompt"] = r.get("lastPrompt")
        elif kind == "pr-link":
            facts["pr"] = r.get("prUrl")
        elif kind in ("assistant", "user"):
            facts["last_active"] = r.get("timestamp")
            content = (r.get("message") or {}).get("content")
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"] \
                if kind == "assistant" and isinstance(content, list) else []
            if texts:
                facts["last_message"] = texts[-1].split("\n")[0]
    return facts


def src_agents(snap):
    rows = json.loads(sh([f"{BIN}/claude", "agents", "--json", "--all"]))
    need(isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get("sessionId"), str) for r in rows),
         "claude agents: a list of objects with sessionId")
    procs = processes()
    sessions = []
    for r in rows:
        if r.get("state") == "stopped":
            continue
        s = {**r, **transcript_facts(r["sessionId"])}
        s["alive"] = bool(r.get("pid")) and r["pid"] in procs  # live: a pid and the process exists
        kids, todo = {}, [r["pid"]] if s["alive"] else []
        while todo:
            parent = todo.pop()
            for pid, (ppid, cpu) in procs.items():
                if ppid == parent:
                    kids[str(pid)] = cpu
                    todo.append(pid)
        s["children"] = kids
        sessions.append(s)
    snap["sessions"] = sessions


def src_treehouse(snap):
    rows = json.loads(sh([f"{BIN}/treehouse", "status", "--json"], cwd=PRECOG))
    need(isinstance(rows, list) and all(isinstance(r, dict) and "name" in r and "status" in r for r in rows),
         "treehouse status: a list of slots with name and status")
    snap["slots"] = [{"slot": r["name"], "path": r.get("path"), "holder": r.get("lease_holder") or "",
                      "live": bool(r.get("processes"))} for r in rows if r["status"] == "leased"]


def inbox_threads():
    """Unresolved Inbox threads. Each jsonl file is an event log: the last record for an id wins."""
    messages, questions, first_seq = {}, {}, {}
    for path in glob.glob(f"{INBOX}/projects/*/messages.jsonl"):
        with open(path) as f:
            for row in json_lines(f.read()):
                messages[row["id"]] = row["record"]
                first_seq.setdefault(row["id"], row["seq"])
    for path in glob.glob(f"{INBOX}/projects/*/questions.jsonl"):
        with open(path) as f:
            for row in json_lines(f.read()):
                questions[row["id"]] = row["record"]
    threads = {}
    for m in sorted(messages.values(), key=lambda m: m["created_at"]):
        threads.setdefault(m["thread_id"], []).append(m)
    result = []
    for thread_id, posts in threads.items():
        root = messages.get(thread_id, posts[0])
        if root.get("resolved_at"):
            continue
        ids = {p["id"] for p in posts}
        open_questions = [
            q["prompt"]
            for q in questions.values()
            if q["message_id"] in ids and q.get("answer") is None and not q.get("action_result") and not q.get("superseded_by")
        ]
        result.append({
            "id": thread_id,
            "subject": root.get("subject") or root.get("thread_name") or "(no subject)",
            "session": root["author"].get("session"),
            "last_author": posts[-1]["author"]["kind"],
            "last_at": posts[-1]["created_at"],
            "open_questions": open_questions,
            "quiet": inbox_quiet(root, posts, first_seq, bool(open_questions)),
        })
    return result


def inbox_quiet(root, posts, first_seq, asking):
    """The Inbox's own Quiet rule (plannotator shared/inbox list.ts inboxSectionOf, store.ts readAt): Mark read
    with no message after it, or nothing asked and every agent message seen (a reply of Tal's counts as a look)."""
    seq = lambda m: first_seq.get(m["id"], 0)
    read_seq = root.get("person_read_seq")
    if read_seq is not None and not any(seq(m) > read_seq for m in posts if not (m.get("set_aside") and m["author"]["kind"] == "person")):
        return True
    seen = max([root.get("person_seen_seq") or 0] + [seq(m) for m in posts if m["author"]["kind"] == "person"])
    return not asking and not any(m["author"]["kind"] == "agent" and seq(m) > seen for m in posts)


def src_inbox(snap):
    try:
        snap["inbox"] = inbox_threads()
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SourceError(f"inbox files: {type(e).__name__}: {e}")
    snap["inbox_url"] = read_json(f"{INBOX}/inbox.json").get("url", DEFAULTS["inbox_url"])


def src_plannotator(snap):
    """Board only, best effort: a missing Plannotator never blocks a tick."""
    try:
        snap["plannotator"] = json_lines(sh([f"{IAR}/plannotator-open.sh"]))
    except (SourceError, ValueError):
        snap["plannotator"] = []


def src_br(snap):
    try:
        issues = ledger.list_issues(status="all")
        need(all(isinstance(i, dict) and "id" in i and "status" in i for i in issues), "tracker list: issues with id and status")
        known = {i["id"] for i in issues}
        snap["beads"] = [i for i in issues if i["status"] == "in_progress"]
        snap["questions"] = [{**question.parse_body(i.get("description")), "id": i["id"], "labels": i.get("labels") or []}
                             for i in issues if i["status"] == "open" and "question" in (i.get("labels") or [])]
        want = {i["id"] for i in snap["beads"]}
        want |= {m[1] for s in snap["slots"] if (m := HOLDER_RE.match(s["holder"]))}
        want |= {m[1] for t in snap["inbox"] if (m := BEAD_RE.match(t["subject"]))}
        # A thread with another subject still belongs to the bead its author session is named after.
        names = {s["sessionId"]: s.get("name") or "" for s in snap["sessions"]}
        want |= {m[1] for t in snap["inbox"] if (m := BEAD_RE.match(names.get(t.get("session"), "")))}
        shown = ledger.show_many(sorted(want & known))
        need(all(isinstance(i, dict) and isinstance(i.get("comments", []), list) for i in shown), "tracker show: issues")
        snap["issues"] = {i["id"]: i for i in shown}
    except ledger.LedgerError as e:
        raise SourceError(str(e))


def attempts(issue):
    """{n: {session, slot, branch}} from bin/bg's attempt comments."""
    return {int(m[1]): {"session": m[2], "slot": m[3], "branch": m[4]}
            for c in issue.get("comments") or [] if (m := ATTEMPT_RE.match(c.get("text") or ""))}


def src_git(snap):
    """pushed: `git ls-remote` has the branch at the local HEAD. Only for the latest attempt with a slot on disk."""
    for bid, issue in snap["issues"].items():
        att = attempts(issue)
        if not att:
            continue
        n = max(att)
        a = att[n]
        head = os.path.isdir(a["slot"]) and sh(["/usr/bin/git", "-C", a["slot"], "rev-parse", "--verify", "--quiet",
                                                 f"refs/heads/{a['branch']}"], check=False)
        head = head.strip() if head else None
        remote = None
        if head:
            out = sh(["/usr/bin/git", "-C", a["slot"], "ls-remote", "origin", f"refs/heads/{a['branch']}"])
            remote = out.split()[0] if out.strip() else None
        snap["git"][f"{bid}#{n}"] = {"head": head, "remote": remote, "pushed": bool(head) and head == remote}


# Order matters: br reads the leases and threads, git reads the issues.
SOURCES = {"agents": src_agents, "treehouse": src_treehouse, "inbox": src_inbox, "plannotator": src_plannotator,
           "br": src_br, "git": src_git}


def cpu_active(snap, state_dir, now):
    """Per session: did a child process use CPU in the last 5 min? Compares `ps` CPU time with a sample at least
    5 min old in $state_dir/cpu.json. No old sample yet: None (unknown, so not stalled)."""
    path = os.path.join(state_dir, "cpu.json")
    samples = [x for x in read_json(path).get("samples", []) if now.timestamp() - x["at"] < 3 * CPU_WINDOW.total_seconds()]
    old = max((x for x in samples if now.timestamp() - x["at"] >= CPU_WINDOW.total_seconds()),
              key=lambda x: x["at"], default=None)
    for s in snap["sessions"]:
        kids = s.get("children") or {}
        if not kids:
            s["cpu_active"] = False
        elif old is not None:
            s["cpu_active"] = any(pid not in old["cpu"] or cpu > old["cpu"][pid] for pid, cpu in kids.items())
    samples.append({"at": now.timestamp(), "cpu": {pid: cpu for s in snap["sessions"] for pid, cpu in (s.get("children") or {}).items()}})
    with tempfile.NamedTemporaryFile("w", dir=state_dir, delete=False) as f:
        json.dump({"samples": samples}, f)
    os.replace(f.name, path)


def snapshot(now, sources=None, state_dir=None):
    """Run every source; a failure lands in snap["errors"] and leaves that source's defaults."""
    snap = {"at": now.isoformat(), "errors": {}, **json.loads(json.dumps(DEFAULTS))}
    for name, fn in {**SOURCES, **(sources or {})}.items():
        _deadline[0] = start = time.monotonic() + TIMEOUT_S
        try:
            fn(snap)
            if time.monotonic() > start:  # ledger calls have their own 30 s limit each
                raise SourceError(f"took more than {TIMEOUT_S}s")
        except (SourceError, OSError, ValueError, TypeError, KeyError) as e:
            snap["errors"][name] = f"{type(e).__name__}: {e}"
        _deadline[0] = float("inf")
    in_progress = {b["id"] for b in snap["beads"]}
    for s in snap["sessions"]:
        m = BEAD_RE.match(s.get("name") or "")
        s["bead"] = m[1] if m else None
        s["bead_in_progress"] = bool(m) and m[1] in in_progress
    if state_dir and "agents" not in snap["errors"]:
        cpu_active(snap, state_dir, now)
    return snap


# --- classify: pure ---

def classify_bead(bid, b, snap, now, stall_after, sessions, leases, threads):
    """Return (class, [action key, …]) for one bead. Predicates: spec W10 and factory-scan.md 1.4."""
    att = attempts(b)
    n = max(att, default=None)
    owned = n is not None and b.get("assignee") == f"agent:{bid}#{n}"
    stale = [lease for lease in leases.get(bid, [])
             if not (b["status"] == "in_progress" and b.get("assignee") == lease["holder"])]
    if not owned and not stale:
        return "external", []
    s = sessions.get(att[n]["session"]) if owned else None
    a = f"attempt={n}"
    working = bool(s) and (s.get("state") == "working" or s.get("status") == "busy")
    if owned:
        qs = [q for q in snap["questions"] if q.get("asking") == bid]
        pending = [q for q in qs if "answered" in q["labels"] and "delivered" not in q["labels"]]
        if pending:
            return "answer-pending", [f"deliver-answer q={q['id']} {a}" for q in pending]
        due = [f"{'timeout' if q.get('reversible') == 'yes' else 'reping'} q={q['id']}" for q in qs
               if "answered" not in q["labels"] and q.get("gated") == "no" and q.get("deadline")
               and question.parse_time(q["deadline"]) <= now]
        if [k for k in due if k not in done_keys(b)]:
            return "question-deadline", due
        if s and not working and (s.get("last_message") or "").startswith("QUESTION:") \
                and not QID_RE.match(s["last_message"]):
            return "malformed-question", [f"flag-malformed {a}"]
        wrote = parse_time(s.get("transcript_at")) if s else None
        if working and wrote and now - wrote >= stall_after and s.get("cpu_active") is False:
            return "stalled", [f"nudge {a} count=1"]
        running = [sessions[x["session"]] for x in att.values() if x["session"] in sessions]
        if b["status"] == "in_progress" and not any(
                r.get("state") in ("working", "blocked") or r.get("alive") for r in running):
            pushed = snap["git"].get(f"{bid}#{n}", {}).get("pushed")
            return "zombie", [f"release {a}" if pushed else f"flag-zombie {a}"]
    if stale:
        return "stale-lease", [f"return-lease slot={x['slot']} holder={x['holder']}" for x in stale]
    mine = threads.get(bid, [])
    if owned and b["status"] == "closed" and mine:
        return "closed-bead-thread", [f"resolve-thread thread={t['id']}" for t in mine]
    if working:
        return "working", []
    if s and s.get("state") == "blocked":
        return "waiting", []
    return "idle", []


def done_keys(issue):
    return {c["text"][len("reconcile: "):].strip() for c in issue.get("comments") or []
            if (c.get("text") or "").startswith("reconcile: ")}


def classify(snap, now, stall_after_min=30):
    """{"classes": {bead: class}, "actions": [{item, class, action}]}; actions already recorded are left out."""
    sessions = {s["sessionId"]: s for s in snap["sessions"]}
    leases = {}
    for slot in snap["slots"]:
        if m := HOLDER_RE.match(slot["holder"]):
            leases.setdefault(m[1], []).append(slot)
    threads = {}
    for bid, b in snap["issues"].items():
        sids = {x["session"] for x in attempts(b).values()}
        threads[bid] = [t for t in snap["inbox"] if t.get("session") in sids
                        or ((m := BEAD_RE.match(t["subject"])) and m[1] == bid)]
    classes, actions = {}, []
    for bid, b in sorted(snap["issues"].items()):
        if snap["errors"]:
            classes[bid] = "source-error"
            continue
        cls, keys = classify_bead(bid, b, snap, now, timedelta(minutes=stall_after_min), sessions, leases, threads)
        classes[bid] = cls
        actions += [{"item": bid, "class": cls, "action": k} for k in keys if k not in done_keys(b)]
    return {"classes": classes, "actions": actions}


# --- tick ---

def state_dir():
    d = os.environ.get("IAR_STATE_DIR") or f"{HOME}/.local/state/iar"
    os.makedirs(d, exist_ok=True)
    return d


def main(argv):
    if "--self-test" in argv:
        self_test()
        return 0
    if "--dry-run" not in argv:
        print("reconcile: only --dry-run exists until the executor (W11)", file=sys.stderr)
        return 2
    d = state_dir()
    lock = open(os.path.join(d, "reconcile.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("reconcile: another tick holds the lock", file=sys.stderr)
        return 75
    os.environ["PATH"] = f"{BIN}:/opt/homebrew/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"
    now = datetime.now(timezone.utc)
    try:
        config, _ = workflow.load()
        os.environ.setdefault("BEADS_DIR", config["tracker"]["beads_dir"])
        snap = snapshot(now, state_dir=d)
        stall = config["stall_after_min"]
    except workflow.WorkflowError as e:
        snap, stall = {**json.loads(json.dumps(DEFAULTS)), "errors": {"workflow": str(e)}}, 30
    result = classify(snap, now, stall)
    line = {"at": now.isoformat(), "dry_run": True, "errors": snap["errors"], **result}
    with open(os.path.join(d, "reconcile.jsonl"), "a") as f:
        f.write(json.dumps(line) + "\n")
    if "--board" in argv:
        import board
        board.write(board.render(board.classify(snap, now)), now)
    print(json.dumps({"errors": snap["errors"], "actions": len(result["actions"])}))
    return 0


def self_test():
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    t = lambda minutes: (now - timedelta(minutes=minutes)).isoformat()

    def bead(bid, n=1, status="in_progress", assignee=None, extra=()):
        comments = [{"text": f"attempt: {k} session: s-{bid}-{k} slot: /slots/{bid} branch: u/tfriedman/iar-{bid} "
                             f"brief: /b"} for k in range(1, n + 1)] + [{"text": x} for x in extra]
        return {"id": bid, "status": status, "assignee": f"agent:{bid}#{n}" if assignee is None else assignee,
                "comments": comments if n else [{"text": x} for x in extra]}

    def sess(bid, k=1, **kw):
        return {"sessionId": f"s-{bid}-{k}", "kind": "background", "name": f"{bid}: x", **kw}

    def q(qid, asking, labels=(), **kw):
        return {"id": qid, "asking": asking, "labels": ["question", *labels], "gated": "no", "reversible": "yes",
                "deadline": "", **kw}

    live = {"pid": 1, "alive": True}
    snap = {
        "errors": {}, "git": {"precog-zp#1": {"pushed": True}},
        "issues": {b["id"]: b for b in [
            bead("precog-ap"), bead("precog-dl"), bead("precog-dl2"), bead("precog-mq"), bead("precog-st"),
            bead("precog-zm"), bead("precog-zp"), bead("precog-sl", status="open", assignee=""),
            bead("precog-ct", status="closed"), bead("precog-wk"), bead("precog-wt"), bead("precog-id"),
            bead("precog-ex", n=0, assignee="factory-wf"), bead("precog-ex2", assignee="factory-wf"),
            # precedence conflicts
            bead("precog-c1"),                                   # stalled + answer-pending
            bead("precog-c2", n=2),                              # zombie + stale-lease (lease of attempt 1)
            bead("precog-c3", status="closed"),                  # orphan lease + closed-bead thread
            bead("precog-rp", extra=["reconcile: reping q=precog-q4"]),  # deadline action done, so idle
            bead("precog-ag", extra=["reconcile: nudge attempt=1 count=1"]),  # stalled, nudge already done
        ]},
        "questions": [q("precog-q1", "precog-ap", ["answered"]), q("precog-q2", "precog-dl", deadline=t(1)),
                      q("precog-q3", "precog-dl2", reversible="no", deadline=t(1)),
                      q("precog-q5", "precog-dl", gated="yes", deadline=t(1)), q("precog-q6", "precog-c1", ["answered"]),
                      q("precog-q4", "precog-rp", reversible="no", deadline=t(1)),
                      q("precog-q7", "precog-ex", ["answered"]), q("precog-q8", "precog-wk", deadline=t(-60))],
        "sessions": [
            sess("precog-ap", state="blocked"), sess("precog-dl", state="blocked"), sess("precog-dl2", state="blocked"),
            sess("precog-mq", state="blocked", last_message="QUESTION: should I ship?"),
            sess("precog-st", state="working", transcript_at=t(45), cpu_active=False, **live),
            sess("precog-c1", state="working", transcript_at=t(45), cpu_active=False, **live),
            sess("precog-ag", state="working", transcript_at=t(45), cpu_active=False, **live),
            sess("precog-zm", state="failed"), sess("precog-zp", state="done"),
            sess("precog-wk", status="busy", transcript_at=t(45), cpu_active=None, **live),
            sess("precog-wt", state="blocked", last_message="QUESTION: precog-q9 ok?"),  # blocked, no pid: not zombie
            sess("precog-id", state="done", status="idle", **live),
            sess("precog-rp", state="done", status="idle", **live),
        ],
        "slots": [{"slot": "4", "holder": "agent:precog-sl#1"}, {"slot": "5", "holder": "agent:precog-c2#1"},
                  {"slot": "6", "holder": "agent:precog-c3#1"}, {"slot": "7", "holder": "agent:precog-ex2#1"},
                  {"slot": "8", "holder": "agent:task-x"}, {"slot": "9", "holder": "agent:dispatch-bead:precog-ex"}],
        "inbox": [{"id": "m-ct", "subject": "precog-ct: integrate?", "session": "zz"},
                  {"id": "m-c3", "subject": "other", "session": "s-precog-c3-1"},
                  {"id": "m-ex", "subject": "precog-ex: hi", "session": "zz"}],
    }
    want = {
        "precog-ap": "answer-pending", "precog-dl": "question-deadline", "precog-dl2": "question-deadline",
        "precog-mq": "malformed-question", "precog-st": "stalled", "precog-zm": "zombie", "precog-zp": "zombie",
        "precog-sl": "stale-lease", "precog-ct": "closed-bead-thread", "precog-wk": "working",
        "precog-wt": "waiting", "precog-id": "idle", "precog-ex": "external", "precog-ex2": "stale-lease",
        "precog-c1": "answer-pending", "precog-c2": "zombie", "precog-c3": "stale-lease", "precog-rp": "idle",
        "precog-ag": "stalled",
    }
    got = classify(snap, now)
    assert got["classes"] == want, {k: (v, want.get(k)) for k, v in got["classes"].items() if want.get(k) != v}
    acts = {(a["item"], a["action"]) for a in got["actions"]}
    assert acts == {
        ("precog-ap", "deliver-answer q=precog-q1 attempt=1"), ("precog-dl", "timeout q=precog-q2"),
        ("precog-dl2", "reping q=precog-q3"), ("precog-mq", "flag-malformed attempt=1"),
        ("precog-st", "nudge attempt=1 count=1"), ("precog-zm", "flag-zombie attempt=1"),
        ("precog-zp", "release attempt=1"), ("precog-sl", "return-lease slot=4 holder=agent:precog-sl#1"),
        ("precog-ct", "resolve-thread thread=m-ct"), ("precog-ex2", "return-lease slot=7 holder=agent:precog-ex2#1"),
        ("precog-c1", "deliver-answer q=precog-q6 attempt=1"), ("precog-c2", "flag-zombie attempt=2"),
        ("precog-c3", "return-lease slot=6 holder=agent:precog-c3#1"),
    }, acts
    assert classify(snap, now) == got  # dry run is idempotent

    # E8a: one failing source -> every item source-error, no actions. Run through snapshot() with fake sources.
    def fake(name):
        def fill(s):
            for k in {"agents": ["sessions"], "treehouse": ["slots"], "inbox": ["inbox"], "plannotator": [],
                      "br": ["issues", "questions"], "git": ["git"]}[name]:
                s[k] = json.loads(json.dumps(snap[k]))
        return fill

    def broken(s):
        raise SourceError("treehouse status: no answer in 30s")
    fakes = {name: fake(name) for name in SOURCES}
    global TIMEOUT_S
    TIMEOUT_S, saved = 0.05, TIMEOUT_S  # a source whose commands together pass the limit is a source error
    slow = snapshot(now, {**fakes, "git": lambda s: (time.sleep(0.03), sh(["/bin/sleep", "0.05"]))})
    TIMEOUT_S = saved
    assert list(slow["errors"]) == ["git"] and "sleep" in slow["errors"]["git"], slow["errors"]
    assert classify(snapshot(now, fakes), now) == got
    bad = snapshot(now, {**fakes, "treehouse": broken})
    assert bad["errors"] == {"treehouse": "SourceError: treehouse status: no answer in 30s"}, bad["errors"]
    e8a = classify(bad, now)
    assert e8a["actions"] == [] and set(e8a["classes"].values()) == {"source-error"}, e8a

    # src_br fetches a closed bead whose thread is linked only by its author session's name.
    real = ledger.list_issues, ledger.show_many
    asked = []
    ledger.list_issues = lambda status=None, labels=(): [{"id": "precog-cs", "status": "closed"}]
    ledger.show_many = lambda ids: asked.extend(ids) or [bead("precog-cs", status="closed")]
    try:
        s = {**json.loads(json.dumps(DEFAULTS)), "sessions": [sess("precog-cs", state="done")],
             "inbox": [{"id": "m-cs", "subject": "landed", "session": "s-precog-cs-1"}]}
        src_br(s)
    finally:
        ledger.list_issues, ledger.show_many = real
    assert asked == ["precog-cs"], asked
    s["errors"] = {}
    assert classify(s, now)["actions"] == [{"item": "precog-cs", "class": "closed-bead-thread",
                                            "action": "resolve-thread thread=m-cs"}]

    # cpu_active: unknown without a 5-min-old sample, then compares CPU time.
    with tempfile.TemporaryDirectory() as d:
        s = {"sessions": [{"children": {"7": 1.0}}, {"children": {}}]}
        cpu_active(s, d, now)
        assert "cpu_active" not in s["sessions"][0] and s["sessions"][1]["cpu_active"] is False
        later = now + timedelta(minutes=6)
        cpu_active(s, d, later)
        assert s["sessions"][0]["cpu_active"] is False
        s["sessions"][0]["children"] = {"7": 2.0}
        cpu_active(s, d, later + timedelta(seconds=60))
        assert s["sessions"][0]["cpu_active"] is True

        # flock: a second concurrent run exits 75 and writes nothing.
        with open(os.path.join(d, "reconcile.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = subprocess.run([sys.executable, os.path.realpath(__file__), "--dry-run"], capture_output=True,
                               text=True, timeout=60, env=dict(os.environ, IAR_STATE_DIR=d))
        assert p.returncode == 75 and "lock" in p.stderr, (p.returncode, p.stderr)
        assert not os.path.exists(os.path.join(d, "reconcile.jsonl"))

    agent = lambda i: {"id": i, "author": {"kind": "agent"}}
    seqs = {"r": 1, "m2": 5}
    assert inbox_quiet({"id": "r", "person_seen_seq": 3}, [agent("r")], seqs, False)
    assert not inbox_quiet({"id": "r", "person_seen_seq": 3}, [agent("r"), agent("m2")], seqs, False)
    assert not inbox_quiet({"id": "r", "person_seen_seq": 9}, [agent("r")], seqs, True)
    assert inbox_quiet({"id": "r", "person_read_seq": 6}, [agent("r"), agent("m2")], seqs, True)
    print("reconcile self-test: ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
