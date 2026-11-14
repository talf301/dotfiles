#!/usr/bin/env python3
"""Point an unattended Codex goal thread at a Linear ticket, and watch it.

Threads run on the local Codex app-server daemon, so no terminal is needed.

  run-ticket start TAL-123 [--cwd DIR] [--budget TOKENS]
  run-ticket ls                      # state of every ticket run
  run-ticket open TAL-123            # bring the ticket's background Rex tab to the front
  run-ticket tab TAL-123             # create that tab without showing it
  run-ticket answer TAL-123 "text"   # reply to a blocked thread
  run-ticket watch                   # one pass: notify on state changes (launchd runs this)
"""

import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_rpc  # noqa: E402

STATE = Path(os.environ.get("RUN_TICKET_STATE", Path.home() / ".local/state/run-ticket"))
RUNS = STATE / "runs.json"
BRIDGE_DB = Path.home() / ".local/state/experiment-status/supervision.sqlite"
TICKET = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
NOTIFY_STATES = {"needs-you", "done", "limited", "stalled", "error"}
STALL_SECONDS = 20 * 60

INSTRUCTIONS = """\
You are running unattended for Linear issue {ticket}. Tal is not watching this thread.
Your Codex thread id is {thread}.

1. Read {ticket} and its comments with the Linear MCP. The issue defines the task and its
   definition of done. If the definition of done is missing or ambiguous, treat that as a
   blocker (step 5).
2. Follow the work skill, and the experiment-execution skill for experiments.
3. Launch any experiment only with `experiment-run run <spec> -- <command>`, passing
   `--provider codex --thread {thread}` so the monitor wakes this thread. Never resubmit a
   launch whose outcome is uncertain; reconcile it first.
4. While a launched run is in progress, do not poll or sleep. Mark the goal blocked and end
   your turn with a final line `WAITING: <run registration id>`. The experiment-run monitor
   queues a message to this thread when the run's state changes. This replaces the
   "Own the wait" section of the experiment-execution skill: do not run wait_experiment.py.
5. If you need a decision only Tal can make, post one comment on {ticket} starting
   "Question for Tal:" with the question, the options, and your recommendation. Then mark the
   goal blocked and end your turn with a final line `NEEDS INPUT: <one-line question>`.
   Make routine, reversible decisions yourself and record them as agent decisions.
6. For implementation work, make code changes in a new git worktree on a `u/tfriedman/`
   branch, not in the main checkout, and run the repository's relevant tests. Push or open a
   draft PR only if {ticket} asks for one. Never merge or approve a PR.
7. Comment on {ticket} at milestones (plan, launch or PR, result) and include the thread id.
   Keep effort proportionate to the ticket; do not add review or evidence steps it does not need.
8. Mark the goal complete only when the definition of done is met with evidence. First post a
   final comment on {ticket}: what was done, evidence (paths, run ids, metrics), and open
   questions. Do not open Plannotator or wait for interactive review.
"""


# ---------- local run records ----------

@contextmanager
def records():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(STATE / "runs.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = json.loads(RUNS.read_text()) if RUNS.is_file() else {}
        yield data
        tmp = RUNS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, RUNS)
    finally:
        os.close(fd)


def find(data, ticket):
    rec = data.get(ticket.upper())
    if not rec:
        sys.exit(f"No run-ticket record for {ticket}")
    return rec


# ---------- state derivation ----------

def live_bridge_run(thread):
    """True when the experiment bridge is still monitoring a run bound to this thread."""
    if not BRIDGE_DB.is_file():
        return False
    try:
        db = sqlite3.connect(f"file:{BRIDGE_DB}?mode=ro", uri=True, timeout=5)
        row = db.execute("SELECT 1 FROM continuations c JOIN runs r ON c.run=r.id "
                         "WHERE c.thread=? AND c.enabled=1 AND r.enabled=1 LIMIT 1", (thread,)).fetchone()
        db.close()
        return row is not None
    except sqlite3.Error:
        return False


def last_final_line(c, thread):
    turns = c.call("thread/turns/list", {"threadId": thread, "limit": 1, "itemsView": "full"}).get("data") or []
    for turn in turns:
        for item in reversed(turn.get("items") or []):
            if item.get("type") == "agentMessage" and item.get("text"):
                lines = [l for l in item["text"].strip().splitlines() if l.strip()]
                return lines[-1].strip() if lines else ""
    return ""


def derive(goal_status, thread_status, final_line, bridge_live, quiet_seconds):
    """Map Codex goal/thread status plus bridge evidence to one run-ticket state."""
    kind = thread_status.get("type")
    flags = thread_status.get("activeFlags") or []
    if kind == "systemError":
        return "error"
    if "waitingOnApproval" in flags or "waitingOnUserInput" in flags:
        return "needs-you"
    if goal_status == "complete":
        return "done"
    if goal_status in ("budgetLimited", "usageLimited"):
        return "limited"
    if goal_status == "paused":
        return "paused"
    if goal_status is None:
        return "no-goal"
    if kind == "active":
        return "running"
    if goal_status == "blocked":
        if bridge_live:
            return "waiting-run"
        if final_line.startswith("WAITING"):
            # The run ended; the bridge should wake the thread shortly.
            return "stalled" if quiet_seconds > STALL_SECONDS else "waiting-run"
        return "needs-you"
    # Goal active but thread idle: Codex should continue on its own.
    return "stalled" if quiet_seconds > STALL_SECONDS else "running"


def observe(c, rec):
    thread = rec["thread"]
    t = c.call("thread/read", {"threadId": thread})["thread"]
    goal = c.call("thread/goal/get", {"threadId": thread}).get("goal")
    goal_status = goal["status"] if goal else None
    final = last_final_line(c, thread) if goal_status == "blocked" else ""
    updated = max(t.get("updatedAt") or 0, (goal or {}).get("updatedAt") or 0)
    quiet = time.time() - updated if updated else 0
    state = derive(goal_status, t.get("status") or {}, final, live_bridge_run(thread), quiet)
    return {"state": state, "goal": goal_status, "final": final,
            "tokens": (goal or {}).get("tokensUsed"), "updated": updated}


# ---------- commands ----------

def cmd_start(args):
    ticket = args.ticket.upper()
    if not TICKET.match(ticket):
        sys.exit(f"Not a ticket id: {args.ticket}")
    cwd = str(Path(args.cwd).resolve())
    with records() as data:
        old = data.get(ticket)
        if old and old.get("last_state") not in ("done", None) and not args.force:
            sys.exit(f"{ticket} already has thread {old['thread']} ({old.get('last_state')}). "
                     "Use `run-ticket answer`/`open`, or --force for a new thread.")
        c = codex_rpc.connect()
        try:
            thread = c.call("thread/start", {"cwd": cwd})["thread"]["id"]
            c.call("thread/name/set", {"threadId": thread, "name": f"{ticket} (run-ticket)"})
            c.call("thread/goal/set", {
                "threadId": thread, "origin": "user", "tokenBudget": args.budget,
                "objective": f"Complete Linear issue {ticket} to its definition of done, with evidence "
                             f"posted on the issue, following the run-ticket instructions."})
            prompt = INSTRUCTIONS.format(ticket=ticket, thread=thread)
            if args.note:
                prompt += f"\nNote from Tal: {args.note}\n"
            c.call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": prompt}]})
        finally:
            c.close()
        data[ticket] = {"thread": thread, "cwd": cwd, "started": time.time(), "budget": args.budget,
                        "last_state": "running", "state_since": time.time()}
        if not args.no_tab:
            try:
                ensure_tab(ticket, data[ticket])
            except Exception as e:  # the thread runs without a tab; `open` retries
                print(f"Rex tab not created: {e}", file=sys.stderr)
    print(f"{ticket}: started Codex thread {thread} in {cwd} (hidden tab in Rex session {REX_SESSION})")


def cmd_ls(args):
    with records() as data:
        if not data:
            print("No ticket runs.")
            return
        c = codex_rpc.connect()
        try:
            for ticket, rec in sorted(data.items(), key=lambda kv: -kv[1]["started"]):
                try:
                    o = observe(c, rec)
                except Exception as e:
                    o = {"state": f"unreadable ({e})", "final": "", "tokens": None}
                age = (time.time() - rec["started"]) / 3600
                extra = f"  {o['final'][:70]}" if o.get("final") else ""
                print(f"{ticket:10} {o['state']:12} {age:5.1f}h  tokens={o.get('tokens')}  "
                      f"thread={rec['thread']}{extra}")
        finally:
            c.close()


REX_SESSION = "run-ticket"


def rex(*args):
    out = subprocess.run(["rex", *args], capture_output=True, text=True, timeout=20, check=True).stdout
    return json.loads(out) if out.strip().startswith("{") else out


def _find_key(obj, key):
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found:
                return found
    return None


def ensure_tab(ticket, rec):
    """Give the ticket a tab in the background Rex session, running the live Codex view."""
    cmd = ["--keep-open", "-c", rec["cwd"], "--json", "--", "codex", "resume", rec["thread"]]
    sessions = rex("ls", "--json").get("sessions", [])
    session = next((x["session_id"] for x in sessions if x.get("label") == REX_SESSION), None)
    if session and rec.get("rex_window") and session == rec.get("rex_session"):
        if rec["rex_window"] in json.dumps(rex("session", "inspect", session, "--json")):
            return rec
    if session:
        out = rex("window", "new", "-s", session, ticket, "--focus=false", *cmd)
    else:
        out = rex("new", REX_SESSION, "--window", ticket, *cmd)
        session = _find_key(out, "session_id")
    rec["rex_session"], rec["rex_window"] = session, _find_key(out, "window_id")
    return rec


def cmd_tab(args):
    ticket = args.ticket.upper()
    with records() as data:
        rec = ensure_tab(ticket, find(data, ticket))
    print(f"{ticket}: tab {rec['rex_window']} in Rex session {REX_SESSION}")


def cmd_open(args):
    ticket = args.ticket.upper()
    with records() as data:
        rec = ensure_tab(ticket, find(data, ticket))
    apps = [c["client_id"] for c in rex("client", "ls", "--json").get("clients", [])
            if (c.get("info") or {}).get("kind") == "app" and c.get("connection_state") == "connected"]
    if not apps:
        sys.exit(f"No Rex app is connected. The tab is in Rex session {REX_SESSION}.")
    rex("-C", apps[0], "do", "session.select", f"session_id={rec['rex_session']}",
        *([f"window_id={rec['rex_window']}"] if rec.get("rex_window") else []))
    print(f"{ticket}: showing tab {ticket} in Rex session {REX_SESSION}")


def cmd_answer(args):
    with records() as data:
        rec = find(data, args.ticket)
    msg = f"Reply from Tal (human instruction): {args.text}"
    subprocess.run(["codex", "queue", "--thread", rec["thread"], "--message", msg, "--resume"], check=True)


def notify(title, message):
    script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
    subprocess.run(["osascript", "-e", script, title, message], capture_output=True, timeout=10)


def cmd_watch(args):
    with records() as data:
        if not data:
            return
        c = codex_rpc.connect()
        try:
            for ticket, rec in data.items():
                if rec.get("last_state") == "done":
                    continue
                try:
                    o = observe(c, rec)
                except Exception as e:
                    print(f"{ticket}: {e}", file=sys.stderr)
                    continue
                if o["state"] != rec.get("last_state"):
                    rec["last_state"], rec["state_since"] = o["state"], time.time()
                    if o["state"] in NOTIFY_STATES:
                        detail = o["final"] or o["state"]
                        notify(f"{ticket}: {o['state']}", f"{detail[:150]} (run-ticket open {ticket})")
                    print(json.dumps({"ts": time.time(), "ticket": ticket, **o}))
        finally:
            c.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("ticket")
    s.add_argument("--cwd", default=os.getcwd())
    s.add_argument("--budget", type=int, default=3_000_000, help="Goal token budget")
    s.add_argument("--note", help="Extra instruction appended to the prompt")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-tab", action="store_true", help="Do not create a background Rex tab")
    sub.add_parser("ls")
    o = sub.add_parser("open")
    o.add_argument("ticket")
    tb = sub.add_parser("tab", help="Create the background Rex tab without showing it")
    tb.add_argument("ticket")
    a = sub.add_parser("answer")
    a.add_argument("ticket")
    a.add_argument("text")
    sub.add_parser("watch")
    args = p.parse_args()
    {"start": cmd_start, "ls": cmd_ls, "open": cmd_open, "tab": cmd_tab, "answer": cmd_answer, "watch": cmd_watch}[args.cmd](args)


if __name__ == "__main__":
    main()
