#!/usr/bin/env python3
"""reconcile: one snapshot of the factory, one class per item, the actions a tick would take.

Spec: precog-am8w W10 (read-only core) and W11 (executor), with the live/zombie predicates of
iar/research/factory-scan.md 1.4.

  reconcile.py [--board]             one tick: take the lock, snapshot, classify, execute the actions
                                     of the classes switched on in WORKFLOW.md `actions`, append one
                                     line to $IAR_STATE_DIR/reconcile.jsonl (default ~/.local/state/iar);
                                     --board also writes board.md from the same snapshot
  reconcile.py --dry-run [--board]   the same, but execute nothing
  reconcile.py --enable <class>      switch one action class on in WORKFLOW.md
  reconcile.py --kill                switch every action class off in WORKFLOW.md
  reconcile.py --self-test           fixtures and a scratch br store; touches no real store, session or lease

Sources (30 s timeout and a schema check each): br (through ledger.py), `claude agents --json`
with transcripts and `ps`, `treehouse status --json`, git (branch head vs `ls-remote`), and the
Inbox files. Any failed source makes every item `source-error`: the tick proposes no action.

Items are beads. A bead is loop-owned when its latest `attempt: <n> session: ...` comment (bin/bg)
matches its assignee `agent:<bead>#<n>`. Leases count only when the holder is exactly
`agent:<bead>#<n>`. Everything else is `external` and gets no action.

Classes, first match wins: source-error, external, over-limit, ineligible-running, answer-pending,
question-deadline, malformed-question, stalled, zombie, stale-lease, closed-bead-thread, inbox-pending, working,
waiting, idle.
An action is `reconcile: <action> <key>` on the bead; a key already in its comments is not proposed.

Executor: for each proposed action, the tick reloads the switches (so --kill stops a running tick),
re-reads the bead and skips a key already recorded (`reconcile: <key>`). Before it acts, it labels the
bead `reconcile-pending` and writes `reconcile-start: <class> <key>`; after, it writes `reconcile: <key>`
and drops the label. A later tick fetches labelled beads and finishes each open intent: if the
action's own effect is visible (a crash between acting and the comment), it writes the comment and
does not act again; if not, and the action is no longer proposed, it writes `reconcile-dropped: <key>`.
An action fails alone: no `reconcile:` comment, the next tick retries it.
Actions are in EXECUTORS by verb; a verb with no executor is skipped. W12 (housekeeping):
  zombie              release (branch pushed: status open, assignee cleared) or flag-zombie (with the resume command)
  stale-lease         return-lease only after the release preflight passes, checked again at act time (E10);
                      flag-lease when a check fails that waiting does not fix (dirty, unpushed, branch not pushed)
  closed-bead-thread  resolve-thread: one "landed" line in the thread, then resolve it
  stalled             one nudge ("status?") per attempt, then flag-stalled; no automatic kill
  over-limit          working past a WORKFLOW.md `limits` value (attempts, hours, tokens): one limit-nudge per
                      attempt; still working 15 min later, or over a limit again in a later attempt: quarantine
                      (labels quarantined + needs-human; bin/bg refuses a quarantined bead; board: Needs you)
  ineligible-running  the bead was closed (its own W9 close: once the turn ends) or deferred, or lost `auto` (only
                      attempts marked `dispatch: auto`): stop-session, verified; the worktree stays and
                      stale-lease returns the slot after the preflight
W13 (questions of loop-owned beads; question beads come from iar/ask):
  answer-pending      import-answer: Tal's sent Inbox answer to the :::question whose subject starts with the
                      question id goes through iar/answer (question.answer, --at = when he picked it), so the first
                      recorded answer wins; deliver-answer: the recorded answer, verbatim, to the latest attempt's
                      session (resumed if stopped; precog-v8xo), checked in its transcript, then the question is
                      labelled `delivered` and closed
  question-deadline   timeout (reversible, not gated: iar/answer --timeout writes the default, delivered next tick)
                      or reping (not reversible: one reminder in the thread); a gated question has no deadline
  malformed-question  flag-malformed: a note; the board lists every one under Needs you (malformed()); no bead invented
  inbox-pending       post-question: post a question bead that ask could not post; last of the action classes
W25 (Linear view, iar/linear_mirror.py, after the other actions of the tick; not per bead):
  linear-mirror       one-way mirror of the beads into Linear team TAL. A dry run lists the writes under `linear` in the
                      jsonl line; a live tick writes them (`linear.done`) only with the switch on
  (W25b) Tal's `answer: <text>` comment, or his move to Approved ("approve"), on a mirrored question issue joins
                      the Inbox answers before classify (read in a dry run, or live with the switch on); the earlier
                      one goes through import-answer (source linear) and deliver-answer like an Inbox answer
W14 (dispatch, class `dispatch`, after the other actions of the tick):
  preflight           WORKFLOW.md loads and every source answered. A WORKFLOW.md that does not load blocks dispatch
                      only: the other actions run on the last good config ($IAR_STATE_DIR/workflow-good.json; --kill
                      switches it off too), and the jsonl line and board show `config error: <msg>`
  candidates          `ready` task beads with tracker.dispatch_label, open, unassigned, not no-swarm, quarantined or
                      auto:blocked; sorted by priority, then creation time. A bead of a stacked `pr:` group
                      (stacked_groups) stays ready, not dispatched; the jsonl line has `held: {bead: reason}`
  reject-auto         the gate fails (no `Files:` list, no `Done when:` line with a `command`, a budget block that
                      budget.py rejects), or limits.max_attempts attempts were made (also quarantined + needs-human):
                      one note and the label auto:blocked
  dispatch            `bin/bg <bead> --done-when <cmd> --auto` while loop-owned in_progress beads are fewer than
                      agent.max_concurrent (and max_concurrent_by_state.in_progress), at most
                      limits.max_starts_per_tick (default 2) per tick. Files that overlap an in_progress bead or a
                      running session's bead: wait. After n `attempt-failed:` comments: wait 10 s * 2^(n-1), at most
                      max_retry_backoff_ms. All state is in bead comments, so a lost $IAR_STATE_DIR repeats nothing (E12)
A flag is a `reconcile-note: <key>` comment. Before flag-stalled, quarantine or stop-session, the tick saves the
last 40 lines of the session's final assistant text (`last-output: <key>`).
A nudge (and an answer delivery) never stops a session (precog-6j1m). A running session gets the text through rex: a
new rex session runs `claude attach <id>`, the text is typed into the Claude input box and Enter sent, then the rex
session is killed (the Claude session keeps running). No Enter when `claude agents` shows the session waiting or the
pane shows a dialog or typed text: the action fails and the next tick tries again. No rex (binary missing, server not
reachable): the action fails and the bead gets `reconcile-note: nudge-no-rex attempt=<n>` once. A session with no
process gets `claude --bg --resume <sid> <text>`.
Exit 75: another tick holds $IAR_STATE_DIR/reconcile.lock.
"""

import contextlib
import fcntl
import fnmatch
import glob
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

IAR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, IAR)
import budget  # noqa: E402
import ledger  # noqa: E402
import linear_mirror  # noqa: E402
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
QID_RE = re.compile(r"^QUESTION:\s*(precog-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)\.?(?:\s|$)")  # as in notify-stop.sh
DEFAULTS = {"sessions": [], "slots": [], "inbox": [], "inbox_url": "http://localhost/", "plannotator": [],
            "beads": [], "questions": [], "inbox_answers": {}, "issues": {}, "git": {}, "slot_git": {}, "session_names": {},
            "candidates": [], "stacked": {}}
LIMITS = {"max_attempts": 3, "max_attempt_hours": 4, "max_attempt_tokens": 3_000_000,
          "max_starts_per_tick": 2}  # WORKFLOW.md `limits` overrides
DISPATCH_LABEL = ["auto"]  # WORKFLOW.md tracker.dispatch_label; tick sets it before the snapshot
AUTO_BLOCKED = "auto:blocked"
BG = os.path.join(os.path.dirname(IAR), "bin", "bg")  # IAR_BG overrides it (tests)
STACK_OWNERS = os.path.join(IAR, "stack-owners.conf")  # `<stack> <branch-glob>...` lines (pr-stack skill)
BG_TIMEOUT_S = 1200  # bin/bg leases (up to 15 min) and waits for the session
TRY_RE = re.compile(r"^attempt(?:: |-failed: \S+ attempt=)(\d+)\b")  # bin/bg next_attempt numbering
# ponytail: keyword guess at "needs compute"; it errs toward a reject (Tal adds a budget block or dispatches by hand)
COMPUTE_RE = re.compile(r"(?i)\b(gpu|cluster|flyte|sky (?:launch|exec|jobs)|skypilot|ray job|aws|baseten|training run|"
                        r"eval run|sagemaker|ec2|s3://)")
LAST_GOOD = [None]  # the last WORKFLOW.md config that loaded; actions run on it while the file is bad
LIMIT_GRACE = timedelta(minutes=15)  # after a limit-nudge, still working this long: quarantine
QUARANTINE = "quarantined"
OUTPUT_LINES = 40
STOP_WAIT_S = 30
REX_APP = "/Applications/Rex Beta.app/Contents/Helpers/Rex Server.rex/Contents/MacOS/rex"  # when not on PATH
DIALOG_RE = re.compile(r"Do you want to |^\s*❯ \d+\. ", re.M)  # a permission or AskUserQuestion dialog
INBOX_PROJECT = "/Users/tal/.local/share/precog/Precog agents"  # inbox-etiquette.md: every send_message names it
TRANSIENT = ("process running in the slot", "session running")  # preflight fails that end by themselves: no flag


class SourceError(RuntimeError):
    pass


def need(ok, what):
    if not ok:
        raise SourceError(f"schema: {what}")


def left():
    """Seconds left before the running source's deadline."""
    remaining = min(TIMEOUT_S, _deadline[0] - time.monotonic())
    if remaining <= 0:
        raise SourceError(f"no time left of the {TIMEOUT_S}s source limit")
    return remaining


def sh(cmd, cwd=None, check=True, input=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=left(), cwd=cwd, input=input)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SourceError(f"{os.path.basename(cmd[0])}: {e}")
    if check and p.returncode != 0:
        raise SourceError(f"{os.path.basename(cmd[0])} {cmd[1]} exit {p.returncode}: {(p.stderr or p.stdout).strip()[-200:]}")
    return p.stdout if p.returncode == 0 else None


def json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


def strict_lines(text):
    """Every non-blank line is one JSON object; anything else raises ValueError."""
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("a line that is not a JSON object")
    return rows


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
    return read_transcript(paths[0]) if paths else {}


def read_transcript(path):
    facts = {"transcript": path,
             "transcript_at": datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()}
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 400_000))
        tail = f.read().decode(errors="replace")
    output_id, output = None, []  # the last assistant message with text; its blocks are separate lines
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
                msg_id = (r.get("message") or {}).get("id")
                output = (output if msg_id and msg_id == output_id else []) + texts
                output_id = msg_id
    if output:
        facts["last_output"] = "\n".join("\n".join(output).splitlines()[-OUTPUT_LINES:])
    return facts


def transcript_tokens(path):
    """input + output + cache_creation tokens of the assistant messages. Every content block of a message is its own
    line with the same message id and usage, so a message counts once."""
    # ponytail: reads the whole file each tick for each working session; cache by (size, offset) if it gets slow
    usage = {}
    with open(path, errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                m = json.loads(line).get("message") or {}
            except ValueError:
                continue
            u = m.get("usage") if isinstance(m, dict) else None
            if isinstance(u, dict) and m.get("id"):
                usage[m["id"]] = sum(u.get(k) or 0 for k in
                                     ("input_tokens", "output_tokens", "cache_creation_input_tokens"))
    return sum(usage.values())


def check_agents(rows):
    """Every field classify reads: sessionId, kind; state for background; pid an int when present."""
    need(isinstance(rows, list), "claude agents: a list")
    for r in rows:
        need(isinstance(r, dict) and isinstance(r.get("sessionId"), str), "claude agents: sessionId")
        need(r.get("kind") in ("background", "interactive"), f"claude agents {r.get('sessionId')}: kind")
        need(r["kind"] == "interactive" or isinstance(r.get("state"), str), f"claude agents {r['sessionId']}: state")
        need(r.get("pid") is None or isinstance(r["pid"], int), f"claude agents {r['sessionId']}: pid")
        need(r.get("status") is None or isinstance(r["status"], str), f"claude agents {r['sessionId']}: status")
    return rows


def check_slots(rows):
    """name and status on every slot; a leased slot also has a string lease_holder and a processes list."""
    need(isinstance(rows, list), "treehouse status: a list")
    for r in rows:
        need(isinstance(r, dict) and "name" in r and isinstance(r.get("status"), str), "treehouse status: name, status")
        need(r["status"] != "leased" or (isinstance(r.get("lease_holder"), str) and isinstance(r.get("processes"), list)),
             f"treehouse slot {r['name']}: lease_holder, processes")
    return rows


def src_agents(snap):
    rows = check_agents(json.loads(sh([f"{BIN}/claude", "agents", "--json", "--all"])))
    procs = processes()
    sessions = []
    for r in rows:
        if r.get("state") == "stopped":
            continue
        s = {**r, **transcript_facts(r["sessionId"])}
        s["alive"] = bool(r.get("pid")) and r["pid"] in procs  # live: a pid and the process exists
        if r["kind"] == "background" and r.get("state") == "working" and s.get("transcript"):
            s["tokens"] = transcript_tokens(s["transcript"])  # over-limit reads it for working sessions only
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
    snap["session_names"] = {r["sessionId"]: r.get("name") or "" for r in rows}  # stopped ones too


def src_treehouse(snap):
    rows = check_slots(json.loads(sh([f"{BIN}/treehouse", "status", "--json"], cwd=PRECOG)))
    snap["slots"] = [{"slot": r["name"], "path": r.get("path"), "holder": r.get("lease_holder") or "",
                      "live": bool(r.get("processes"))} for r in rows if r["status"] == "leased"]


def inbox_records():
    """(messages, questions, first seq per message id) of every project. Each jsonl file is an event log: the
    last record for an id wins."""
    messages, questions, first_seq = {}, {}, {}
    for path in glob.glob(f"{INBOX}/projects/*/messages.jsonl"):
        with open(path) as f:
            for row in strict_lines(f.read()):
                messages[row["id"]] = row["record"]
                first_seq.setdefault(row["id"], row["seq"])
    for path in glob.glob(f"{INBOX}/projects/*/questions.jsonl"):
        with open(path) as f:
            for row in strict_lines(f.read()):
                questions[row["id"]] = row["record"]
    return messages, questions, first_seq


def inbox_answers(messages, questions):
    """{question bead id: {text, at}}: Tal's sent answers to the :::question that iar/ask posted (subject
    `<qid>: ...`). A picked option (plus its note), or the reply text when he answered by reply. Drafts (no
    sent reply) and skips are not answers."""
    out = {}
    for q in sorted(questions.values(), key=lambda q: q.get("position") or 0):
        a = q.get("answer") or {}
        m = BEAD_RE.match((messages.get(q["message_id"]) or {}).get("subject") or "")
        if not (m and a and q.get("sent_reply_id") and q.get("picked_at")):
            continue
        if a.get("by_reply"):
            text = (messages.get(q["sent_reply_id"]) or {}).get("body") or ""
        else:
            text = "; ".join(a.get("selected") or [])
            note = (a.get("note") or "").strip()
            text = f"{text} (note: {note})" if text and note else text or note
        if text.strip():
            out.setdefault(m[1], {"text": text.strip(), "at": q["picked_at"]})
    return out


def inbox_threads(messages, questions, first_seq):
    """Unresolved Inbox threads."""
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
        records = inbox_records()
        snap["inbox"] = inbox_threads(*records)
        snap["inbox_answers"] = inbox_answers(*records[:2])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SourceError(f"inbox files: {type(e).__name__}: {e}")
    snap["inbox_url"] = read_json(f"{INBOX}/inbox.json").get("url", DEFAULTS["inbox_url"])


def src_plannotator(snap):
    """Board only, best effort: a missing Plannotator never blocks a tick."""
    try:
        snap["plannotator"] = json_lines(sh([f"{IAR}/plannotator-open.sh"]))
    except (SourceError, ValueError):
        snap["plannotator"] = []


def check_issues(issues):
    """br show: every field classify reads."""
    for i in issues:
        need(isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("status"), str),
             "tracker show: id, status")
        need(i.get("assignee") is None or isinstance(i["assignee"], str), f"tracker show {i['id']}: assignee")
        need(isinstance(i.get("comments", []), list)
             and all(isinstance(c, dict) and isinstance(c.get("text"), str) for c in i.get("comments", [])),
             f"tracker show {i['id']}: comments")
    return issues


def src_br(snap):
    try:
        ledger.TIMEOUT_S = left()  # each tracker call gets what is left of the source's deadline
        issues = ledger.list_issues(status="all")
        need(all(isinstance(i, dict) and "id" in i and "status" in i for i in issues), "tracker list: issues with id and status")
        known = {i["id"] for i in issues}
        snap["beads"] = [i for i in issues if i["status"] == "in_progress"]
        snap["questions"] = [{**question.parse_body(i.get("description")), "id": i["id"], "labels": i.get("labels") or []}
                             for i in issues if i["status"] == "open" and "question" in (i.get("labels") or [])]
        want = {i["id"] for i in snap["beads"]}
        want |= {i["id"] for i in issues if "reconcile-pending" in (i.get("labels") or [])}  # open intents (W11)
        want |= {i["id"] for i in issues if QUARANTINE in (i.get("labels") or []) and i["status"] != "closed"}  # board
        # ineligible-running: a running session's bead, whatever its status (closed, deferred).
        want |= {m[1] for s in snap["sessions"] if running(s) and (m := BEAD_RE.match(s.get("name") or ""))}
        want |= {m[1] for s in snap["slots"] if (m := HOLDER_RE.match(s["holder"]))}
        want |= {m[1] for t in snap["inbox"] if (m := BEAD_RE.match(t["subject"]))}
        # A thread with another subject still belongs to the bead its author session is named after.
        names = snap["session_names"]
        want |= {m[1] for t in snap["inbox"] if (m := BEAD_RE.match(names.get(t.get("session")) or ""))}
        ledger.TIMEOUT_S = left()
        shown = ledger.show_many(sorted(want & known))
        check_issues(shown)
        snap["issues"] = {i["id"]: i for i in shown}
        # W14 candidates: ready task beads with the dispatch label, with their comments (attempts, backoff).
        ledger.TIMEOUT_S = left()
        ready = [i["id"] for i in ledger.ready(labels=[DISPATCH_LABEL[0]], type="task")]
        ledger.TIMEOUT_S = left()
        snap["candidates"] = check_issues(ledger.show_many(sorted(ready)))
    except ledger.LedgerError as e:
        raise SourceError(str(e))
    finally:
        ledger.TIMEOUT_S = TIMEOUT_S


def attempts(issue):
    """{n: {session, slot, branch, at, auto}} from bin/bg's attempt comments (auto: W14 marks `dispatch: auto`)."""
    return {int(m[1]): {"session": m[2], "slot": m[3], "branch": m[4], "at": c.get("created_at"),
                        "auto": " dispatch: auto" in c["text"]}
            for c in issue.get("comments") or [] if (m := ATTEMPT_RE.match(c.get("text") or ""))}


def branch_state(slot, branch):
    """pushed: `git ls-remote` has the branch at the local HEAD."""
    head = os.path.isdir(slot) and sh(["/usr/bin/git", "-C", slot, "rev-parse", "--verify", "--quiet",
                                       f"refs/heads/{branch}"], check=False)
    head = head.strip() if head else None
    remote = None
    if head:
        out = sh(["/usr/bin/git", "-C", slot, "ls-remote", "origin", f"refs/heads/{branch}"])
        remote = out.split()[0] if out.strip() else None
    return {"head": head, "remote": remote, "pushed": bool(head) and head == remote}


def slot_state(path):
    """Release preflight git checks: uncommitted or untracked files; a local branch with commits that no current
    origin branch has. Origin heads come from `git ls-remote` (not cached remote-tracking refs, which may name a
    deleted or rewritten branch); a head not in the local object store cannot cover a local commit."""
    git = ["/usr/bin/git", "-C", path]
    dirty = sh([*git, "status", "--porcelain"])
    heads = "\n".join(line.split()[0] for line in sh([*git, "ls-remote", "--heads", "origin"]).splitlines() if line.strip())
    known = [line.split()[0] for line in sh([*git, "cat-file", "--batch-check"], input=heads + "\n").splitlines()
             if line.split()[1:2] == ["commit"]]
    # `--not` does not reach revisions read from --stdin: each one gets its own ^
    unpushed = sh([*git, "rev-list", "--max-count=1", "--branches", "--stdin"], input="".join(f"^{x}\n" for x in known))
    return {"dirty": bool(dirty.strip()), "unpushed": bool(unpushed.strip())}


def src_git(snap):
    """Branch state for the latest attempt of each bead and the attempt of each bin/bg lease; slot state for
    each bin/bg lease with a path on disk."""
    for bid, issue in snap["issues"].items():
        att = attempts(issue)
        if att:
            n = max(att)
            snap["git"][f"{bid}#{n}"] = branch_state(att[n]["slot"], att[n]["branch"])
    for lease in snap["slots"]:
        m = HOLDER_RE.match(lease["holder"])
        a = m and attempts(snap["issues"].get(m[1]) or {}).get(int(m[2]))
        if not a or not lease.get("path") or not os.path.isdir(lease["path"]):
            continue
        snap["slot_git"][lease["slot"]] = slot_state(lease["path"])
        if f"{m[1]}#{m[2]}" not in snap["git"]:
            snap["git"][f"{m[1]}#{m[2]}"] = branch_state(lease["path"], a["branch"])
    slugs = {x[3:] for c in snap["candidates"] for x in c.get("labels") or [] if x.startswith("pr:")}
    snap["stacked"] = stacked_groups(PRECOG, slugs) if slugs else {}


def stacked_groups(repo, slugs):
    """{slug: reason} for the PR groups the loop must not dispatch (W9b): listed in stack-owners.conf, or not based
    on the default branch: another unmerged group branch is an ancestor of it (it is stacked on that group), the
    two share commits not on the default branch and neither contains the other (a stack that needs a restack, and
    git cannot tell which is the bottom one: both wait). A group the default branch moved past is not held: the
    group's PR takes the new commits at merge. A group branch that does not exist yet is based on the default
    branch. Fetches those branches first."""
    git = ["/usr/bin/git", "-C", repo]
    globs = []
    with contextlib.suppress(OSError), open(STACK_OWNERS) as f:
        globs = [g for line in f if not line.lstrip().startswith("#") for g in line.split()[1:]]
    head = re.search(r"(?m)^ref: refs/heads/(\S+)\tHEAD$", sh([*git, "ls-remote", "--symref", "origin", "HEAD"]))
    need(head, "git ls-remote: origin HEAD names the default branch")
    sh([*git, "fetch", "-q", "--prune", "origin", f"+refs/heads/{head[1]}:refs/remotes/origin/{head[1]}",
        "+refs/heads/u/tfriedman/pr/*:refs/remotes/origin/u/tfriedman/pr/*"])
    default = f"origin/{head[1]}"
    ancestor = lambda a, b: sh([*git, "merge-base", "--is-ancestor", a, b], check=False) is not None
    groups = sh([*git, "for-each-ref", "--format=%(refname:strip=2)", "refs/remotes/origin/u/tfriedman/pr/"]).split()
    unmerged = [x for x in groups if not ancestor(x, default)]
    out = {}
    for slug in sorted(slugs):
        g = f"origin/u/tfriedman/pr/{slug}"
        if any(fnmatch.fnmatchcase(g[7:], x) for x in globs):
            out[slug] = "stacked group: use pr-stack (listed in iar/stack-owners.conf)"
            continue
        for other in (x for x in unmerged if x != g and g in groups):
            if ancestor(other, g):
                out[slug] = f"stacked group: use pr-stack (based on {other[7:]})"
            elif not ancestor(g, other) and (base := sh([*git, "merge-base", g, other], check=False)) \
                    and not ancestor(base.strip(), default):
                out[slug] = f"stacked group: use pr-stack (shares unmerged commits with {other[7:]}, needs a restack)"
            if slug in out:
                break
    return out


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


def snapshot(now, sources=None, state_dir=None, only=None):
    """Run every source (or the `only` ones); a failure lands in snap["errors"] and leaves that source's defaults.
    snap["refresh"]() takes a new snapshot with the same sources, for checks an action repeats when it acts."""
    snap = {"at": now.isoformat(), "errors": {}, **json.loads(json.dumps(DEFAULTS))}
    snap["refresh"] = lambda: snapshot(datetime.now(timezone.utc), sources, only=("agents", "treehouse", "br", "git"))
    for name, fn in {**SOURCES, **(sources or {})}.items():
        if only and name not in only:
            continue
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

def running(s):
    """The session's process runs (a live pid) or it works."""
    return bool(s) and (bool(s.get("alive")) or s.get("state") == "working" or s.get("status") == "busy")


def preflight(bid, b, k, lease, snap, sessions):
    """Release preflight (spec "State machines") for the lease of attempt k: [] when it may be returned, else the
    checks that fail."""
    fails = []
    g = snap["slot_git"].get(lease["slot"])
    if g is None:
        fails.append("no git state for the slot")
    else:
        fails += ["worktree not clean"] * g["dirty"] + ["local commits missing from origin"] * g["unpushed"]
    if lease.get("live"):
        fails.append("process running in the slot")
    if b["status"] != "closed":
        if not snap["git"].get(f"{bid}#{k}", {}).get("pushed"):
            fails.append("bead not closed and branch not pushed")
        if running(sessions.get(attempts(b)[k]["session"])):
            fails.append("session running")
    return fails


def over_limit(b, n, s, now, limits):
    """The first WORKFLOW.md limit attempt n is past: attempts, hours (since its attempt comment), tokens."""
    lim = {**LIMITS, **(limits or {})}
    start = parse_time(attempts(b)[n]["at"])
    if n > lim["max_attempts"]:
        return "attempts"
    if start and now - start >= timedelta(hours=lim["max_attempt_hours"]):
        return "hours"
    if (s.get("tokens") or 0) >= lim["max_attempt_tokens"]:
        return "tokens"
    return None


def limit_actions(b, n, limit, now):
    """One limit-nudge per attempt; quarantine 15 min after it, or at once when an earlier attempt was nudged."""
    if QUARANTINE in (b.get("labels") or []):
        return []
    nudged = {}
    for c in b.get("comments") or []:
        if m := re.match(r"reconcile: limit-nudge attempt=(\d+) ", c.get("text") or ""):
            nudged.setdefault(int(m[1]), parse_time(c.get("created_at")) or now)
    if n not in nudged:
        return [f"quarantine attempt={n}"] if nudged else [f"limit-nudge attempt={n} limit={limit}"]
    return [f"quarantine attempt={n}"] if now - nudged[n] >= LIMIT_GRACE else []


def malformed(s, qs):
    """W5: the session's last message is a `QUESTION:` line with no question id, or with an id that is not one of qs
    (the open question beads of its bead). The board flags it under Needs you whatever the bead's class."""
    said = s.get("last_message") or ""
    return said.startswith("QUESTION:") and not ((m := QID_RE.match(said)) and m[1] in {q["id"] for q in qs})


def classify_bead(bid, b, snap, now, stall_after, sessions, leases, threads, limits=None):
    """Return (class, [action key, …]) for one bead. Predicates: spec W10 and factory-scan.md 1.4."""
    att = attempts(b)
    n = max(att, default=None)
    owned = n is not None and b.get("assignee") == f"agent:{bid}#{n}"
    if b.get("assignee") and not owned and not re.match(rf"^agent:{re.escape(bid)}#\d+$", b["assignee"]):
        return "external", []  # claimed by anything other than bin/bg: no action at all, leases included
    stale = [lease for lease in leases.get(bid, []) if int(HOLDER_RE.match(lease["holder"])[2]) in att
             and not (b["status"] == "in_progress" and b.get("assignee") == lease["holder"])]  # a lease of a recorded attempt
    if not owned and not stale:
        return "external", []
    s = sessions.get(att[n]["session"]) if owned else None
    a = f"attempt={n}"
    working = bool(s) and (s.get("state") == "working" or s.get("status") == "busy")
    if owned and working and b["status"] == "in_progress" and (limit := over_limit(b, n, s, now, limits)):
        return "over-limit", limit_actions(b, n, limit, now)
    if owned and running(s):
        # The session's own W9 close (reason "integrated into ...", WORKFLOW.md step 7) is followed by its FYI guide
        # and READY line in the same turn, so that session is stopped once its turn ends; any other close at once.
        own_close = (b.get("close_reason") or "").startswith("integrated into ")
        why = (b["status"] == "closed" and not (working and own_close)) or b["status"] == "deferred" \
            or (att[n]["auto"] and "auto" not in (b.get("labels") or []))
        if why:
            return "ineligible-running", [f"stop-session {a}"]
    if owned:
        qs = [q for q in snap["questions"] if q.get("asking") == bid]
        # W13: import Tal's Inbox answer through iar/answer, then deliver the recorded answer to the latest attempt.
        keys = [f"import-answer q={q['id']}" for q in qs if q["id"] in snap.get("inbox_answers", {})]
        # Open until closed: a crash between the `delivered` label and the close leaves it here, and the act closes it.
        keys += [f"deliver-answer q={q['id']} {a}" for q in qs if "answered" in q["labels"]]
        if [k for k in keys if k not in done_keys(b)]:
            return "answer-pending", keys
        due = [f"{'timeout' if q.get('reversible') == 'yes' else 'reping'} q={q['id']}" for q in qs
               if "answered" not in q["labels"] and q.get("gated") == "no" and q.get("deadline")
               and question.parse_time(q["deadline"]) <= now
               and "inbox:pending" not in q["labels"]]  # not before Tal could see it: inbox-pending posts it first
        if [k for k in due if k not in done_keys(b)]:
            return "question-deadline", due
        if s and not working and malformed(s, qs):
            return "malformed-question", [f"flag-malformed {a}"]
        wrote = parse_time(s.get("transcript_at")) if s else None
        if working and wrote and now - wrote >= stall_after and s.get("cpu_active") is False:
            return "stalled", [f"flag-stalled {a}" if f"nudge {a} count=1" in done_keys(b) else f"nudge {a} count=1"]
        listed = [sessions[x["session"]] for x in att.values() if x["session"] in sessions]
        if b["status"] == "in_progress" and not any(
                r.get("state") in ("working", "blocked") or r.get("alive") for r in listed):
            pushed = snap["git"].get(f"{bid}#{n}", {}).get("pushed")
            return "zombie", [f"release {a}" if pushed else f"flag-zombie {a}"]
    if stale:
        keys = []
        for x in stale:
            fails = preflight(bid, b, int(HOLDER_RE.match(x["holder"])[2]), x, snap, sessions)
            if not fails:
                keys.append(f"return-lease slot={x['slot']} holder={x['holder']}")
            elif not set(fails) & set(TRANSIENT):  # E10: never returned; flagged once
                keys.append(f"flag-lease slot={x['slot']} holder={x['holder']}")
        return "stale-lease", keys
    mine = threads.get(bid, [])
    if owned and b["status"] == "closed" and mine:
        return "closed-bead-thread", [f"resolve-thread thread={t['id']}" for t in mine]
    # Last of the action classes, so a post that keeps failing (Inbox down) holds up no other action.
    posts = [f"post-question q={q['id']}" for q in snap["questions"] if owned and q.get("asking") == bid
             and "inbox:pending" in q["labels"]]
    if posts:
        return "inbox-pending", posts
    if working:
        return "working", []
    if s and s.get("state") == "blocked":
        return "waiting", []
    return "idle", []


def done_keys(issue):
    return {c["text"][len("reconcile: "):].strip() for c in issue.get("comments") or []
            if (c.get("text") or "").startswith("reconcile: ")}


def classify(snap, now, stall_after_min=30, limits=None):
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
        cls, keys = classify_bead(bid, b, snap, now, timedelta(minutes=stall_after_min), sessions, leases, threads,
                                  limits)
        classes[bid] = cls
        actions += [{"item": bid, "class": cls, "action": k} for k in keys if k not in done_keys(b)]
    return {"classes": classes, "actions": actions}


# --- W14 dispatch plan: pure (budget_check aside) ---

def bead_files(issue):
    """The paths of the bead's `Files:` list (`- <path> ...` lines, up to the first other line); `(none ...)` is no
    path. A path under a known repo (dotfiles, Precog) becomes repo-relative, so `~/dotfiles/iar/x.py` and
    `iar/x.py` are one file."""
    m = re.search(r"(?im)^\W*files:[ \t]*\n((?:[ \t]*[-*] .*(?:\n|$))+)", issue.get("description") or "")
    items = [line.strip()[2:].strip() for line in (m[1].splitlines() if m else [])]
    return {repo_path(x.split()[0].strip("`")) for x in items if x and not x.startswith("(")}


def repo_path(path):
    # ponytail: two known repo roots; a bead in another repo compares by its paths as written
    p = os.path.normpath(os.path.expanduser(path))
    for root in (f"{HOME}/dotfiles", PRECOG):
        if p.startswith(root + "/"):
            return p[len(root) + 1:]
    return p


def done_when(issue):
    """The command of the bead's `Done when:` line: its first `backticked` span."""
    text = "\n".join(issue.get(k) or "" for k in ("description", "acceptance_criteria"))
    m = re.search(r"(?im)^\W*done[- ]when:[^`\n]*`([^`\n]+)`", text)
    return m[1].strip() if m else None


def next_try(issue):
    """bin/bg's next attempt number: one more than any `attempt:` or `attempt-failed:` comment."""
    return max((int(m[1]) for c in issue.get("comments") or [] if (m := TRY_RE.match(c.get("text") or ""))),
               default=0) + 1


def backoff_until(issue, max_ms):
    """Dispatch retry: n `attempt-failed:` comments since the last `attempt:` comment wait 10 s * 2^(n-1), capped
    at max_ms, after the latest failure. None: nothing to wait for."""
    fails = []
    for c in issue.get("comments") or []:
        text = c.get("text") or ""
        if ATTEMPT_RE.match(text):
            fails = []
        elif text.startswith("attempt-failed: "):
            fails.append(parse_time(c.get("created_at")))
    if not fails or fails[-1] is None:
        return None
    return fails[-1] + timedelta(milliseconds=min(10_000 * 2 ** (len(fails) - 1), max_ms))


def gate(issue, busy, budget_check):
    """The automatable part of the dispatch-bead readiness gate. None: dispatch. "wait: ...": its files overlap
    running work, try again next tick. Anything else: the reason to reject it."""
    files = bead_files(issue)
    if not files:
        return "no `Files:` list"
    if not done_when(issue):
        return "no `Done when:` line with a `command`"
    text = "\n".join(issue.get(k) or "" for k in ("title", "description", "acceptance_criteria"))
    has_block = re.search(r"(?m)^```[^\n]*\n\s*budget", text)
    if not has_block and (word := COMPUTE_RE.search(text)):
        return f"mentions compute ({word[0]!r}) but has no `budget:` block"
    if has_block:
        try:
            budget_check(issue["id"])
        except budget.BudgetError as e:
            return f"budget block not valid: {e}"
        except Exception as e:  # the tracker failed: not a verdict on the bead, try again next tick
            return f"wait: budget check failed: {type(e).__name__}: {e}"
    if overlap := sorted(files & busy):
        return f"wait: files in use by running work: {', '.join(overlap)}"
    return None


def budget_check(bid):
    """iar/budget.py <bead> exits 0: raises budget.BudgetError otherwise. Its report is not printed."""
    with contextlib.redirect_stdout(io.StringIO()):
        budget.check(bid)


def plan_dispatch(snap, now, config, budget_check):
    """W14: this tick's dispatch and reject-auto actions. Candidates by priority, then creation time. Starts are
    limited by the free slots (agent.max_concurrent and max_concurrent_by_state.in_progress, counted over the
    loop-owned in_progress beads) and by limits.max_starts_per_tick."""
    lim = {**LIMITS, **(config.get("limits") or {})}
    agent = config["agent"]
    # Running attempts: a bin/bg claim (agent:<bead>#<n>) on an in_progress bead, with or without its attempt comment
    # (bin/bg writes it last, after the session starts, so a claim alone is a start in flight), plus any bead whose
    # recorded attempt session still runs whatever the bead's status (closed, deferred: stopped later).
    live_sids = {s.get("sessionId") for s in snap["sessions"] if running(s)}
    owned = {bid for bid, b in snap["issues"].items()
             if (b["status"] == "in_progress" and re.match(rf"^agent:{re.escape(bid)}#\d+$", b.get("assignee") or ""))
             or any(x["session"] in live_sids for x in attempts(b).values())}
    cap = min(agent["max_concurrent"], agent["max_concurrent_by_state"].get("in_progress", agent["max_concurrent"]))
    starts = max(0, min(cap - len(owned), lim["max_starts_per_tick"]))
    live = {s["bead"] for s in snap["sessions"] if s.get("bead") and running(s)}
    busy = set()
    for bid, b in snap["issues"].items():
        if b["status"] == "in_progress" or bid in live:
            busy |= bead_files(b)
    out = []
    for c in sorted(snap["candidates"], key=lambda c: (c.get("priority", 9), c.get("created_at") or "")):
        if c["status"] != "open" or c.get("assignee") or set(c.get("labels") or []) & {"no-swarm", QUARANTINE, AUTO_BLOCKED}:
            continue
        stacked = snap.get("stacked") or {}
        if why := next((stacked[x[3:]] for x in c.get("labels") or [] if x.startswith("pr:") and x[3:] in stacked), None):
            snap.setdefault("held", {})[c["id"]] = why  # W9b: stays ready, no reject; the jsonl line shows the reason
            continue
        n = sum(1 for k in done_keys(c) if k.startswith("reject-auto "))
        reject = {"item": c["id"], "class": "dispatch", "action": f"reject-auto count={n + 1}"}
        if (made := next_try(c) - 1) >= lim["max_attempts"]:  # started and failed attempts both count
            out.append(dict(reject, over_limit=True, detail=f"over-limit: {made} attempts (started or failed) "
                                                            f"reached limits.max_attempts {lim['max_attempts']}"))
            continue
        if (until := backoff_until(c, config["max_retry_backoff_ms"])) and now < until:
            continue
        why = gate(c, busy, budget_check)
        if why and not why.startswith("wait: "):
            out.append(dict(reject, detail=why))
        if why or starts <= 0:
            continue
        starts -= 1
        busy |= bead_files(c)
        out.append({"item": c["id"], "class": "dispatch", "action": f"dispatch attempt={next_try(c)}",
                    "done_when": done_when(c)})
    return out


# --- execute ---

# verb (first word of the action key) -> (visible(action, snap) -> bool, act(action, snap)).
# visible: the action's own effect already shows (for example the lease is already returned).
EXECUTORS = {}


PENDING = "reconcile-pending"  # label: a reconcile-start: comment with no reconcile: comment yet


def open_intents(issue):
    """{key: class} of actions started (`reconcile-start: <class> <key>`) and not yet finished
    (`reconcile: <key>`) or dropped (`reconcile-dropped: <key>`). Comments are read in order."""
    started = {}
    for c in issue.get("comments") or []:
        verb, _, rest = (c.get("text") or "").partition(": ")
        if verb == "reconcile-start":
            cls, _, key = rest.strip().partition(" ")
            started[key] = cls
        elif verb in ("reconcile", "reconcile-dropped"):
            started.pop(rest.strip(), None)
    return started


def finish(bid, text):
    """Write the closing comment; drop the label when no intent of the bead is left open."""
    ledger.comment(bid, text)
    if not open_intents(ledger.show(bid)):
        ledger.label(bid, remove=[PENDING])


def switches():
    """WORKFLOW.md `actions`, read again for each action (so --kill stops a running tick). While the file does not
    load, the last good switches with dispatch off (W14: a bad WORKFLOW.md blocks dispatch only)."""
    try:
        return workflow.load()[0]["actions"]
    except workflow.WorkflowError:
        if LAST_GOOD[0] is None:
            raise
        return {**LAST_GOOD[0]["actions"], "dispatch": False}


def execute(snap, actions, executors=None):
    """Apply each action of a switched-on class once, and finish the open intents a crash left.
    Returns [{item, action, outcome}]."""
    executors = EXECUTORS if executors is None else executors
    out = []
    if snap["errors"]:
        return out  # E8b: a failing tick does nothing
    try:
        any_on = any(switches().values())
    except workflow.WorkflowError as e:
        return [{"item": "-", "action": "-", "outcome": f"error: {e}"}]  # no readable switches: act on nothing
    work = [dict(a, proposed=True) for a in actions]
    seen = {(a["item"], a["action"]) for a in actions}
    for bid, b in sorted(snap["issues"].items()):
        intents = open_intents(b)
        work += [{"item": bid, "class": cls, "action": key, "proposed": False}
                 for key, cls in intents.items() if (bid, key) not in seen]
        if any_on and not intents and PENDING in (b.get("labels") or []):  # crash after the comment, before the unlabel
            try:
                ledger.label(bid, remove=[PENDING])
                out.append({"item": bid, "action": f"unlabel {PENDING}", "outcome": "done"})
            except Exception as e:
                out.append({"item": bid, "action": f"unlabel {PENDING}", "outcome": f"error: {type(e).__name__}: {e}"})
    for a in work:
        def note(outcome):
            out.append({"item": a["item"], "action": a["action"], "outcome": outcome})
        try:
            if not switches().get(a["class"]):
                note("off")
                continue
        except workflow.WorkflowError as e:
            note(f"error: {e}")
            break  # no readable switches: act on nothing
        ex = executors.get(a["action"].split()[0])
        if not ex:
            note("no-executor")
            continue
        bid, key = a["item"], a["action"]
        try:
            fresh = ledger.show(bid)
            if key in done_keys(fresh):
                note("recorded")
                continue
            visible, act = ex
            if visible(a, snap):  # done before a crash, or not needed: record it, do not act again
                finish(bid, f"reconcile: {key}")
                note("recovered")
                continue
            if not a["proposed"]:  # started, no effect, and no longer wanted
                finish(bid, f"reconcile-dropped: {key}")
                note("dropped")
                continue
            if key not in open_intents(fresh):
                ledger.label(bid, add=[PENDING])  # src_br fetches labelled beads, so a crash after act is found
                ledger.comment(bid, f"reconcile-start: {a['class']} {key}")
            act(a, snap)
            finish(bid, f"reconcile: {key}")
            note("done")
        except Exception as e:  # one failed action does not stop the others; no comment, so the next tick retries
            note(f"error: {type(e).__name__}: {e}")
    return out


def set_switches(enable=None, path=None):
    """--enable <class>: switch one class on. --kill (enable None): switch every class off.
    Edits only the switch lines of WORKFLOW.md, checks the result loads, then replaces the file."""
    path = os.path.realpath(path or os.environ.get("IAR_WORKFLOW") or os.path.join(IAR, "WORKFLOW.md"))
    config, _ = workflow.load(path)
    if enable is not None and enable not in config["actions"]:
        raise workflow.WorkflowError(f"unknown class {enable!r}; classes: {', '.join(config['actions'])}")
    with open(path) as f:
        text = f.read()
    m = re.search(r'"actions": \{[^}]*\}', text)
    names = config["actions"] if enable is None else [enable]
    block = m[0]
    for name in names:
        block = re.sub(rf'("{re.escape(name)}": )(true|false)', rf"\g<1>{'true' if enable else 'false'}", block)
    text = text[:m.start()] + block + text[m.end():]
    with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(path), delete=False) as f:
        f.write(text)
    os.chmod(f.name, os.stat(path).st_mode & 0o7777)
    try:
        new = workflow.load(f.name)[0]["actions"]
        if new != {k: (enable is not None if k in names else v) for k, v in config["actions"].items()}:
            raise workflow.WorkflowError(f"{path}: switch edit did not apply: {new}")
    except BaseException:
        os.unlink(f.name)
        raise
    os.replace(f.name, path)
    return new


# --- W12 actions: each is (visible, act) in EXECUTORS ---

def key_field(a, name):
    return re.search(rf"\b{name}=(\S+)", a["action"])[1]


def session_of(a, snap):
    """(attempt record, the session's row in this tick or {}) for the attempt the action names."""
    att = attempts(snap["issues"][a["item"]])[int(key_field(a, "attempt"))]
    return att, next((s for s in snap["sessions"] if s["sessionId"] == att["session"]), {})


def note(a, body):
    ledger.comment(a["item"], f"reconcile-note: {a['action']}\n{body}")


def noted(a, snap):
    """A flag's effect is its note."""
    return any((c.get("text") or "").startswith(f"reconcile-note: {a['action']}\n")
               for c in ledger.show(a["item"]).get("comments") or [])


def save_output(a, s):
    """Before a flag or a stop: the last 40 lines of the session's final assistant text, once per action."""
    head = f"last-output: {a['action']} session={s.get('sessionId')}"
    if any((c.get("text") or "").startswith(head) for c in ledger.show(a["item"]).get("comments") or []):
        return
    ledger.comment(a["item"], f"{head}\n````\n{s.get('last_output') or '(no assistant text in the transcript)'}\n````")


def agent_rows():
    return check_agents(json.loads(sh([f"{BIN}/claude", "agents", "--json", "--all"])))


def stop_session(sid, short):
    """claude stop, then wait until claude agents shows the session stopped or without a process."""
    sh([f"{BIN}/claude", "stop", short or sid])
    for _ in range(STOP_WAIT_S):
        row = next((r for r in agent_rows() if r["sessionId"] == sid), None)
        if row is None or row.get("state") == "stopped" or not row.get("pid"):
            return
        time.sleep(1)
    raise RuntimeError(f"session {sid} still running after claude stop")


class NoRex(RuntimeError):
    pass


def rex(*args, check=True):
    """One rex call; never starts a rex server. NoRex when the binary is missing."""
    path = shutil.which("rex") or (REX_APP if os.path.exists(REX_APP) else None)
    if not path:
        raise NoRex("rex not found on PATH or in Rex Beta.app")
    return sh([path, "--autostart=false", *args], check=check)


def pane_state(screen):
    """ready (empty input box), typed (text in the input box), dialog, or None (no input box yet). The input box is
    the `❯` line right under a rule, at the bottom of the pane."""
    tail = [x for x in screen.splitlines() if x.strip()][-12:]
    for i in range(len(tail) - 1, 0, -1):
        if tail[i].startswith("❯") and tail[i - 1].startswith("─"):
            return "ready" if tail[i] == "❯" or not tail[i][1:].strip() else "typed"
    return "dialog" if DIALOG_RE.search("\n".join(tail)) else None


def send(s, slot, text, a):
    """Give the session text as a user turn; never stop it. A running session: through rex (module docstring). A
    session with no process: `claude --bg --resume`; the resumed id must be this session, else the copy is stopped."""
    if running(s):
        return rex_send(s, text, a)
    out = sh([f"{BIN}/claude", "--bg", "--resume", s["sessionId"], text], cwd=slot)
    m = re.search(r"backgrounded\W+([\w-]+)", out)
    if not m:
        raise RuntimeError(f"claude --bg --resume printed no session id: {out.strip()[:200]}")
    for _ in range(STOP_WAIT_S):
        row = next((r for r in agent_rows() if r.get("id") == m[1]), None)
        if row and row["sessionId"] != s["sessionId"]:
            stop_session(row["sessionId"], row.get("id"))
            raise RuntimeError(f"resume started a copy {row['sessionId']} of {s['sessionId']}; the copy is stopped")
        if row:
            return
        time.sleep(1)
    raise RuntimeError(f"resumed session {m[1]} not in claude agents after {STOP_WAIT_S}s")


def rex_send(s, text, a):
    """Attach in a new rex session, check the pane, type text and Enter, and always kill the rex session."""
    sid, short = s["sessionId"], s.get("id") or s["sessionId"]
    row = next((r for r in agent_rows() if r["sessionId"] == sid), {})
    if row.get("state") in ("blocked", "waiting") or row.get("status") == "waiting":
        raise RuntimeError(f"session {sid} waits on a dialog; no text sent")
    name = f"iar-nudge-{short}-{time.time_ns()}"
    try:
        rex("new", name, "--", f"{BIN}/claude", "attach", short)
    except (NoRex, SourceError) as e:
        key = f"nudge-no-rex attempt={key_field(a, 'attempt')}"
        if not any((c.get("text") or "").startswith(f"reconcile-note: {key}\n")
                   for c in ledger.show(a["item"]).get("comments") or []):
            ledger.comment(a["item"], f"reconcile-note: {key}\nNo nudge: rex is not available ({e}). Start Rex; "
                                      f"the next tick sends it.")
        raise NoRex(f"no rex: {e}") from e
    try:
        for _ in range(STOP_WAIT_S):
            state = pane_state(rex("capture", "-s", name, "--trim"))
            if state == "ready":
                break
            if state:
                raise RuntimeError(f"session {sid} shows {'a dialog' if state == 'dialog' else 'typed text'}; "
                                   "no text sent")
            time.sleep(1)
        else:
            raise RuntimeError(f"no Claude input box in rex session {name} after {STOP_WAIT_S}s")
        rex("send", "-s", name, text)
        rex("send-key", "-s", name, "Enter")
    finally:
        rex("kill", name, check=False)


def inbox(tool, args):
    """One Inbox MCP tool call through `plannotator inbox mcp` (IAR_INBOX_CMD overrides it, as in question.py)."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "iar-reconcile", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}}]
    p = subprocess.run(shlex.split(os.environ.get("IAR_INBOX_CMD", "plannotator inbox mcp")),
                       input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
                       timeout=TIMEOUT_S)
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") == 2:
            if m.get("error") or (m.get("result") or {}).get("isError"):
                raise RuntimeError(f"inbox {tool}: {json.dumps(m.get('error') or m['result'])[:300]}")
            return m["result"]
    raise RuntimeError(f"inbox {tool}: no answer (exit {p.returncode}): {p.stderr.strip()[-200:]}")


def still_proposed(a, snap):
    """Classify fresh sources again: the action is still what this bead needs (its session did not resume, its
    branch did not change)."""
    fresh = snap["refresh"]()
    if fresh["errors"]:
        raise RuntimeError(f"fresh sources failed: {fresh['errors']}")
    return any(x["item"] == a["item"] and x["action"] == a["action"]
               for x in classify(fresh, datetime.now(timezone.utc))["actions"])


def act_release(a, snap):
    att, s = session_of(a, snap)
    if not still_proposed(a, snap):
        raise RuntimeError("no longer a zombie with a pushed branch on fresh sources")
    ledger.update(a["item"], status="open", assignee="")
    note(a, f"Released the claim: session {att['session']} is {s.get('state') or 'not listed'} with no process, "
            f"and branch {att['branch']} is pushed. The bead is open again.")


def act_flag_zombie(a, snap):
    att, s = session_of(a, snap)
    note(a, f"Zombie: the bead is in progress, session {att['session']} is {s.get('state') or 'not listed'} with no "
            f"process, and branch {att['branch']} is not pushed, so the claim stays. "
            f"Resume it: cd {att['slot']} && claude --resume {att['session']}")


def lease_of(a, snap):
    slot, holder = key_field(a, "slot"), key_field(a, "holder")
    return next((x for x in snap["slots"] if x["slot"] == slot and x["holder"] == holder), None)


def lease_fails(a, snap):
    lease = lease_of(a, snap)
    sessions = {s["sessionId"]: s for s in snap["sessions"]}
    return lease, preflight(a["item"], snap["issues"][a["item"]], int(HOLDER_RE.match(lease["holder"])[2]),
                            lease, snap, sessions)


def act_return_lease(a, snap):
    """E10: the preflight runs again on fresh sources right before the return."""
    fresh = snap["refresh"]()
    if fresh["errors"]:
        raise RuntimeError(f"preflight sources failed: {fresh['errors']}")
    if not lease_of(a, fresh):
        return  # already returned
    lease, fails = lease_fails(a, fresh)
    if fails:
        raise RuntimeError(f"release preflight failed: {'; '.join(fails)}")
    sh([f"{BIN}/treehouse", "return", lease["path"], "--force", "--if-lease-holder", lease["holder"]], cwd=PRECOG)


def act_flag_lease(a, snap):
    lease, fails = lease_fails(a, snap)
    note(a, f"Lease {lease['holder']} on slot {lease['slot']} ({lease.get('path')}) is not returned: "
            f"{'; '.join(fails)}. Push or clean the slot, then the next tick returns it.")


def act_resolve_thread(a, snap):
    tid, b = key_field(a, "thread"), snap["issues"][a["item"]]
    inbox("send_message", {"body": f"Landed: {a['item']} is closed ({b.get('close_reason') or 'no reason given'}).",
                           "reply_to": tid, "idempotency_key": f"landed-{tid}", "project_path": INBOX_PROJECT})
    inbox("resolve_message", {"message_id": tid, "resolved": True})


NUDGES = {"nudge": "status?",
          "limit-nudge": "You reached the {limit} limit: commit, push your branch, write a status comment, then stop."}


def nudge_text(a):
    verb = a["action"].split()[0]
    return NUDGES[verb].format(limit=key_field(a, "limit")) if verb == "limit-nudge" else NUDGES[verb]


def transcript_has(path, text, since):
    """A user turn in the transcript at or after `since` whose content holds `text`."""
    needle = json.dumps(text, ensure_ascii=False)[1:-1]  # as the text sits in a JSON line (newlines escaped)
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if needle not in line or '"user"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                at = parse_time(r.get("timestamp"))
                if r.get("type") == "user" and at and at >= since and needle in json.dumps((r.get("message") or {}).get("content"), ensure_ascii=False):
                    return True
    except OSError:
        pass
    return False


def delivered(a, snap):
    """A nudge's effect: its note, or (a crash after the resume, before the note) the session's transcript has a
    user turn with the nudge text after the action started. Keeps a nudge once per attempt."""
    if noted(a, snap):
        return True
    att, _ = session_of(a, snap)
    return got_since(att["session"], nudge_text(a), started_at(a))


def started_at(a):
    """When the action was first started: its earliest `reconcile-start:` comment, or None."""
    starts = [parse_time(c.get("created_at")) for c in ledger.show(a["item"]).get("comments") or []
              if (c.get("text") or "").startswith("reconcile-start: ") and c["text"].rstrip().endswith(f" {a['action']}")]
    return min(starts, default=None)


def got_since(sid, text, since):
    """The session's transcript has a user turn holding text at or after since."""
    path = since and transcript_facts(sid).get("transcript")
    return bool(path and transcript_has(path, text, since))


def act_nudge(a, snap):
    att, s = session_of(a, snap)
    send(s, att["slot"], nudge_text(a), a)
    note(a, f"Sent session {att['session']}: {nudge_text(a)}")


def act_flag_stalled(a, snap):
    att, s = session_of(a, snap)
    save_output(a, s)
    note(a, f"Stalled after one nudge: session {att['session']} works with no transcript write and no child CPU. "
            f"Not stopped (no automatic kill). Look: claude attach {s.get('id') or att['session']}")


def act_quarantine(a, snap):
    att, s = session_of(a, snap)
    save_output(a, s)
    ledger.label(a["item"], add=[QUARANTINE, "needs-human"])
    note(a, f"Quarantined: attempt {key_field(a, 'attempt')} still works past its limit after the limit nudge "
            f"(or an earlier attempt was nudged too). bin/bg does not dispatch it until you remove `{QUARANTINE}`.")


def act_stop_session(a, snap):
    att, s = session_of(a, snap)
    b = snap["issues"][a["item"]]
    save_output(a, s)
    stop_session(att["session"], s.get("id"))
    why = b["status"] if b["status"] in ("closed", "deferred") else "no longer labelled auto"
    note(a, f"Stopped session {att['session']}: the bead is {why}. The worktree stays; the slot is returned once "
            f"the release preflight passes.")


def lease_gone(a, snap):
    return lease_of(a, snap) is None


def released(a, snap):
    fresh = ledger.show(a["item"])
    holder = f"agent:{a['item']}#{key_field(a, 'attempt')}"
    return not (fresh["status"] == "in_progress" and fresh.get("assignee") == holder)


EXECUTORS.update({
    "release": (released, act_release),
    "flag-zombie": (noted, act_flag_zombie),
    "return-lease": (lease_gone, act_return_lease),
    "flag-lease": (noted, act_flag_lease),
    "resolve-thread": (lambda a, snap: key_field(a, "thread") not in {t["id"] for t in snap["inbox"]},
                       act_resolve_thread),
    "nudge": (delivered, act_nudge),
    "flag-stalled": (noted, act_flag_stalled),
    "limit-nudge": (delivered, act_nudge),
    "quarantine": (lambda a, snap: QUARANTINE in (ledger.show(a["item"]).get("labels") or []), act_quarantine),
    "stop-session": (lambda a, snap: not running(session_of(a, snap)[1]), act_stop_session),
})


# --- W13 actions: questions (answer-pending, question-deadline, malformed-question, inbox-pending) ---

def q_of(a):
    return ledger.show(key_field(a, "q"))


def answer_text(qid, q, ans):
    """What a delivery sends: the answer verbatim on the first line, then what it answers."""
    f = question.parse_body(q.get("description"))
    how = "your declared default, applied at the deadline" if ans["source"] == "timeout" \
        else f"Tal's answer (source: {ans['source'] or 'tracker'})"
    return (f"ANSWER {qid}: {ans['text']}\n\nThis is {how} to your question: {f.get('question')}\n"
            "Act on it now. Do not ask again with AskUserQuestion.")


def act_import(a, snap):
    """Tal's Inbox (or Linear, W25b) answer goes through iar/answer (question.answer), with the time he gave it."""
    got = snap["inbox_answers"][key_field(a, "q")]
    question.answer(key_field(a, "q"), got["text"], at=got["at"], source=got.get("source", "inbox"))


def imported(a, snap):
    return any(re.search(r"\n-- at: \S+ source: (inbox|linear)\Z", c.get("text") or "") for c in q_of(a).get("comments") or [])


def act_deliver(a, snap):
    """Send the recorded answer to the attempt's session (resumed if stopped), check that its transcript has the new
    turn, then label the question `delivered` and close it. A crash after the send: the turn is in the transcript (or
    the label is set), so the retry does not send again."""
    qid = key_field(a, "q")
    q = ledger.show(qid)
    ans = question.effective_answer(q)
    if not ans:
        raise RuntimeError(f"{qid} has no recorded answer")
    got = ans["source"] == "timeout" and inbox_answers(*inbox_records()[:2]).get(qid)
    if got:  # E2b: Tal's Inbox answer that landed after the snapshot still goes in before the default is sent
        question.answer(qid, got["text"], at=got["at"], source="inbox")
        q = ledger.show(qid)
        ans = question.effective_answer(q)
    text = answer_text(qid, q, ans)
    att, s = session_of(a, snap)
    since = started_at(a)
    if "delivered" not in (q.get("labels") or []) and not got_since(att["session"], text, since):
        send(s or {"sessionId": att["session"]}, att["slot"], text, a)
        for _ in range(STOP_WAIT_S):
            if got_since(att["session"], text, since):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"session {att['session']} shows no turn with the answer after {STOP_WAIT_S}s")
    ledger.label(qid, add=["delivered"])
    ledger.close(qid, reason=f"delivered to {a['item']}#{key_field(a, 'attempt')}")


def act_timeout(a, snap):
    """Reversible and not gated, deadline passed: iar/answer --timeout writes the default (it refuses otherwise)."""
    question.answer(key_field(a, "q"), "", timeout=True)


def act_reping(a, snap):
    """Not reversible: one reminder in the question's thread at the deadline, then it waits."""
    qid = key_field(a, "q")
    f = question.parse_body(q_of(a).get("description"))
    if f.get("inbox_thread") in (None, "", "pending"):
        raise RuntimeError(f"{qid} is not posted to the Inbox yet")
    inbox("send_message", {"body": f"Reminder: {qid} passed its deadline ({f.get('deadline')}) with no answer. It is "
                                   f"not reversible, so no default applies and {f.get('asking')} waits for you.\n\n"
                                   f"{f.get('question')}",
                           "reply_to": f["inbox_thread"], "idempotency_key": f"reping-{qid}",
                           "project_path": f.get("inbox_project") or INBOX_PROJECT})


def act_post(a, snap):
    """E5: a question bead the Inbox did not take when it was asked (label inbox:pending) is posted now."""
    q = q_of(a)
    question.post(q["id"], question.parse_body(q.get("description")))


def act_flag_malformed(a, snap):
    att, s = session_of(a, snap)
    note(a, f"Malformed question: session {att['session']} ended its turn with a `QUESTION:` line that names no open "
            f"question bead of {a['item']}, so the loop cannot time it out or deliver its answer. The board lists it "
            f"under Needs you. Answer in the session (claude attach {s.get('id') or att['session']}), or have it file "
            f"the question with iar/ask. Its line:\n{s.get('last_message') or '(not in this tick)'}")


EXECUTORS.update({
    "import-answer": (imported, act_import),
    "deliver-answer": (lambda a, snap: q_of(a)["status"] == "closed", act_deliver),
    "timeout": (lambda a, snap: q_of(a)["status"] == "closed" or bool(question.effective_answer(q_of(a))),
                act_timeout),
    "reping": (lambda a, snap: False, act_reping),  # the Inbox idempotency key makes a repeat send a no-op
    "post-question": (lambda a, snap: "inbox:pending" not in (q_of(a).get("labels") or []), act_post),
    "flag-malformed": (noted, act_flag_malformed),
})


# --- W14 actions: dispatch, reject-auto ---

def tried(a, snap):
    """bin/bg wrote the attempt (`attempt: <n>`) or its failure (`attempt-failed: <step> attempt=<n>`)."""
    n = key_field(a, "attempt")
    return any((m := TRY_RE.match(c.get("text") or "")) and m[1] == n for c in ledger.show(a["item"]).get("comments") or [])


def act_dispatch(a, snap):
    """bin/bg, marked `dispatch: auto`. A failed start is recorded by bg (`attempt-failed:`) and retried with backoff."""
    p = subprocess.run([os.environ.get("IAR_BG", BG), a["item"], "--done-when", a["done_when"], "--auto"],
                       capture_output=True, text=True, timeout=BG_TIMEOUT_S)
    if not tried(a, snap):
        raise RuntimeError(f"bin/bg exit {p.returncode} wrote no attempt {key_field(a, 'attempt')}: "
                           f"{(p.stderr or p.stdout).strip()[-300:]}")


def act_reject(a, snap):
    extra = [QUARANTINE, "needs-human"] if a.get("over_limit") else []
    note(a, f"Not dispatched automatically: {a['detail']}. Fix the bead, then remove the label"
            f"{'s' if extra else ''} `{'`, `'.join([AUTO_BLOCKED, *extra[:1]])}`.")
    ledger.label(a["item"], add=[AUTO_BLOCKED, *extra])


EXECUTORS.update({
    "dispatch": (tried, act_dispatch),
    "reject-auto": (lambda a, snap: AUTO_BLOCKED in (ledger.show(a["item"]).get("labels") or []), act_reject),
})


# --- tick ---

def state_dir():
    d = os.environ.get("IAR_STATE_DIR") or f"{HOME}/.local/state/iar"
    os.makedirs(d, exist_ok=True)
    return d


def tick(d, dry_run, sources=None, executors=None, board=False, mirror=None, linear_answers=None):
    """One tick under the lock. Returns (exit code, the line written to reconcile.jsonl or None)."""
    lock = open(os.path.join(d, "reconcile.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 75, None
    try:
        now = datetime.now(timezone.utc)
        good, config_error = os.path.join(d, "workflow-good.json"), None
        try:
            config, _ = workflow.load()
            with tempfile.NamedTemporaryFile("w", dir=d, delete=False) as f:
                json.dump(config, f)
            os.replace(f.name, good)
        except workflow.WorkflowError as e:  # W14: blocks dispatch only; the rest runs on the last good config
            config, config_error = read_json(good) or None, f"config error: {e}"
        LAST_GOOD[0] = config
        if config:
            DISPATCH_LABEL[0] = config["tracker"]["dispatch_label"]
            os.environ.setdefault("BEADS_DIR", config["tracker"]["beads_dir"])
            snap = snapshot(now, sources, state_dir=d)
            stall, limits = config["stall_after_min"], config.get("limits")
        else:
            snap, stall = {**json.loads(json.dumps(DEFAULTS)), "errors": {"workflow": config_error}}, 30
            limits = None
        snap["config_error"] = config_error
        mirror_on = bool(config) and config["actions"].get("linear-mirror", False)
        linear_error = None
        if not snap["errors"] and (dry_run or mirror_on) and snap["questions"]:
            # W25b: Tal's Linear answers join his Inbox answers; the earlier one is imported (first answer wins).
            got, linear_error = (linear_answers or linear_mirror.answers)(snap["questions"])
            for qid, ans in got.items():
                have = snap["inbox_answers"].get(qid)
                if not have or question.parse_time(ans["at"]) < question.parse_time(have["at"]):
                    snap["inbox_answers"][qid] = ans
        result = classify(snap, now, stall, limits)
        if not snap["errors"] and not config_error:  # dispatch preflight: WORKFLOW.md loads, every source answered
            result["actions"] += plan_dispatch(snap, now, config, budget_check)
        line = {"at": now.isoformat(), "dry_run": dry_run, "errors": snap["errors"], "config_error": config_error,
                **({"held": snap["held"]} if snap.get("held") else {}),
                **({"linear_answers_error": linear_error} if linear_error else {}), **result}
        if not dry_run:
            line["executed"] = execute(snap, result["actions"], executors)
        # W25: Linear mirror. A dry run lists its writes; a live tick writes them when the switch is on.
        if not snap["errors"] and (dry_run or mirror_on):
            line["linear"] = (mirror or linear_mirror.run)(dry_run, snap["inbox"], snap["inbox_url"])
        with open(os.path.join(d, "reconcile.jsonl"), "a") as f:
            f.write(json.dumps(line) + "\n")
        if board:
            import board as board_mod
            board_mod.write(board_mod.render(board_mod.classify(snap, now)), now)
        return 0, line
    finally:
        lock.close()


def main(argv):
    if "--self-test" in argv:
        self_test()
        return 0
    if "--kill" in argv or "--enable" in argv:
        cls = argv[argv.index("--enable") + 1:][:1] if "--enable" in argv else [None]
        if not cls:
            print("reconcile: usage: --enable <class>", file=sys.stderr)
            return 2
        good = os.path.join(state_dir(), "workflow-good.json")
        if cls[0] is None and (cached := read_json(good)):  # --kill also stops actions run on the last good config
            cached["actions"] = {k: False for k in cached["actions"]}
            with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(good), delete=False) as f:
                json.dump(cached, f)
            os.replace(f.name, good)
        try:
            print(json.dumps(set_switches(cls[0])))
        except workflow.WorkflowError as e:
            print(f"reconcile: {e}", file=sys.stderr)
            return 2
        return 0
    os.environ["PATH"] = f"{BIN}:/opt/homebrew/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"
    code, line = tick(state_dir(), "--dry-run" in argv, board="--board" in argv)
    if code == 75:
        print("reconcile: another tick holds the lock", file=sys.stderr)
        return code
    print(json.dumps({"errors": line["errors"], "actions": len(line["actions"]),
                      **({"executed": line["executed"]} if "executed" in line else {}),
                      **({"linear": line["linear"]} if "linear" in line else {})}))
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
        "errors": {}, "git": {"precog-zp#1": {"pushed": True}, "precog-sl#1": {"pushed": True}},
        "slot_git": {x: {"dirty": False, "unpushed": False} for x in ("4", "6")},
        "issues": {b["id"]: b for b in [
            bead("precog-ap"), bead("precog-dl"), bead("precog-dl2"), bead("precog-mq"), bead("precog-mq2"), bead("precog-mq3"), bead("precog-st"),
            bead("precog-zm"), bead("precog-zp"), bead("precog-sl", status="open", assignee=""),
            bead("precog-ct", status="closed"), bead("precog-wk"), bead("precog-wt"), bead("precog-id"),
            bead("precog-ex", n=0, assignee="factory-wf"), bead("precog-nl", n=0, status="open", assignee=""), bead("precog-ex2", assignee="factory-wf"),  # old bg lease, foreign claim
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
                      q("precog-q7", "precog-ex", ["answered"]), q("precog-q8", "precog-wk", deadline=t(-60)),
                      q("precog-q9", "precog-wt", deadline=t(-60)), q("precog-q10", "precog-mq3", deadline=t(-60))],
        "sessions": [
            sess("precog-ap", state="blocked"), sess("precog-dl", state="blocked"), sess("precog-dl2", state="blocked"),
            sess("precog-mq", state="blocked", last_message="QUESTION: should I ship?"),
            sess("precog-mq2", state="blocked", last_message="QUESTION: precog-q8 not mine?"),
            sess("precog-mq3", state="blocked", last_message="QUESTION: precog-q10-junk ok?"),
            sess("precog-st", state="working", transcript_at=t(45), cpu_active=False, **live),
            sess("precog-c1", state="working", transcript_at=t(45), cpu_active=False, **live),
            sess("precog-ag", state="working", transcript_at=t(45), cpu_active=False, **live),
            sess("precog-zm", state="failed"), sess("precog-zp", state="done"),
            sess("precog-wk", status="busy", transcript_at=t(45), cpu_active=None, **live),
            sess("precog-wt", state="blocked", last_message="QUESTION: precog-q9. ok?"),  # blocked, no pid: not zombie
            sess("precog-id", state="done", status="idle", **live),
            sess("precog-rp", state="done", status="idle", **live),
        ],
        "slots": [{"slot": "4", "holder": "agent:precog-sl#1"}, {"slot": "5", "holder": "agent:precog-c2#1"},
                  {"slot": "6", "holder": "agent:precog-c3#1"}, {"slot": "7", "holder": "agent:precog-ex2#1"},
                  {"slot": "8", "holder": "agent:task-x"}, {"slot": "10", "holder": "agent:precog-nl#1"}, {"slot": "9", "holder": "agent:dispatch-bead:precog-ex"}],
        "inbox": [{"id": "m-ct", "subject": "precog-ct: integrate?", "session": "zz"},
                  {"id": "m-c3", "subject": "other", "session": "s-precog-c3-1"},
                  {"id": "m-ex", "subject": "precog-ex: hi", "session": "zz"}],
    }
    want = {
        "precog-ap": "answer-pending", "precog-dl": "question-deadline", "precog-dl2": "question-deadline",
        "precog-mq": "malformed-question", "precog-mq2": "malformed-question", "precog-mq3": "malformed-question", "precog-st": "stalled", "precog-zm": "zombie", "precog-zp": "zombie",
        "precog-sl": "stale-lease", "precog-ct": "closed-bead-thread", "precog-wk": "working",
        "precog-wt": "waiting", "precog-id": "idle", "precog-ex": "external", "precog-ex2": "external", "precog-nl": "external",
        "precog-c1": "answer-pending", "precog-c2": "zombie", "precog-c3": "stale-lease", "precog-rp": "idle",
        "precog-ag": "stalled",
    }
    got = classify(snap, now)
    assert got["classes"] == want, {k: (v, want.get(k)) for k, v in got["classes"].items() if want.get(k) != v}
    acts = {(a["item"], a["action"]) for a in got["actions"]}
    assert acts == {
        ("precog-ap", "deliver-answer q=precog-q1 attempt=1"), ("precog-dl", "timeout q=precog-q2"),
        ("precog-dl2", "reping q=precog-q3"), ("precog-mq", "flag-malformed attempt=1"),
        ("precog-mq2", "flag-malformed attempt=1"), ("precog-mq3", "flag-malformed attempt=1"),
        ("precog-st", "nudge attempt=1 count=1"), ("precog-zm", "flag-zombie attempt=1"),
        ("precog-zp", "release attempt=1"), ("precog-sl", "return-lease slot=4 holder=agent:precog-sl#1"),
        ("precog-ct", "resolve-thread thread=m-ct"),
        ("precog-c1", "deliver-answer q=precog-q6 attempt=1"), ("precog-c2", "flag-zombie attempt=2"),
        ("precog-c3", "return-lease slot=6 holder=agent:precog-c3#1"), ("precog-ag", "flag-stalled attempt=1"),
    }, acts
    assert classify(snap, now) == got  # dry run is idempotent

    # E8a: one failing source -> every item source-error, no actions. Run through snapshot() with fake sources.
    def fake(name):
        def fill(s):
            for k in {"agents": ["sessions"], "treehouse": ["slots"], "inbox": ["inbox"], "plannotator": [],
                      "br": ["issues", "questions"], "git": ["git", "slot_git"]}[name]:
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

    # Schema checks: a record missing a field classify reads fails its source.
    for check, rows in [(check_agents, [{"sessionId": "a", "kind": "background"}]),
                        (check_agents, [{"sessionId": "a", "kind": "interactive", "pid": "1"}]),
                        (check_slots, [{"name": "1", "status": "leased", "processes": []}]),
                        (check_issues, [{"id": "a", "comments": []}]),
                        (check_issues, [{"id": "a", "status": "open", "comments": [{"body": "x"}]}]),
                        (strict_lines, "{}\nnot json\n"), (strict_lines, "[1]\n")]:
        try:
            check(rows)
            raise AssertionError(f"{check.__name__} accepted {rows!r}")
        except (SourceError, ValueError):
            pass
    assert check_agents([{"sessionId": "a", "kind": "interactive", "pid": 1, "status": "idle"}])
    assert check_slots([{"name": "1", "status": "available"}]) and strict_lines("{}\n\n") == [{}]

    # src_br fetches a closed bead whose thread is linked only by its author session's name (session stopped).
    real = ledger.list_issues, ledger.show_many
    asked = []
    ledger.list_issues = lambda status=None, labels=(): [{"id": "precog-cs", "status": "closed"},
                                                         {"id": "precog-pd", "status": "open", "labels": [PENDING]}]
    ledger.show_many = lambda ids: asked.extend(ids) or [bead("precog-cs", status="closed")]
    try:
        s = {**json.loads(json.dumps(DEFAULTS)), "session_names": {"s-precog-cs-1": "precog-cs: x"},  # stopped session
             "inbox": [{"id": "m-cs", "subject": "landed", "session": "s-precog-cs-1"}]}
        src_br(s)
    finally:
        ledger.list_issues, ledger.show_many = real
    assert asked == ["precog-cs", "precog-pd"], asked  # precog-pd: an open intent
    # A malformed tracker answer is a source error: no class but source-error, no action.
    ledger.list_issues = lambda status=None, labels=(): [{"id": "precog-ap", "status": "in_progress"}]
    ledger.show_many = lambda ids: [{"id": "precog-ap", "comments": []}]  # no status
    try:
        broken_br = snapshot(now, {**fakes, "br": src_br})
    finally:
        ledger.list_issues, ledger.show_many = real
    assert list(broken_br["errors"]) == ["br"] and "status" in broken_br["errors"]["br"], broken_br["errors"]
    assert classify(broken_br, now)["actions"] == []
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
    executor_self_test()
    w12_classify_test()
    w12_executor_test()
    w12b_send_test()
    w13_test()
    w14_plan_test()
    w9b_stack_test()
    w14_tick_test()
    w25_tick_test()
    print("reconcile self-test: ok")


def w25_tick_test():
    """W25: a dry-run tick lists the Linear mirror's writes; a live tick runs it only with `linear-mirror` on; a
    source error runs no mirror. The mirror itself: linear_mirror.py --self-test."""
    saved_env = {k: os.environ.get(k) for k in ("IAR_WORKFLOW", "BEADS_DIR")}
    with tempfile.TemporaryDirectory() as d:
        wf = os.path.join(d, "WORKFLOW.md")
        with open(os.path.join(IAR, "WORKFLOW.md")) as src, open(wf, "w") as dst:
            dst.write(src.read())
        os.environ.update(IAR_WORKFLOW=wf, BEADS_DIR=os.path.join(d, ".beads"))
        calls, fail = [], [False]

        def treehouse(s):
            if fail[0]:
                raise SourceError("treehouse status: no answer in 30s")
        fakes = {"agents": lambda s: None, "treehouse": treehouse, "plannotator": lambda s: None,
                 "git": lambda s: None,
                 "inbox": lambda s: s.update(inbox=[{"id": "th", "subject": "x"}], inbox_url="http://i/")}

        def mirror(dry_run, inbox, url):
            calls.append((dry_run, inbox[0]["id"], url))
            return {"writes": ["create wl-1 [Todo] x"], "error": None}
        try:
            ledger.init("wl")
            set_switches(None, wf)
            line = tick(d, True, fakes, mirror=mirror)[1]
            assert calls == [(True, "th", "http://i/")] and line["linear"]["writes"] == ["create wl-1 [Todo] x"]
            line = tick(d, False, fakes, mirror=mirror)[1]
            assert len(calls) == 1 and "linear" not in line, line
            set_switches("linear-mirror", wf)
            tick(d, False, fakes, mirror=mirror)
            assert calls[-1] == (False, "th", "http://i/") and len(calls) == 2
            fail[0] = True
            line = tick(d, False, fakes, mirror=mirror)[1]
            assert len(calls) == 2 and "linear" not in line and line["errors"], line
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("w25 tick: ok")


def w14_plan_test():
    """W14 plan: sort, slot limits, starts per tick, gate rejection, over-limit, backoff, attempt numbering."""
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    ago = lambda s: (now - timedelta(seconds=s)).isoformat()
    body = "Files:\n- iar/{f}.py\n- `bin/{f}` (new)\n\nDone when: `iar-run-tests.sh x tests/{f}`\n"

    def cand(bid, p=2, created="2026-10-01", files=None, comments=(), labels=("auto",), **kw):
        return {"id": bid, "status": "open", "priority": p, "created_at": created, "labels": list(labels),
                "description": body.format(f=files or bid), "comments": [{"text": t, "created_at": at} for t, at in comments],
                **kw}
    config = {"agent": {"max_concurrent": 4, "max_concurrent_by_state": {"in_progress": 4}},
              "limits": {"max_attempts": 3}, "max_retry_backoff_ms": 300000}
    plan = lambda cands, issues=None, sessions=(), cfg=config, check=lambda bid: None: plan_dispatch(
        {"candidates": cands, "issues": issues or {}, "sessions": list(sessions)}, now, cfg, check)
    keys = lambda out: [(a["item"], a["action"]) for a in out]

    assert bead_files(cand("a")) == {"iar/a.py", "bin/a"} and done_when(cand("a")) == "iar-run-tests.sh x tests/a"
    assert bead_files({"description": "Files:\n- (none: branch-only chore)\n"}) == set()
    assert bead_files({"description": "Files:\n- ~/dotfiles/iar/x.py\n- ./bin/y\n"}) == {"iar/x.py", "bin/y"}
    assert bead_files({"description": f"Files:\n- {PRECOG}/hydra/z.py\n- /elsewhere/w\n"}) == {"hydra/z.py", "/elsewhere/w"}
    # Sort by priority, then creation time; 5 ready, 4 free slots: 2 starts per tick (limits.max_starts_per_tick).
    five = [cand("e", 3), cand("d", 1, "2026-10-03"), cand("c", 1, "2026-10-02"), cand("b", 2), cand("a", 0)]
    assert keys(plan(five)) == [("a", "dispatch attempt=1"), ("c", "dispatch attempt=1")], keys(plan(five))
    assert [a["done_when"] for a in plan(five)] == ["iar-run-tests.sh x tests/a", "iar-run-tests.sh x tests/c"]
    assert len(plan(five, cfg=dict(config, limits={"max_starts_per_tick": 5}))) == 4  # 4 free slots
    # Slot limits: loop-owned in_progress beads take slots (by_state counts too); external claims do not.
    mine = lambda bid: {"id": bid, "status": "in_progress", "assignee": f"agent:{bid}#1", "description": ""}
    issues = {x: mine(x) for x in ("r1", "r2", "r3")}
    issues["ext"] = dict(mine("ext"), assignee="factory-wf")
    assert keys(plan(five, issues)) == [("a", "dispatch attempt=1")]
    assert plan(five, dict(issues, r4=mine("r4"))) == []
    by_state = dict(config, agent={"max_concurrent": 9, "max_concurrent_by_state": {"in_progress": 3}})
    assert plan(five, issues, cfg=by_state) == []
    # A closed bead whose attempt session still runs holds its slot until the session stops.
    gone = {"id": "cl", "status": "closed", "assignee": None, "description": "",
            "comments": [{"text": "attempt: 1 session: s-cl slot: /s branch: b brief: /b dispatch: auto"}]}
    assert keys(plan(five, dict(issues, cl=gone), [{"sessionId": "s-cl", "alive": True}])) == []
    assert keys(plan(five, dict(issues, cl=gone), [{"sessionId": "s-cl", "state": "stopped"}])) == [("a", "dispatch attempt=1")]
    # Not candidates: no-swarm, quarantined, auto:blocked, claimed or not open.
    skip = [cand("n", labels=("auto", "no-swarm")), cand("q", labels=("auto", QUARANTINE)),
            cand("k", labels=("auto", AUTO_BLOCKED)), cand("m", assignee="someone"), cand("o", status="in_progress")]
    assert plan(skip) == []
    # Gate rejection: one reject-auto each (no start used); the count goes up after Tal clears the label.
    nofiles = cand("nf", description="Done when: `true`\n")
    nodone = cand("nd", description="Files:\n- iar/x.py\n")
    budgeted = cand("bu", description=body.format(f="bu") + "\n```\nbudget:\n  compute: nope\n```\n")
    again = cand("ag", description="no gate at all", comments=[("reconcile: reject-auto count=1", ago(99))])

    def bad_budget(bid):
        raise budget.BudgetError("compute 'nope' is not in the compute allowlist")
    out = plan([nofiles, nodone, budgeted, again, cand("ok")], check=bad_budget)
    assert sorted(keys(out)) == [("ag", "reject-auto count=2"), ("bu", "reject-auto count=1"), ("nd", "reject-auto count=1"),
                         ("nf", "reject-auto count=1"), ("ok", "dispatch attempt=1")], keys(out)
    why = {a["item"]: a.get("detail") for a in out}
    assert "Files" in why["nf"] and "Done when" in why["nd"] and "allowlist" in why["bu"], why
    assert keys(plan([budgeted])) == [("bu", "dispatch attempt=1")]  # a valid budget passes

    def tracker_down(bid):
        raise ledger.LedgerError("br show: no answer in 30s")
    assert plan([budgeted], check=tracker_down) == []  # not a verdict on the bead: wait, no reject
    gpu = cand("gp", description=body.format(f="gp") + "Run the eval on the GPU cluster.\n")
    out = plan([gpu])
    assert keys(out) == [("gp", "reject-auto count=1")] and "no `budget:` block" in out[0]["detail"], out
    # Files in use by an in_progress bead or a running session's bead: wait, no reject; and within one tick.
    busy = {"w": dict(mine("w"), description=body.format(f="a"))}
    assert plan([cand("a")], busy) == []
    ended = {"w": dict(busy["w"], status="open", assignee=None)}
    assert keys(plan([cand("a")], ended)) == [("a", "dispatch attempt=1")]
    assert plan([cand("a")], ended, [{"bead": "w", "alive": True}]) == []
    assert keys(plan([cand("x", files="same"), cand("y", files="same")])) == [("x", "dispatch attempt=1")]
    mixed = {"w": dict(mine("w"), description="Files:\n- ~/dotfiles/iar/a.py\n")}  # same file, other spelling
    assert plan([cand("a")], mixed) == []
    # Over limits.max_attempts: rejected and quarantined, not dispatched.
    att = lambda k: (f"attempt: {k} session: s{k} slot: /s branch: b brief: /b dispatch: auto", ago(9999))
    out = plan([cand("ol", comments=[att(1), att(2), att(3)])])
    assert keys(out) == [("ol", "reject-auto count=1")] and out[0]["over_limit"] and "max_attempts 3" in out[0]["detail"]
    # Failed launches count too: three attempt-failed comments reach the limit.
    fail = lambda k, s: (f"attempt-failed: lease attempt={k} boom", ago(s))
    out = plan([cand("of", comments=[fail(1, 999), fail(2, 999), fail(3, 999)])])
    assert keys(out) == [("of", "reject-auto count=1")] and out[0]["over_limit"], out
    # Backoff 10 s * 2^(n-1) after n failures since the last attempt, capped at max_retry_backoff_ms.
    many_ok = dict(config, limits={"max_attempts": 99})
    go = lambda *c: keys(plan([cand("f", comments=c)], cfg=many_ok))
    assert go(fail(1, 5)) == [] and go(fail(1, 11)) == [("f", "dispatch attempt=2")]
    assert go(fail(1, 99), fail(2, 99), fail(3, 30)) == [] and go(fail(1, 99), fail(2, 99), fail(3, 41)) != []
    many = [fail(k, 9999) for k in range(1, 10)]
    assert go(*many, fail(10, 299)) == [] and go(*many, fail(10, 301)) == [("f", "dispatch attempt=11")]
    assert go(att(1), fail(2, 11)) == [("f", "dispatch attempt=3")]  # the count restarts after a real attempt
    assert go(fail(1, 99), att(2), fail(3, 15)) != []
    print("w14 plan: ok")


def w9b_stack_test():
    """W9b: a bead of a group based on another unmerged group (or listed in stack-owners.conf) is not dispatched."""
    global STACK_OWNERS
    saved = STACK_OWNERS
    with tempfile.TemporaryDirectory() as d:
        o, w = os.path.join(d, "origin.git"), os.path.join(d, "slot")
        git = lambda repo, *a: subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                              check=True, capture_output=True, text=True).stdout.strip()
        os.mkdir(w)
        git(d, "init", "-q", "--bare", o)
        git(o, "symbolic-ref", "HEAD", "refs/heads/master")
        git(w, "init", "-q")
        git(w, "remote", "add", "origin", o)
        tree = git(o, "hash-object", "-t", "tree", "-w", "/dev/null")
        commit = lambda msg, *parents: git(o, "commit-tree", "-m", msg, *[x for p in parents for x in ("-p", p)], tree)
        ref = lambda name, sha: git(o, "update-ref", f"refs/heads/{name}", sha)  # on the remote only: the check fetches
        m = commit("main")
        a1 = commit("a", m)
        ref("master", m)
        ref("u/tfriedman/pr/a", a1)
        ref("u/tfriedman/pr/b", commit("b", a1))  # stacked on a
        ref("u/tfriedman/pr/c", commit("c", m))
        ref("u/tfriedman/pr/d", commit("d", m))
        ref("u/tfriedman/pr/gone", commit("gone", a1))  # fetched, then deleted on the remote: pruned, not held
        git(w, "fetch", "-q", "origin", "+refs/heads/u/tfriedman/pr/gone:refs/remotes/origin/u/tfriedman/pr/gone")
        git(o, "update-ref", "-d", "refs/heads/u/tfriedman/pr/gone")
        STACK_OWNERS = os.path.join(d, "owners.conf")
        with open(STACK_OWNERS, "w") as f:
            f.write("# comment u/tfriedman/pr/c\nown  u/tfriedman/pr/d*  u/tfriedman/pr/zz\n")
        try:
            got = stacked_groups(w, {"a", "b", "c", "d", "new", "gone"})
            assert sorted(got) == ["b", "d"] and got["b"].endswith("(based on u/tfriedman/pr/a)"), got
            assert all(r.startswith("stacked group: use pr-stack") for r in got.values()) and "stack-owners" in got["d"]
            ref("u/tfriedman/pr/a", commit("a2", a1))  # a moved on after b was made: no order, both wait
            diverged = stacked_groups(w, {"a", "b", "c"})
            assert sorted(diverged) == ["a", "b"] and "needs a restack" in diverged["a"], diverged
            ref("master", commit("m2", m))  # the default branch moved past c: c is not held
            assert stacked_groups(w, {"c"}) == {}, stacked_groups(w, {"c"})
        finally:
            STACK_OWNERS = saved
    body = "Files:\n- iar/{f}.py\n\nDone when: `true`\n"
    cand = lambda bid, *labels: {"id": bid, "status": "open", "priority": 2, "labels": ["auto", *labels],
                                 "description": body.format(f=bid), "comments": []}
    snap = {"candidates": [cand("s", "pr:b"), cand("ok", "pr:c"), cand("no-group")], "issues": {}, "sessions": [],
            "stacked": got}
    config = {"agent": {"max_concurrent": 4, "max_concurrent_by_state": {}}, "limits": {"max_starts_per_tick": 5},
              "max_retry_backoff_ms": 300000}
    out = plan_dispatch(snap, datetime.now(timezone.utc), config, lambda bid: None)
    assert [a["item"] for a in out] == ["ok", "no-group"], out  # no dispatch and no reject-auto for s
    assert snap["held"] == {"s": got["b"]}, snap["held"]
    print("w9b stack: ok")


FAKE_BG = r'''#!/usr/bin/env python3
import os, re, sys
sys.path.insert(0, os.environ["IAR_DIR"])
import ledger
bead, args = sys.argv[1], sys.argv[2:]
assert args[0] == "--done-when" and args[1] and args[2:] == ["--auto"], args
n = 1 + max([int(m[1]) for c in ledger.show(bead).get("comments") or []
             if (m := re.match(r"attempt(?:: |-failed: \S+ attempt=)(\d+)", c["text"]))], default=0)
failed = bead in os.environ.get("FAKE_BG_FAIL", "").split()
with open(os.environ["FAKE_BG_LOG"], "a") as f:
    f.write(f"{bead} {n}{' failed' * failed}\n")
if failed:
    ledger.comment(bead, f"attempt-failed: lease attempt={n} no free slot")
    sys.exit(1)
ledger.update(bead, status="in_progress", assignee=f"agent:{bead}#{n}")
ledger.comment(bead, f"attempt: {n} session: s-{bead}-{n} slot: /nonexistent/{bead} branch: u/x/{bead} brief: /b dispatch: auto")
'''


def w14_tick_test():
    """W14 end to end: real tick, scratch br store, fake bin/bg (claim + attempt comment, or attempt-failed), fake
    sessions that work for every dispatched attempt. Dispatch, reject, failure backoff, source-error preflight, E12
    (fresh state dirs repeat nothing), a bad WORKFLOW.md (dispatch blocked, other actions on the last good config,
    --kill still stops them)."""
    saved_env = {k: os.environ.get(k) for k in ("IAR_WORKFLOW", "BEADS_DIR", "IAR_BG", "IAR_DIR", "FAKE_BG_LOG",
                                                "FAKE_BG_FAIL", "IAR_STATE_DIR")}
    with tempfile.TemporaryDirectory() as d:
        log, wf = os.path.join(d, "bg.log"), os.path.join(d, "WORKFLOW.md")
        with open(os.path.join(IAR, "WORKFLOW.md")) as src, open(wf, "w") as dst:
            dst.write(src.read())
        with open(os.path.join(d, "bg"), "w") as f:
            f.write(FAKE_BG)
        os.chmod(os.path.join(d, "bg"), 0o755)
        os.environ.update(IAR_WORKFLOW=wf, BEADS_DIR=os.path.join(d, ".beads"), IAR_BG=os.path.join(d, "bg"),
                          IAR_DIR=IAR, FAKE_BG_LOG=log, FAKE_BG_FAIL="")
        fail_source, stopped = [False], set()

        def agents(s):
            with open(log) if os.path.exists(log) else io.StringIO() as f:
                started = [line.split() for line in f if not line.strip().endswith(" failed")]
            s["sessions"] = [{"sessionId": f"s-{b}-{n}", "id": b, "kind": "background", "name": f"{b}: x",
                              "state": "stopped" if b in stopped else "working", "alive": b not in stopped}
                             for b, n in started]

        def treehouse(s):
            if fail_source[0]:
                raise SourceError("treehouse status: no answer in 30s")
        fakes = {"agents": agents, "treehouse": treehouse, "inbox": lambda s: None, "plannotator": lambda s: None,
                 "git": lambda s: None}
        executors = {**EXECUTORS, "flag-zombie": (noted, lambda a, snap: note(a, "zombie"))}
        n_dir = [0]

        def run(new=True):  # a tick, by default with a new, empty state dir: E12 (state lost) repeats no action
            n_dir[0] += new
            sd = os.path.join(d, f"state{n_dir[0]}")
            os.makedirs(sd, exist_ok=True)
            line = tick(sd, False, fakes, executors)[1]
            return line, [(x["item"], x["action"], x["outcome"]) for x in line["executed"] if x["outcome"] != "off"]
        texts = lambda bid: [c["text"] for c in ledger.show(bid).get("comments") or []]
        labels = lambda bid: ledger.show(bid).get("labels") or []
        tries = lambda: open(log).read().split("\n")[:-1] if os.path.exists(log) else []
        spec = "Files:\n- iar/{0}.py\n\nDone when: `true {0}`\n"

        def auto(title, p, desc=None):
            return ledger.create(title, type="task", priority=p, labels=["auto"],
                                 description=desc if desc is not None else spec.format(title))["id"]

        def zombie(title):
            bid = ledger.create(title, type="task")["id"]
            ledger.comment(bid, f"attempt: 1 session: s-gone-{bid} slot: /nonexistent/{bid} branch: u/x/{bid} brief: /b")
            ledger.update(bid, status="in_progress", assignee=f"agent:{bid}#1")
            return bid
        try:
            ledger.init("wt")
            set_switches(None, wf)
            set_switches("dispatch", wf)
            set_switches("zombie", wf)
            with open(wf) as f:  # fl fails 4 times below; the attempt limit is not under test here
                text = f.read()
            with open(wf, "w") as f:
                f.write(text.replace('"max_attempts": 3', '"max_attempts": 9'))
            fl, a, r, b = auto("fails", 0), auto("first", 1), auto("rejected", 1, "no files\n"), auto("second", 2)
            ledger.create("not auto", type="task", priority=0, description=spec.format("na"))
            z = zombie("zombie one")
            os.environ["FAKE_BG_FAIL"] = fl
            # Tick 1: 2 starts per tick (fl fails, a starts), r rejected once, b waits; the zombie is flagged too.
            line, done = run()
            assert sorted(done) == sorted([(fl, "dispatch attempt=1", "done"), (a, "dispatch attempt=1", "done"),
                                           (r, "reject-auto count=1", "done"), (z, "flag-zombie attempt=1", "done")]), done
            assert tries() == [f"{fl} 1 failed", f"{a} 1"] and line["config_error"] is None
            s = ledger.show(a)
            assert s["status"] == "in_progress" and s["assignee"] == f"agent:{a}#1", s
            assert [texts(a)[-3], texts(a)[-1]] == ["reconcile-start: dispatch dispatch attempt=1", "reconcile: dispatch attempt=1"]
            assert attempts(s)[1]["auto"] and PENDING not in labels(a)
            assert ledger.show(fl)["status"] == "open" and texts(fl)[-1] == "reconcile: dispatch attempt=1", texts(fl)
            assert AUTO_BLOCKED in labels(r) and QUARANTINE not in labels(r)
            assert [t for t in texts(r) if t.startswith("reconcile-note: ")] == [
                "reconcile-note: reject-auto count=1\nNot dispatched automatically: no `Files:` list. Fix the bead, "
                "then remove the label `auto:blocked`."], texts(r)
            # Three more failures (as if from later ticks): fl now waits 80 s, so the next ticks surely skip it.
            for k in (2, 3, 4):
                ledger.comment(fl, f"attempt-failed: lease attempt={k} no free slot")
            # Preflight: a source fails, nothing is dispatched (b would start now).
            fail_source[0] = True
            line, done = run()
            assert done == [] and "treehouse" in line["errors"] and tries() == [f"{fl} 1 failed", f"{a} 1"]
            fail_source[0] = False
            # Tick 2 (state dir lost): a is working, 1 free slot of 3 (z and a hold 2): b starts; fl waits.
            line, done = run()
            assert done == [(b, "dispatch attempt=1", "done")], done
            assert line["classes"][a] == "working", line["classes"]
            # E12: again with a new state dir: the attempt -> session -> lease mapping comes from bead comments;
            # no action repeats.
            line, done = run()
            assert done == [] and tries() == [f"{fl} 1 failed", f"{a} 1", f"{b} 1"], (done, tries())
            assert [t for t in texts(z) if t.startswith("reconcile-note: ")] == ["reconcile-note: flag-zombie attempt=1\nzombie"]
            # Backoff over (cap lowered to 1 s): fl is tried again as attempt 5, once the zombie's slot is free.
            ledger.close(z, reason="test")
            os.environ["FAKE_BG_FAIL"] = ""
            with open(wf) as f:
                good = f.read()
            with open(wf, "w") as f:
                f.write(good.replace('"max_retry_backoff_ms": 300000', '"max_retry_backoff_ms": 1000'))
            time.sleep(1.1)
            line, done = run()
            assert done == [(fl, "dispatch attempt=5", "done")] and tries()[-1] == f"{fl} 5", done
            # A WORKFLOW.md that does not load: no dispatch, the snapshot says why, other actions run on the last
            # good config (from this state dir).
            with open(wf, "w") as f:
                f.write(good.replace('"tracker"', '"tracker', 1))
            for bid in (a, b, fl):
                ledger.close(bid, reason="test")
                stopped.add(bid)  # their sessions end too: a running one would still hold its slot
            c, z2 = auto("third", 1), zombie("zombie two")
            line, done = run(new=False)
            assert done == [(z2, "flag-zombie attempt=1", "done")], done
            assert line["config_error"].startswith("config error: ") and "bad front matter" in line["config_error"]
            assert not line["errors"] and ledger.show(c)["status"] == "open" and tries()[-1] == f"{fl} 5"
            # --kill with the file still bad: it cannot edit WORKFLOW.md, but the last good config goes all off.
            sd = os.path.join(d, f"state{n_dir[0]}")
            os.environ["IAR_STATE_DIR"] = sd
            with contextlib.redirect_stderr(io.StringIO()):
                assert main(["--kill"]) == 2
            z3 = zombie("zombie three")
            line = tick(sd, False, fakes, executors)[1]
            assert [x["outcome"] for x in line["executed"] if x["item"] == z3] == ["off"], line["executed"]
            # Fixed file: dispatch resumes.
            with open(wf, "w") as f:
                f.write(good)
            line, done = run()
            assert (c, "dispatch attempt=1", "done") in done and line["config_error"] is None, done
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("w14 tick: ok")


def w13_test():
    """W13 end to end: question beads filed with the real iar/ask against a scratch br store, real Inbox files in a
    temp dir, fake claude and Inbox MCP. E2b, E3b, E4 (with a stopped session: precog-v8xo), E5, malformed, external."""
    import contextlib
    import io
    g = globals()
    saved = {k: g[k] for k in ("sh", "transcript_facts", "INBOX")}
    saved_env = {k: os.environ.get(k) for k in ("IAR_WORKFLOW", "BEADS_DIR", "IAR_INBOX_CMD", "IAR_STATE_DIR")}
    agents, calls = [], []
    stamp = lambda t: t.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with tempfile.TemporaryDirectory() as d:
        tdir = os.path.join(d, "transcripts")
        os.makedirs(tdir)

        def fake_sh(cmd, cwd=None, check=True, input=None):
            tool, args = os.path.basename(cmd[0]), cmd[1:]
            if tool == "claude" and args[0] == "agents":
                return json.dumps(agents)
            if tool == "claude" and args[0] == "stop":
                calls.append(("stop", args[1]))
                next(r for r in agents if r["id"] == args[1]).update(state="stopped", pid=None)
                return ""
            if tool == "claude" and args[:2] == ["--bg", "--resume"]:
                calls.append(("resume", args[2], args[3]))
                row = next(r for r in agents if r["sessionId"] == args[2])
                row.update(state="working", pid=None)
                with open(os.path.join(tdir, f"{args[2]}.jsonl"), "a") as f:  # the new turn
                    f.write(json.dumps({"type": "user", "timestamp": stamp(datetime.now(timezone.utc)),
                                        "message": {"content": args[3]}}) + "\n")
                return f"backgrounded · {row['id']}\n"
            raise AssertionError(f"unexpected command {cmd}")

        def transcript(sid):
            path = os.path.join(tdir, f"{sid}.jsonl")
            return {"transcript": path} if os.path.exists(path) else {}

        # The Inbox: a fake MCP server (log of calls) and the files Tal's answers land in.
        log, fake = os.path.join(d, "inbox.log"), os.path.join(d, "fake-inbox")
        with open(fake, "w") as f:
            f.write(f"#!/bin/sh\ncat >> '{log}'\necho '"
                    '{"jsonrpc":"2.0","id":2,"result":{"structuredContent":{"thread_id":"msg_T1"}}}'
                    "'\n")
        os.chmod(fake, 0o755)
        inbox_dir = os.path.join(d, "inbox", "projects", "p")
        os.makedirs(inbox_dir)
        rows = {"messages": [], "questions": []}

        def tal_answers(qid, picked, choice=None, reply=None):
            """Tal answers qid in the Inbox: a picked option, or a reply."""
            mid, rid = f"msg_{qid}", f"msg_{qid}_r"
            rows["messages"] += [{"id": mid, "thread_id": mid, "created_at": picked, "author": {"kind": "agent"},
                                  "subject": f"{qid}: question"},
                                 {"id": rid, "thread_id": mid, "reply_to": mid, "created_at": picked,
                                  "author": {"kind": "person"}, "body": reply or f"Answer: {choice}"}]
            rows["questions"].append({"id": f"{mid}/q-1", "message_id": mid, "position": 0, "prompt": "?",
                                      "answer": {"selected": [choice] if choice else [], "by_reply": not choice,
                                                 "skipped": not choice},
                                      "sent_reply_id": rid, "picked_at": picked})
            for kind in rows:
                with open(os.path.join(inbox_dir, f"{kind}.jsonl"), "w") as f:
                    f.writelines(json.dumps({"seq": i, "id": r["id"], "record": r}) + "\n"
                                 for i, r in enumerate(rows[kind]))

        def sent():
            """send_message calls the fake Inbox got: (subject or reply_to, body)."""
            try:
                with open(log) as f:
                    msgs = [json.loads(x) for x in f.read().splitlines()]
            except OSError:
                return []
            return [(m["params"]["arguments"].get("subject") or m["params"]["arguments"].get("reply_to"),
                     m["params"]["arguments"]["body"]) for m in msgs if m.get("method") == "tools/call"]

        wf = os.path.join(d, "WORKFLOW.md")
        with open(os.path.join(IAR, "WORKFLOW.md")) as src, open(wf, "w") as dst:
            dst.write(src.read())
        os.environ.update(IAR_WORKFLOW=wf, BEADS_DIR=os.path.join(d, ".beads"), IAR_STATE_DIR=d, IAR_INBOX_CMD=fake)
        g.update(sh=fake_sh, transcript_facts=transcript, INBOX=os.path.join(d, "inbox"))
        try:
            subprocess.run(["br", "init", "--prefix", "qr"], cwd=d, check=True, capture_output=True, timeout=30)
            set_switches(None, wf)
            for cls in ("answer-pending", "question-deadline", "malformed-question", "inbox-pending"):
                set_switches(cls, wf)

            def asking(title, n=1, assignee=None, last=None):
                """A loop-owned bead with n attempts; the last attempt's session ended its turn (blocked)."""
                bid = ledger.create(title)["id"]
                for k in range(1, n + 1):
                    ledger.comment(bid, f"attempt: {k} session: s-{bid}-{k} slot: {d} branch: b-{bid} brief: /b")
                    agents.append({"sessionId": f"s-{bid}-{k}", "id": f"s-{bid}-{k}", "kind": "background",
                                   "name": f"{bid}: x", "state": "blocked" if k == n else "stopped", "pid": None,
                                   **({"last_message": last} if last else {})})
                ledger.update(bid, status="in_progress", assignee=assignee or f"agent:{bid}#{n}")
                return bid

            def ask(bid, text, deadline=None, reversible="yes", more=()):
                argv = [bid, text, "-o", "Keep", "-o", "Drop", "--default", "Keep", "--reversible", reversible,
                        "--gated", "no", "--blocks", "no", "--session", f"s-{bid}-1", *more]
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = question.main_ask(argv + (["--deadline", deadline] if deadline else []))
                return rc, out.getvalue().strip()

            fakes = {"agents": lambda s: s.update(sessions=[dict(r, alive=bool(r["pid"])) for r in agents
                                                            if r["state"] != "stopped"],
                                                  session_names={r["sessionId"]: r["name"] for r in agents}),
                     "treehouse": lambda s: None, "plannotator": lambda s: None, "git": lambda s: None}
            run = lambda: sorted((x["item"], x["action"].split()[0], x["outcome"])
                                 for x in tick(d, False, fakes)[1]["executed"] if x["outcome"] != "off")
            past = question.iso(question.now() - timedelta(minutes=1))
            texts = lambda bid: [c["text"] for c in ledger.show(bid).get("comments") or []]

            # E5, part 1: the Inbox is down when the question is asked and at the next tick.
            ip = asking("inbox down")
            os.environ["IAR_INBOX_CMD"] = "false"
            # Not reversible and past its deadline: its reping waits for the post.
            rc, qip = ask(ip, "Post me later?", deadline=past, reversible="no",
                          more=["--project", "/inbox/other", "--thread", "my thread"])
            assert rc == 3 and "inbox:pending" in ledger.show(qip)["labels"], rc
            # Reversible and past its deadline: no timeout before Tal could see it; the post comes first.
            ir = asking("inbox down, reversible")
            qir = ask(ir, "Time me out only once posted?", deadline=past)[1]
            down = "error: RuntimeError: Inbox gave no answer (exit 1): "
            assert run() == sorted([(ip, "post-question", down), (ir, "post-question", down)])
            assert not question.answers(ledger.show(qir))
            os.environ["IAR_INBOX_CMD"] = fake

            ap = asking("E4: answer from the Inbox, two attempts, the asking session stopped", n=2)
            agents[-1].update(state="stopped")  # precog-v8xo: Tal answers while the session is stopped
            qap = ask(ap, "Keep the cache?")[1]
            tal_answers(qap, stamp(datetime.now(timezone.utc)), choice="Keep")
            hw = asking("E2b: a human answer before the deadline wins")
            qhw = ask(hw, "Rename it?", deadline=past)[1]
            tal_answers(qhw, past, reply="Drop it, and say why in the bead")
            to = asking("E2b: timeout, then an earlier human answer")
            qto = ask(to, "Port it?", deadline=past)[1]
            td = asking("E2b: timeout applies the default")
            qtd = ask(td, "Split it?", deadline=past)[1]
            rv = asking("not reversible: one reping")
            qrv = ask(rv, "Delete the table?", deadline=past, reversible="no")[1]
            gt = asking("E3b: gated never times out")
            f = {"question": "Launch?", "asking": gt, "session": f"s-{gt}-1", "blocks": "no", "default": "Keep",
                 "reversible": "yes", "gated": "yes", "deadline": past, "inbox_thread": "msg_T1"}  # a deadline anyway
            qgt = ledger.create("Q(x): Launch?", type="task", parent=gt, labels=["question", "no-swarm"],
                                description=question.render_body(f, ["Keep", "Drop"], ""))["id"]
            mq = asking("malformed", last="QUESTION: should I ship?")
            ex = asking("external: a workflow claim", assignee="factory-wf")
            qex = ask(ex, "Mine?")[1]
            tal_answers(qex, past, choice="Drop")
            posted = len(sent())

            t0 = datetime.now(timezone.utc)
            assert run() == sorted([
                (ip, "post-question", "done"), (ap, "import-answer", "done"), (hw, "import-answer", "done"),
                (to, "timeout", "done"), (td, "timeout", "done"), (rv, "reping", "done"),
                (mq, "flag-malformed", "done"), (ir, "post-question", "done")])
            # E5: posted once, later; the thread id is written back.
            assert [s for s in sent()[posted:] if s[0].startswith(qip)] and "inbox:pending" not in ledger.show(qip)["labels"]
            assert question.parse_body(ledger.show(qip)["description"])["inbox_thread"] == "msg_T1"
            with open(log) as f:  # the retry posts where ask was told to
                call = [json.loads(x) for x in f.read().splitlines() if qip + ":" in x][0]["params"]["arguments"]
            assert (call["project_path"], call["thread"]) == ("/inbox/other", "my thread"), call
            assert [s for s in sent()[posted:] if s[0] == "msg_T1" and qrv in s[1]]  # the reping, in the thread
            assert any(x.startswith("reconcile-note: flag-malformed attempt=1\n") and "should I ship?" in x
                       for x in texts(mq))
            assert "decision:auto" in ledger.show(td)["labels"]
            # E2b: Tal's Inbox answer, picked before the timeout was written, wins over it.
            tal_answers(qto, stamp(t0 - timedelta(seconds=1)), choice="Drop")

            assert run() == sorted([
                (ap, "deliver-answer", "done"), (hw, "deliver-answer", "done"), (to, "import-answer", "done"),
                (to, "deliver-answer", "done"), (td, "deliver-answer", "done"), (ip, "reping", "done"),
                (ir, "timeout", "done")])
            with open(log) as f:  # the reping goes to the project the question was posted to
                call = [json.loads(x) for x in f.read().splitlines() if f"reping-{qip}" in x][0]["params"]["arguments"]
            assert call["reply_to"] == "msg_T1" and call["project_path"] == "/inbox/other", call
            resumes = {c[1]: c[2] for c in calls if c[0] == "resume"}
            assert sorted(resumes) == sorted([f"s-{ap}-2", f"s-{hw}-1", f"s-{to}-1", f"s-{td}-1"]), resumes  # E4: attempt 2
            assert resumes[f"s-{ap}-2"].startswith(f"ANSWER {qap}: Keep\n"), resumes
            assert resumes[f"s-{hw}-1"].startswith(f"ANSWER {qhw}: Drop it, and say why in the bead\n")
            assert resumes[f"s-{to}-1"].startswith(f"ANSWER {qto}: Drop\n"), resumes  # the human answer, not Keep
            assert resumes[f"s-{td}-1"].startswith(f"ANSWER {qtd}: Keep\n") and "declared default" in resumes[f"s-{td}-1"]
            assert not [c for c in calls if c[0] == "stop"]  # every asking session had ended its turn
            for q in (qap, qhw, qto, qtd):
                s = ledger.show(q)
                assert s["status"] == "closed" and "delivered" in s["labels"], s
            assert sum("(timeout)" in x for x in texts(qto)) == 1 and not any("(timeout)" in x for x in texts(qhw))
            assert question.effective_answer(ledger.show(qto))["text"] == "Drop"

            assert run() == [(ir, "deliver-answer", "done")]
            # Nothing left: each action ran once. E3b: the gated question never timed out; the external bead got no
            # import or delivery.
            n_sent = len(sent())
            assert run() == [] and run() == [] and len(sent()) == n_sent
            assert len([c for c in calls if c[0] == "resume"]) == 5
            assert ledger.show(qgt)["status"] == "open" and not question.answers(ledger.show(qgt))
            try:
                question.answer(qgt, "", timeout=True)
                raise AssertionError("a gated question timed out")
            except ValueError:
                pass
            assert not question.answers(ledger.show(qex)) and texts(ex) == [texts(ex)[0]]

            # A crash between the `delivered` label and the close: the next tick closes it and does not send again
            # (the transcript is gone here, so only the label tells).
            cr = asking("crash after the delivered label")
            qcr = ask(cr, "Crash?")[1]
            question.answer(qcr, "Drop")
            real_close = ledger.close

            def close_fails(*args, **kw):
                ledger.close = real_close
                raise ledger.LedgerError("close failed")
            ledger.close = close_fails
            try:
                assert run() == [(cr, "deliver-answer", "error: LedgerError: close failed")]
            finally:
                ledger.close = real_close
            assert "delivered" in ledger.show(qcr)["labels"] and ledger.show(qcr)["status"] == "open"
            os.remove(os.path.join(tdir, f"s-{cr}-1.jsonl"))
            assert run() == [(cr, "deliver-answer", "done")] and ledger.show(qcr)["status"] == "closed"
            assert [c[1] for c in calls if c[0] == "resume"].count(f"s-{cr}-1") == 1 and run() == []

            # E2b race: Tal's answer, picked before the timeout write, lands after the delivery tick's snapshot (the
            # git source runs after the Inbox source). The delivery imports it and sends it, not the default.
            rt = asking("E2b: answer lands after the snapshot")
            qrt = ask(rt, "Late race?", deadline=past)[1]
            assert run() == [(rt, "timeout", "done")]
            wrote = question.answers(ledger.show(qrt))[0]["at"]
            late = [lambda: tal_answers(qrt, stamp(wrote - timedelta(milliseconds=1)), choice="Drop")]
            fakes["git"] = lambda s: late and late.pop()()
            assert run() == [(rt, "deliver-answer", "done")]
            assert [c[2] for c in calls if c[0] == "resume" and c[1] == f"s-{rt}-1"][0].startswith(f"ANSWER {qrt}: Drop\n")
            assert question.effective_answer(ledger.show(qrt))["source"] == "inbox" and run() == []

            # E4 via Linear (W25b): Tal's `answer:` comment on the question's Linear issue (which comments count:
            # linear_mirror.py --self-test) is imported through iar/answer and delivered once to the asking session.
            lp = asking("E4 via Linear")
            qlp = ask(lp, "Answer me in Linear?")[1]
            set_switches("linear-mirror", wf)
            linear = {qlp: {"text": "Drop", "at": stamp(datetime.now(timezone.utc)), "source": "linear"}}
            lin = lambda qs: ({q["id"]: linear[q["id"]] for q in qs if q["id"] in linear}, None)
            lrun = lambda: sorted((x["item"], x["action"].split()[0], x["outcome"]) for x in tick(
                d, False, fakes, mirror=lambda *a: {"writes": [], "error": None}, linear_answers=lin)[1]["executed"]
                if x["outcome"] != "off")
            assert lrun() == [(lp, "import-answer", "done")]
            assert question.effective_answer(ledger.show(qlp))["source"] == "linear"
            assert lrun() == [(lp, "deliver-answer", "done")]
            got = [c[2] for c in calls if c[0] == "resume" and c[1] == f"s-{lp}-1"]
            assert len(got) == 1 and got[0].startswith(f"ANSWER {qlp}: Drop\n") and "source: linear" in got[0], got
            assert lrun() == [] and lrun() == [] and ledger.show(qlp)["status"] == "closed"
            assert len([c for c in calls if c[0] == "resume" and c[1] == f"s-{lp}-1"]) == 1
            # E2b via Linear: Tal's Linear answer, given before the timeout write but read after it, wins over it.
            lt = asking("E2b via Linear")
            qlt = ask(lt, "Time me out, then answer in Linear?", deadline=past)[1]
            assert lrun() == [(lt, "timeout", "done")]
            wrote = question.answers(ledger.show(qlt))[0]["at"]
            linear[qlt] = {"text": "Drop", "at": stamp(wrote - timedelta(milliseconds=1)), "source": "linear"}
            assert lrun() == [(lt, "deliver-answer", "done"), (lt, "import-answer", "done")]
            got = [c[2] for c in calls if c[0] == "resume" and c[1] == f"s-{lt}-1"]
            assert len(got) == 1 and got[0].startswith(f"ANSWER {qlt}: Drop\n"), got
            assert question.effective_answer(ledger.show(qlt))["source"] == "linear" and lrun() == []

            # inbox_answers: drafts (no sent reply) and skips with no reply are not answers; a note rides along.
            q = lambda **kw: {"message_id": "m", "position": 0, "picked_at": "t", "sent_reply_id": "r", **kw}
            msgs = {"m": {"subject": "precog-a1: x?"}, "r": {"body": "free text"}}
            assert inbox_answers(msgs, {"1": q(answer={"selected": ["A"]}, sent_reply_id=None)}) == {}
            assert inbox_answers(msgs, {"1": q(answer={"selected": [], "skipped": True})}) == {}
            assert inbox_answers(msgs, {"1": q(answer={"selected": ["A"], "note": "why "})}) == \
                {"precog-a1": {"text": "A (note: why)", "at": "t"}}
            assert inbox_answers(msgs, {"1": q(answer={"selected": [], "by_reply": True})})["precog-a1"]["text"] == \
                "free text"
        finally:
            g.update(saved)
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def w12_classify_test():
    """W12 classes and their precedence, pure."""
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    t = lambda minutes: (now - timedelta(minutes=minutes)).isoformat()

    def bead(bid, status="in_progress", n=1, labels=(), extra=(), at=10, auto=False):
        comments = [{"text": f"attempt: {k} session: s-{bid} slot: /s/{bid} branch: b-{bid} brief: /b"
                             + (" dispatch: auto" if auto else ""), "created_at": t(at)} for k in range(1, n + 1)]
        comments += [{"text": x, "created_at": t(m)} for x, m in extra]
        return {"id": bid, "status": status, "assignee": f"agent:{bid}#{n}", "labels": list(labels),
                "comments": comments}

    def sess(bid, **kw):
        return {"sessionId": f"s-{bid}", "kind": "background", "name": f"{bid}: x", **kw}
    work = {"state": "working", "pid": 1, "alive": True, "transcript_at": t(1)}
    over = dict(work, tokens=4_000_000)
    nudge1 = ("reconcile: limit-nudge attempt=1 limit=tokens", 5)
    beads = [
        bead("p-tok"), bead("p-hrs", at=5 * 60), bead("p-att", n=4), bead("p-grace", extra=[nudge1]),
        bead("p-quar", extra=[(nudge1[0], 20)]), bead("p-again", n=2, extra=[nudge1]),
        bead("p-qdone", labels=[QUARANTINE], extra=[(nudge1[0], 60)]), bead("p-olans"), bead("p-olidle"),
        bead("p-olclosed", status="closed"),
        bead("i-closed", status="closed"), bead("i-closedwork", status="closed"), bead("i-def", status="deferred"),
        dict(bead("i-intwork", status="closed"), close_reason="integrated into u/tfriedman/pr/x at abc"),
        bead("i-auto", auto=True), bead("i-autook", auto=True, labels=["auto"]), bead("i-manual"),
        bead("i-stall", status="deferred"), bead("i-gone", status="closed"),
        bead("l-dirty", status="closed"), bead("l-unp", status="closed"), bead("l-live", status="closed"),
        bead("l-run", status="open"), bead("l-np", status="open"), bead("l-ok", status="closed"),
        bead("l-flagged", status="closed", extra=[("reconcile: flag-lease slot=l7 holder=agent:l-flagged#1", 9)]),
        bead("s-flag", extra=[("reconcile: nudge attempt=1 count=1", 40)]),
    ]
    for b in beads:
        if b["id"].startswith("l-") and b["status"] == "open":
            b["assignee"] = ""
    stalled = dict(work, transcript_at=t(45), cpu_active=False)
    snap = {
        "errors": {}, "inbox": [], "git": {"l-run#1": {"pushed": True}},
        "issues": {b["id"]: b for b in beads},
        "questions": [{"id": "precog-qa", "asking": "p-olans", "labels": ["question", "answered"]}],
        "sessions": [sess("p-tok", **over), sess("p-hrs", **work), sess("p-att", **work), sess("p-grace", **over),
                     sess("p-quar", **over), sess("p-again", **over), sess("p-qdone", **over), sess("p-olans", **over),
                     sess("p-olidle", state="blocked", tokens=4_000_000), sess("p-olclosed", **over),
                     sess("i-closed", state="done", pid=1, alive=True), sess("i-closedwork", **work),
                     sess("i-intwork", **work),
                     sess("i-def", **work), sess("i-auto", **work), sess("i-autook", **work), sess("i-manual", **work),
                     sess("i-stall", **stalled), sess("i-gone", state="done"), sess("l-run", state="done", pid=1, alive=True),
                     sess("s-flag", **stalled)],
        "slots": [{"slot": f"l{k}", "path": f"/p/{k}", "holder": f"agent:{bid}#1", "live": bid == "l-live"}
                  for k, bid in enumerate(["l-dirty", "l-unp", "l-live", "l-run", "l-np", "l-ok", "l-flagged"], 1)],
        "slot_git": {"l1": {"dirty": True, "unpushed": False}, "l2": {"dirty": False, "unpushed": True},
                     **{f"l{k}": {"dirty": False, "unpushed": False} for k in range(3, 7)},
                     "l7": {"dirty": True, "unpushed": False}},
    }
    got = classify(snap, now)
    want = {
        "p-tok": "over-limit", "p-hrs": "over-limit", "p-att": "over-limit", "p-grace": "over-limit",
        "p-quar": "over-limit", "p-again": "over-limit", "p-qdone": "over-limit", "p-olans": "over-limit",
        "p-olidle": "waiting", "p-olclosed": "ineligible-running",
        "i-closed": "ineligible-running", "i-closedwork": "ineligible-running", "i-intwork": "working",
        "i-def": "ineligible-running",
        "i-auto": "ineligible-running", "i-autook": "working", "i-manual": "working", "i-stall": "ineligible-running",
        "i-gone": "idle",
        "l-dirty": "stale-lease", "l-unp": "stale-lease", "l-live": "stale-lease", "l-run": "stale-lease",
        "l-np": "stale-lease", "l-ok": "stale-lease", "l-flagged": "stale-lease", "s-flag": "stalled",
    }
    assert got["classes"] == want, {k: (v, want.get(k)) for k, v in got["classes"].items() if want.get(k) != v}
    acts = sorted((a["item"], a["action"]) for a in got["actions"])
    assert acts == sorted([
        ("p-tok", "limit-nudge attempt=1 limit=tokens"), ("p-hrs", "limit-nudge attempt=1 limit=hours"),
        ("p-att", "limit-nudge attempt=4 limit=attempts"), ("p-quar", "quarantine attempt=1"),
        ("p-again", "quarantine attempt=2"), ("p-olans", "limit-nudge attempt=1 limit=tokens"),
        ("p-olclosed", "stop-session attempt=1"), ("i-closed", "stop-session attempt=1"),
        ("i-closedwork", "stop-session attempt=1"),
        ("i-def", "stop-session attempt=1"), ("i-auto", "stop-session attempt=1"),
        ("i-stall", "stop-session attempt=1"),
        ("l-dirty", "flag-lease slot=l1 holder=agent:l-dirty#1"), ("l-unp", "flag-lease slot=l2 holder=agent:l-unp#1"),
        ("l-np", "flag-lease slot=l5 holder=agent:l-np#1"), ("l-ok", "return-lease slot=l6 holder=agent:l-ok#1"),
        ("s-flag", "flag-stalled attempt=1"),
    ]), acts
    assert classify(snap, now, limits={"max_attempt_tokens": 5_000_000})["classes"]["p-tok"] == "working"
    assert preflight("l-dirty", snap["issues"]["l-dirty"], 1, snap["slots"][0], snap, {}) == ["worktree not clean"]

    # Transcript: the last assistant text (all blocks of one message), last 40 lines; tokens count a message once.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.jsonl")
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 9999}
        rows = [{"type": "assistant", "message": {"id": "A", "usage": usage, "content": [{"type": "text", "text": "old"}]}},
                {"type": "user", "message": {"content": "hi"}},
                {"type": "assistant", "message": {"id": "B", "usage": usage, "content": [
                    {"type": "text", "text": "\n".join(f"l{i}" for i in range(50))}]}},
                {"type": "assistant", "message": {"id": "B", "usage": usage, "content": [{"type": "tool_use"}]}},
                {"type": "assistant", "message": {"id": "B", "usage": usage, "content": [{"type": "text", "text": "end"}]}}]
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        out = read_transcript(p)["last_output"].splitlines()
        assert len(out) == OUTPUT_LINES and out[-1] == "end" and out[-2] == "l49", out
        assert transcript_tokens(p) == 2 * 115, transcript_tokens(p)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "timestamp": t(1), "message": {"content": "status?"}}) + "\n")
        assert transcript_has(p, "status?", now - timedelta(minutes=2))
        assert not transcript_has(p, "status?", now) and not transcript_has(p, "other", now - timedelta(minutes=2))


def w12_executor_test():
    """W12 actions end to end against a scratch br store, real git repos for the release preflight (E10), and fake
    claude, treehouse and Inbox. Each action's happy path, then a second and third tick that do nothing (idempotency)."""
    agents, slots, threads, calls = [], [], [], []
    race = {"slot": None, "resume": None}
    real_sh, real_inbox = sh, inbox

    def fake_sh(cmd, cwd=None, check=True, input=None):
        tool, args = os.path.basename(cmd[0]), cmd[1:]
        if tool == "claude" and args[0] == "agents":
            return json.dumps([{k: v for k, v in r.items() if k not in ("transcript_at", "cpu_active", "last_output",
                                                                         "tokens")} for r in agents])
        if tool == "claude" and args[0] == "stop":
            calls.append(("stop", args[1]))
            for r in agents:
                if r["id"] == args[1]:
                    r.update(state="stopped", pid=None)
            return ""
        if tool == "claude" and args[:2] == ["--bg", "--resume"]:
            calls.append(("resume", args[2], args[3]))
            row = next(r for r in agents if r["sessionId"] == args[2])
            row.update(state="working", pid=1)
            return f"backgrounded · {row['id']}\n"
        if tool == "rex":  # a ready Claude pane; the text joins the session's turn, nothing stops
            calls.append(("rex", *args[1:]))
            return READY_PANE if args[1] == "capture" else ""
        if tool == "treehouse" and args[0] == "return":
            calls.append(("return", args[1], args[4]))
            slots[:] = [x for x in slots if x["path"] != args[1]]
            return ""
        return real_sh(cmd, cwd, check, input)

    def fake_inbox(tool, args):
        calls.append((tool, args.get("reply_to") or args.get("message_id"), args.get("body")))
        if tool == "resolve_message":
            threads[:] = [x for x in threads if x["id"] != args["message_id"]]
        return {}

    def src_fake_agents(s):
        s["sessions"] = [dict(r, alive=bool(r.get("pid"))) for r in agents if r["state"] != "stopped"]
        s["session_names"] = {r["sessionId"]: r["name"] for r in agents}

    def src_race_git(s):
        src_git(s)
        if race["slot"]:  # the slot gets dirty after the snapshot, before the action: act-time preflight refuses
            open(os.path.join(race["slot"], "late.txt"), "w").close()
            race["slot"] = None
        if race["resume"]:  # the zombie's session comes back after the snapshot: the release must not happen
            next(r for r in agents if r["sessionId"] == race["resume"]).update(state="working", pid=1)
            race["resume"] = None
    fakes = {"agents": src_fake_agents, "treehouse": lambda s: s.update(slots=[dict(x) for x in slots]),
             "inbox": lambda s: s.update(inbox=[dict(x) for x in threads]), "plannotator": lambda s: None,
             "git": src_race_git}
    saved_env = {k: os.environ.get(k) for k in ("IAR_WORKFLOW", "BEADS_DIR", "PATH")}
    g = globals()
    with tempfile.TemporaryDirectory() as d:
        os.environ["PATH"] = fake_rex(d)
        git = lambda *a, cwd=d: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, timeout=30)
        git("init", "--bare", "-q", "origin.git")
        git("clone", "-q", "origin.git", "seed")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "seed", cwd=f"{d}/seed")
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=f"{d}/seed")

        def clone(name, branch, push=True, dirty=False, unpushed=False):
            path = f"{d}/{name}"
            git("clone", "-q", "origin.git", name)
            git("checkout", "-q", "-b", branch, cwd=path)
            git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", name, cwd=path)
            if push:
                git("push", "-q", "origin", branch, cwd=path)
            if unpushed:
                git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "more", cwd=path)
            if dirty:
                open(f"{path}/scratch.txt", "w").close()
            return path

        wf = os.path.join(d, "WORKFLOW.md")
        with open(os.path.join(IAR, "WORKFLOW.md")) as src, open(wf, "w") as dst:
            dst.write(src.read())
        os.environ.update(IAR_WORKFLOW=wf, BEADS_DIR=os.path.join(d, ".beads"))
        g["sh"], g["inbox"] = fake_sh, fake_inbox
        try:
            subprocess.run(["br", "init", "--prefix", "rt"], cwd=d, check=True, capture_output=True, timeout=30)
            set_switches(None, wf)
            for cls in ("zombie", "stale-lease", "closed-bead-thread", "stalled", "over-limit", "ineligible-running"):
                set_switches(cls, wf)

            def bead(title, status="in_progress", slot=None, branch="b-none", lease=None, session=None, extra=()):
                bid = ledger.create(title)["id"]
                ledger.comment(bid, f"attempt: 1 session: s-{bid} slot: {slot or '/nonexistent'} branch: {branch} brief: /b")
                for x in extra:
                    ledger.comment(bid, x)
                ledger.update(bid, status="in_progress", assignee=f"agent:{bid}#1")
                if status == "closed":
                    ledger.close(bid, reason="integrated into pr/x at abc")
                elif status != "in_progress":
                    ledger.update(bid, status=status)
                if lease:
                    slots.append({"slot": lease, "path": slot, "holder": f"agent:{bid}#1", "live": False})
                if session:
                    agents.append({"sessionId": f"s-{bid}", "id": f"s-{bid}", "kind": "background", "name": f"{bid}: x",
                                   "last_output": f"final words of {bid}", **session})
                return bid
            now = datetime.now(timezone.utc)
            old = (now - timedelta(minutes=45)).isoformat()
            zp = bead("zombie pushed", slot=clone("zp", "b-zp"), branch="b-zp", session={"state": "failed"})
            zf = bead("zombie unpushed", session={"state": "failed"})
            zr = bead("zombie resumes", slot=clone("zr", "b-zr"), branch="b-zr", session={"state": "failed"})
            race["resume"] = f"s-{zr}"
            ok = bead("closed clean", status="closed", slot=clone("ok", "b-ok"), branch="b-ok", lease="1")
            threads.append({"id": "m-ok", "subject": f"{ok}: integrated", "session": "zz"})
            dirty = bead("closed dirty", status="closed", slot=clone("dirty", "b-x", dirty=True), lease="2")
            unp = bead("closed unpushed", status="closed", slot=clone("unp", "b-y", unpushed=True), lease="3")
            rc = bead("closed race", status="closed", slot=clone("race", "b-z"), lease="4")
            gone = bead("closed, remote branch deleted", status="closed", slot=clone("gone", "b-gone"), lease="6")
            git("push", "-q", "origin", "--delete", "b-gone", cwd=f"{d}/seed")  # gone's origin/b-gone ref is stale
            race["slot"] = f"{d}/race"
            st = bead("stalled", session={"state": "working", "pid": 1, "transcript_at": old, "cpu_active": False})
            ol = bead("over limit again", session={"state": "working", "pid": 1, "transcript_at": now.isoformat()},
                      extra=["reconcile: limit-nudge attempt=0 limit=tokens"])
            agents[-1]["tokens"] = 4_000_000
            tk = bead("over limit", session={"state": "working", "pid": 1, "transcript_at": now.isoformat(),
                                             "tokens": 4_000_000})
            df = bead("deferred", status="deferred", session={"state": "working", "pid": 1,
                                                                "transcript_at": now.isoformat()})
            run = lambda: [(x["item"], x["action"].split()[0], x["outcome"])
                           for x in tick(d, False, fakes)[1]["executed"] if x["outcome"] != "off"]
            first = run()
            assert sorted(first) == sorted([
                (zp, "release", "done"), (zf, "flag-zombie", "done"), (ok, "return-lease", "done"),
                (zr, "release", "error: RuntimeError: no longer a zombie with a pushed branch on fresh sources"),
                (dirty, "flag-lease", "done"), (unp, "flag-lease", "done"), (gone, "flag-lease", "done"),
                (rc, "return-lease", "error: RuntimeError: release preflight failed: worktree not clean"),
                (st, "nudge", "done"), (ol, "quarantine", "done"), (tk, "limit-nudge", "done"),
                (df, "stop-session", "done")]), first
            texts = lambda bid: [c["text"] for c in ledger.show(bid).get("comments") or []]
            z = ledger.show(zp)
            assert z["status"] == "open" and not z.get("assignee"), z
            assert any(f"claude --resume s-{zf}" in x for x in texts(zf)), texts(zf)
            assert [x["slot"] for x in slots] == ["2", "3", "4", "6"]  # E10: dirty, unpushed, late-dirty, stale-ref slots stay
            assert any("local commits missing from origin" in x for x in texts(unp))
            sent = [c for c in calls if c[:2] == ("rex", "send")]
            assert ("rex", "send", "-s", sent[0][3], "status?") in calls and ("rex", "kill", sent[0][3]) in calls, calls
            assert not [c for c in calls if c[0] == "resume"] and ("stop", f"s-{st}") not in calls  # a nudge never stops
            assert ledger.show(ol)["labels"] and {QUARANTINE, "needs-human"} <= set(ledger.show(ol)["labels"])
            assert any(x.startswith("last-output: quarantine attempt=1") and f"final words of {ol}" in x for x in texts(ol))
            assert any(c[:2] == ("rex", "send") and "tokens limit" in c[4] for c in calls), calls
            assert ("stop", f"s-{df}") in calls and any(x.startswith("last-output: stop-session") for x in texts(df))
            p = subprocess.run([os.path.join(os.path.dirname(IAR), "bin", "bg"), ol, "--done-when", "true"],
                               capture_output=True, text=True, timeout=60)
            assert p.returncode == 1 and "quarantined" in p.stderr, (p.returncode, p.stderr)

            # Second tick: the race slot is now flagged (its refused return dropped), the stalled session (still
            # quiet) is flagged, not stopped
            # again; the returned lease lets the closed bead's thread resolve (lease first, then thread: E9).
            stops = sum(c[0] == "stop" for c in calls)
            second = run()
            assert ledger.show(zr)["status"] == "in_progress" and ledger.show(zr)["assignee"] == f"agent:{zr}#1"
            assert sorted(second) == sorted([(rc, "flag-lease", "done"), (rc, "return-lease", "dropped"),
                                             (zr, "release", "dropped"),
                                             (st, "flag-stalled", "done"), (ok, "resolve-thread", "done")]), second
            assert ("send_message", "m-ok", f"Landed: {ok} is closed (integrated into pr/x at abc).") in calls, calls
            assert ("resolve_message", "m-ok", None) in calls and not threads
            assert sum(c[0] == "stop" for c in calls) == stops  # no automatic kill
            assert any(x.startswith("last-output: flag-stalled") for x in texts(st))
            # Idempotency: nothing left to do, and no comment written.
            count = {b: len(texts(b)) for b in (zp, zf, ok, dirty, unp, rc, gone, st, ol, tk, df)}
            assert run() == [] and run() == []
            assert {b: len(texts(b)) for b in count} == count
            assert [x["slot"] for x in slots] == ["2", "3", "4", "6"] and sum(c[0] == "stop" for c in calls) == stops
        finally:
            g["sh"], g["inbox"] = real_sh, real_inbox
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


READY_PANE = "⏺ done\n\n──── x ─\n❯ \n────\n  x  Haiku\n  ⏵⏵ auto mode on\n"


def fake_rex(d):
    """A fake rex on PATH (fake_sh answers its calls); returns the new PATH."""
    os.makedirs(f"{d}/fakebin", exist_ok=True)
    with open(f"{d}/fakebin/rex", "w") as f:
        f.write("#!/bin/sh\nexit 1\n")
    os.chmod(f"{d}/fakebin/rex", 0o755)
    return f"{d}/fakebin:{os.environ.get('PATH', '/usr/bin:/bin')}"


def w12b_send_test():
    """precog-6j1m: send() through a fake rex and claude. Idle and busy sessions get the text and Enter; a waiting
    session, a dialog, typed text and no input box get no Enter and raise; no rex flags nudge-no-rex once; a stopped
    session is resumed; the rex session is killed on every path that opened one; no path stops a session."""
    g = globals()
    saved = {k: g[k] for k in ("sh", "REX_APP", "STOP_WAIT_S")}
    saved_env = {k: os.environ.get(k) for k in ("PATH",)}
    saved_ledger = ledger.show, ledger.comment
    calls, comments, rows, pane = [], [], [], [READY_PANE]

    def fake_sh(cmd, cwd=None, check=True, input=None):
        tool, args = os.path.basename(cmd[0]), cmd[1:]
        if tool == "claude" and args[0] == "agents":
            return json.dumps(rows)
        if tool == "claude" and args[:2] == ["--bg", "--resume"]:
            calls.append(("resume", args[2]))
            return "backgrounded · s1\n"
        if tool == "rex":
            calls.append(tuple(args[1:]))
            if args[1] == "new" and pane[0] is None:
                raise SourceError("rex new exit 1: server not running")
            return pane[0] if args[1] == "capture" else ""
        raise AssertionError(f"unexpected command {cmd}")

    def try_send(state, screen, alive=True):
        calls.clear()
        rows[:] = [{"sessionId": "s1", "id": "s1", "kind": "background", "state": state, "pid": 1 if alive else None}]
        pane[0] = screen
        try:
            send(dict(rows[0], alive=alive), "/slot", "status?", {"item": "b1", "action": "nudge attempt=1 count=1"})
            return None
        except RuntimeError as e:
            return e
    with tempfile.TemporaryDirectory() as d:
        g.update(sh=fake_sh, REX_APP=f"{d}/no-rex", STOP_WAIT_S=2)
        ledger.show = lambda bid: {"comments": [{"text": t} for t in comments]}
        ledger.comment = lambda bid, text, author=None: comments.append(text)
        os.environ["PATH"] = fake_rex(d)
        try:
            for state in ("done", "working"):  # idle and busy: the text, then Enter, then kill
                assert try_send(state, READY_PANE) is None
                name = calls[0][1]
                assert calls[0] == ("new", name, "--", f"{BIN}/claude", "attach", "s1"), calls
                assert calls[-3:] == [("send", "-s", name, "status?"), ("send-key", "-s", name, "Enter"),
                                      ("kill", name)], calls
            dialog = "⏺ Bash(rm x)\n Do you want to proceed?\n ❯ 1. Yes\n   2. No\n"
            typed = READY_PANE.replace("❯ \n", "❯ half a message\n")
            for screen, why in ((dialog, "a dialog"), (typed, "typed text"), ("loading\n", "no Claude input box")):
                e = try_send("working", screen)
                assert e and why in str(e), (why, e)
                assert not [c for c in calls if c[0] in ("send", "send-key")] and calls[-1][0] == "kill", calls
            e = try_send("blocked", READY_PANE)  # claude agents: waiting
            assert e and "waits" in str(e) and calls == [], calls
            pane[0] = None  # rex server not reachable, then the binary missing
            for path in (os.environ["PATH"], "/usr/bin:/bin"):
                os.environ["PATH"] = path
                e = try_send("working", None)
                assert isinstance(e, NoRex) and not [c for c in calls if c[0] in ("send", "kill")], (e, calls)
            assert [t.split("\n")[0] for t in comments] == ["reconcile-note: nudge-no-rex attempt=1"], comments
            assert try_send("stopped", READY_PANE, alive=False) is None and calls == [("resume", "s1")], calls
        finally:
            g.update(saved)
            ledger.show, ledger.comment = saved_ledger
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def executor_self_test():
    """W11: switches, journal, E6, E8b, crash recovery, end to end against a scratch br store (real br source,
    real comments and labels). Fake sessions, leases and Inbox; fault injection wraps the tracker writes."""
    import threading
    returned, flagged, fail = [], [], {"comment": None, "source": False, "unlabel": False}
    slots = []
    real = ledger.comment, ledger.label

    def comment(bid, text, author=None):
        if fail["comment"] and text.startswith(fail["comment"]):
            fail["comment"] = None
            raise ledger.LedgerError("br comments failed (crash after the action)")
        return real[0](bid, text, author)

    def label(bid, add=(), remove=()):
        if remove and fail["unlabel"]:
            fail["unlabel"] = False
            raise ledger.LedgerError("br label remove failed")
        return real[1](bid, add, remove)

    def treehouse(s):
        if fail["source"]:
            raise SourceError("treehouse status: no answer in 30s")
        s["slots"] = [x for x in slots if x["slot"] not in returned]
    def git(s):  # every leased slot clean and its attempt branch pushed: the release preflight passes
        s["slot_git"] = {x["slot"]: {"dirty": False, "unpushed": False} for x in s["slots"]}
        s["git"] = {f"{m[1]}#{m[2]}": {"pushed": True} for x in s["slots"] if (m := HOLDER_RE.match(x["holder"]))}
    fakes = {"agents": lambda s: None, "treehouse": treehouse, "inbox": lambda s: None, "plannotator": lambda s: None,
             "git": git}
    slot_of =lambda a: a["action"].split()[1][len("slot="):]

    def act_flag(a, snap):
        time.sleep(0.2)  # long enough for a concurrent tick to overlap
        flagged.append(a["item"])
    executors = {"return-lease": (lambda a, snap: slot_of(a) in returned, lambda a, snap: returned.append(slot_of(a))),
                 "flag-zombie": (lambda a, snap: False, act_flag)}
    texts = lambda bid: [c["text"] for c in ledger.show(bid).get("comments") or []]
    labels = lambda bid: ledger.show(bid).get("labels") or []

    def bead(title, slot=None, zombie=False):
        bid = ledger.create(title)["id"]
        real[0](bid, f"attempt: 1 session: s-{bid} slot: /nonexistent/{bid} branch: u/x/{bid} brief: /b")
        if zombie:
            ledger.update(bid, status="in_progress", assignee=f"agent:{bid}#1")
        if slot:
            slots.append({"slot": slot, "holder": f"agent:{bid}#1"})
        return bid

    saved_env = {k: os.environ.get(k) for k in ("IAR_WORKFLOW", "BEADS_DIR")}
    with tempfile.TemporaryDirectory() as d:
        wf = os.path.join(d, "WORKFLOW.md")
        with open(os.path.join(IAR, "WORKFLOW.md")) as src, open(wf, "w") as dst:
            dst.write(src.read())
        os.environ.update(IAR_WORKFLOW=wf, BEADS_DIR=os.path.join(d, ".beads"))
        ledger.comment, ledger.label = comment, label
        try:
            subprocess.run(["br", "init", "--prefix", "rt"], cwd=d, check=True, capture_output=True, timeout=30)
            set_switches(None, wf)  # start all off, whatever the live switches are
            sl, zm = bead("stale lease", slot="4"), bead("zombie", zombie=True)
            run = lambda: [x for x in tick(d, False, fakes, executors)[1]["executed"] if x["outcome"] != "off"]
            # Switches: all off -> nothing acts. --enable turns one class on; an unknown class fails.
            assert run() == [] and not returned + flagged
            try:
                set_switches("no-such-class")
                raise AssertionError("set_switches accepted an unknown class")
            except workflow.WorkflowError:
                pass
            assert set_switches("zombie")["zombie"] is True and not workflow.load(wf)[0]["actions"]["stale-lease"]
            # E8b: a failing tick does nothing; the next healthy tick does each pending action once.
            fail["source"] = True
            assert run() == [] and not flagged
            fail["source"] = False
            # E6: two ticks started at the same time do one action.
            start, codes = threading.Barrier(2), []

            def racer():
                start.wait()
                codes.append(tick(d, False, fakes, executors)[0])
            threads = [threading.Thread(target=racer) for _ in range(2)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            assert flagged == [zm] and 0 in codes, (flagged, codes)
            assert texts(zm)[-2:] == ["reconcile-start: zombie flag-zombie attempt=1",
                                      "reconcile: flag-zombie attempt=1"], texts(zm)
            assert PENDING not in labels(zm) and run() == [] and flagged == [zm]
            # A stale snapshot still proposing a recorded key: the fresh check before acting skips it.
            assert execute({"errors": {}, "issues": {}}, [{"item": zm, "class": "zombie", "action": "flag-zombie attempt=1"}],
                           executors)[0]["outcome"] == "recorded" and flagged == [zm]
            # Crash between "action done" and "comment written". The effect (lease returned) removes the
            # candidate, so only the open intent (label + reconcile-start:) brings the next tick back to it.
            assert set_switches("stale-lease")["stale-lease"] is True
            key = f"return-lease slot=4 holder=agent:{sl}#1"
            fail["comment"] = "reconcile: return-lease"
            assert run() == [{"item": sl, "action": key,
                              "outcome": "error: LedgerError: br comments failed (crash after the action)"}]
            assert returned == ["4"] and PENDING in labels(sl)
            assert run() == [{"item": sl, "action": key, "outcome": "recovered"}]
            assert returned == ["4"] and PENDING not in labels(sl) and run() == []
            assert texts(sl)[-2:] == [f"reconcile-start: stale-lease {key}", f"reconcile: {key}"], texts(sl)
            # An intent with no effect that is no longer proposed is dropped, not acted on.
            dr = bead("dropped intent")
            real[0](dr, f"reconcile-start: stale-lease return-lease slot=9 holder=agent:{dr}#1")
            real[1](dr, add=[PENDING])
            assert [x["outcome"] for x in run()] == ["dropped"] and returned == ["4"]
            assert PENDING not in labels(dr) and run() == []
            # A crash after the reconcile: comment, before the label drop: the next tick drops the stale label,
            # but not after --kill.
            lb = bead("label left", slot="6")
            fail["unlabel"] = True
            assert [x["outcome"] for x in run()] == ["error: LedgerError: br label remove failed"]
            assert returned == ["4", "6"] and PENDING in labels(lb)
            assert not any(set_switches().values()) and not any(workflow.load(wf)[0]["actions"].values())
            assert run() == [] and PENDING in labels(lb)  # --kill: no tracker write at all
            set_switches("stale-lease")
            assert run() == [{"item": lb, "action": f"unlabel {PENDING}", "outcome": "done"}]
            assert PENDING not in labels(lb) and run() == [] and returned == ["4", "6"]
            with open(os.path.join(d, "reconcile.jsonl")) as f:
                assert all(json.loads(x)["dry_run"] is False for x in f)
        finally:
            ledger.comment, ledger.label = real
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
