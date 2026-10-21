#!/usr/bin/env python3
"""Write board.md in Tal's vault: one static view of every piece of work in flight.

Joins the same sources as the /digest skill (Claude sessions, in-progress beads, treehouse slots, open Plannotator
sessions, Inbox threads) without a model, and adds a Stalled section for work that went quiet without a READY:,
a QUESTION: or an Inbox post. launchd runs it every minute; the file is rewritten only when its content changes,
so Obsidian refreshes and iCloud syncs only on real changes.

Usage: board.py [--stdout] [--self-test]
"""

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
IAR = os.path.dirname(os.path.realpath(__file__))
VAULT = f"{HOME}/Library/Mobile Documents/com~apple~CloudDocs/vault"
BOARD = f"{VAULT}/board.md"
INBOX = f"{HOME}/.plannotator/inbox"
PRECOG = f"{HOME}/Documents/precog"
BEADS_DIR = f"{HOME}/.local/share/beads/precog/.beads"

WORKING_QUIET = timedelta(hours=4)  # a working session silent this long is probably hung
RECENT = timedelta(days=3)  # idle tabs and unreported endings older than this move to loose ends
OLD = timedelta(days=7)  # anything quiet longer than this folds into the Old section
BEAD_RE = re.compile(r"^([a-z0-9]+-[a-z0-9.]+): ")


def run(cmd, cwd=None):
    env = dict(os.environ, BEADS_DIR=BEADS_DIR, PATH=f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin")
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=cwd, env=env).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


def parse_time(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect():
    beads = json.loads(run([f"{HOME}/.local/bin/br", "list", "--status", "in_progress", "--json"]) or "[]")
    return {
        "sessions": json_lines(run([f"{IAR}/agents-digest.sh", "--all"])),
        "beads": beads if isinstance(beads, list) else beads.get("issues", []),
        "slots": parse_slots(run([f"{HOME}/.local/bin/treehouse", "status"], cwd=PRECOG)),
        "plannotator": json_lines(run([f"{IAR}/plannotator-open.sh"])),
        "inbox": inbox_threads(),
        "inbox_url": read_json(f"{INBOX}/inbox.json").get("url", "http://localhost/"),
    }


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def parse_slots(text):
    """Leased slots from `treehouse status`; a slot is live when a process line follows its lease line."""
    slots = []
    for line in text.splitlines():
        lease = re.match(r"^(\d+)\s+leased\s+(\S+)\s+\(held by (.+)\)", line)
        if lease:
            slots.append({"slot": lease[1], "path": lease[2], "holder": lease[3], "live": False})
        elif slots and re.match(r"^\s+\S.*\(\d+\)", line):
            slots[-1]["live"] = True
    return slots


def inbox_threads():
    """Unresolved Inbox threads. Each jsonl file is an event log: the last record for an id wins."""
    messages, questions = {}, {}
    for path in glob.glob(f"{INBOX}/projects/*/messages.jsonl"):
        for row in json_lines(open(path).read()):
            messages[row["id"]] = row["record"]
    for path in glob.glob(f"{INBOX}/projects/*/questions.jsonl"):
        for row in json_lines(open(path).read()):
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
            if q["message_id"] in ids and q.get("answer") is None and not q.get("superseded_by")
        ]
        result.append({
            "id": thread_id,
            "subject": root.get("subject") or root.get("thread_name") or "(no subject)",
            "session": root["author"].get("session"),
            "last_author": posts[-1]["author"]["kind"],
            "last_at": posts[-1]["created_at"],
            "open_questions": open_questions,
        })
    return result


def ago(when, now):
    if when is None:
        return "?"
    delta = now - when
    if delta < timedelta(hours=1):
        return f"{max(int(delta.total_seconds() // 60), 0)}m"
    if delta < timedelta(days=2):
        return f"{int(delta.total_seconds() // 3600)}h"
    return f"{delta.days}d"


def clip(text, n=110):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def classify(data, now):
    """Return {section: [line, …]}. Pure, so --self-test can check it."""
    sec = {name: [] for name in SECTIONS}
    sessions = data["sessions"]
    live_ids = {s["sessionId"] for s in sessions if s.get("pid")}
    bead_sessions = {BEAD_RE.match(s.get("name") or "")[1] for s in sessions if BEAD_RE.match(s.get("name") or "")}
    thread_sessions = {t["session"] for t in data["inbox"]}
    link = lambda t: f"[{clip(t['subject'], 80)}]({data['inbox_url']}#thread={t['id']})"

    def add(name, line, when):
        """Anything quiet longer than OLD folds into Old, except what needs Tal."""
        old = name != "Needs you" and (when is None or now - when > OLD)
        sec["Old" if old else name].append(line)

    for t in sorted(data["inbox"], key=lambda t: t["last_at"], reverse=True):
        last = parse_time(t["last_at"])
        when = ago(last, now)
        if t["open_questions"]:
            add("Needs you", f"Inbox {link(t)} — {clip(t['open_questions'][0])} · {when}", last)
        elif t["last_author"] == "person" and t["session"] not in live_ids:
            add("Loose ends", f"Your Inbox reply waits on a stopped session: {link(t)} · {when}", last)
        elif t["last_author"] != "person":
            add("Ready / to read", f"Inbox {link(t)} · {when}", last)

    for s in sorted(sessions, key=lambda s: s.get("last_active") or "", reverse=True):
        if s.get("name") == "first-mate" and s.get("state") != "working":
            continue
        active = parse_time(s.get("last_active"))
        quiet = now - active if active else timedelta(days=999)
        said = s.get("last_message") or ""
        name = s.get("name") or s.get("title") or s["sessionId"][:8]
        title = f" ({clip(s['title'], 50)})" if s.get("title") and s["title"] != name else ""
        pr = f" [PR]({s['pr']})" if s.get("pr") else ""
        jump = f" [open](claudego://{s['sessionId']})"
        if s.get("kind") == "background" and s.get("id") and s.get("state") != "working":
            jump += f" [remove](claudego://act/rm/{s['id']})"
        line = f"**{name}**{title} — {clip(said)} · {ago(active, now)}{pr}{jump}"
        reported = s["sessionId"] in thread_sessions
        if s.get("kind") == "interactive":
            if s.get("status") == "waiting":
                add("Needs you", line, active)
            elif s.get("status") == "busy":
                add("Working", line, now)
            elif quiet <= RECENT:
                tag = "" if s.get("bead") or reported else " _(untracked)_"
                add("Open tabs", line + tag, active)
            else:
                add("Loose ends", f"Idle tab, close it? {line}", active)
        elif s.get("state") == "working":
            add("Stalled" if quiet > WORKING_QUIET else "Working", line, now)
        elif said.startswith("QUESTION:"):
            if not reported:
                add("Needs you", line, active)
        elif said.startswith("READY:"):
            if not reported:
                add("Ready / to read", line, active)
        elif s.get("bead_in_progress"):
            add("Stalled", f"{line} — ended without READY/QUESTION, bead still in progress", active)
        elif s.get("state") == "blocked" and quiet <= RECENT and not reported:
            add("Stalled", f"{line} — ended without READY/QUESTION", active)
        elif quiet > RECENT and s.get("state") in ("blocked", "done"):
            add("Loose ends", f"Finished: {line}", active)

    for b in sorted(data["beads"], key=lambda b: b.get("updated_at") or "", reverse=True):
        if b["id"] not in bead_sessions:
            who = f" ({b['assignee']})" if b.get("assignee") else ""
            updated = parse_time(b.get("updated_at"))
            add("Stalled", f"Bead `{b['id']}`{who} in progress with no session — {clip(b['title'], 90)} · "
                f"{ago(updated, now)} [release/close](claudego://act/bead/{b['id']})", updated)

    for p in sorted(data["plannotator"], key=lambda p: p.get("started") or 0, reverse=True):
        target = os.path.basename(p.get("target") or "") or p.get("mode")
        owner = p.get("session") or "no session"
        started = parse_time(p.get("started"))
        add("Plannotator", f"[{clip(target, 60)}]({p['url']}) — {p.get('mode')}, {owner} · {ago(started, now)}"
            f" [close](claudego://act/plannotator/{p['pid']})", started)

    for slot in data["slots"]:
        if not slot["live"]:
            add("Loose ends", f"Slot {slot['slot']} leased by `{slot['holder']}` with no process running"
                f" [return](claudego://act/slot/{slot['slot']})", now)
    return sec


SECTIONS = ["Needs you", "Ready / to read", "Stalled", "Working", "Open tabs", "Plannotator", "Loose ends", "Old"]


def render(sec):
    counts = " · ".join(f"{name} {len(sec[name])}" for name in SECTIONS if sec[name])
    body = [counts or "Nothing in flight."]
    for name in SECTIONS[:-1]:
        if sec[name]:
            body += ["", f"## {name}", *[f"- {line}" for line in sec[name]]]
    if sec["Old"]:
        body += ["", f"> [!note]- Old ({len(sec['Old'])}): quiet more than {OLD.days} days", *[f"> - {line}" for line in sec["Old"]]]
    return "\n".join(body) + "\n"


def write(body, now):
    try:
        with open(BOARD) as f:
            old = f.read()
        if old.split("\n", 2)[-1] == body:
            return False
    except OSError:
        pass
    stamp = now.astimezone().strftime("%a %H:%M")
    # In place, not tmp+rename: macOS lets a launchd job modify a file in iCloud Drive but not rename over it.
    with open(BOARD, "w") as f:
        f.write(f"# Board\n_changed {stamp} · written by ~/dotfiles/iar/board.py_\n{body}")
    return True


def self_test():
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    t = lambda hours: (now - timedelta(hours=hours)).isoformat()
    data = {
        "inbox_url": "http://inbox/",
        "sessions": [
            {"sessionId": "a", "id": "a", "pid": 1, "kind": "background", "name": "q", "state": "blocked",
             "last_message": "QUESTION: which?", "last_active": t(1)},
            {"sessionId": "b", "id": "b", "pid": 2, "kind": "background", "name": "precog-x1: thing", "state": "done",
             "last_message": "All done.", "last_active": t(1), "bead": "precog-x1", "bead_in_progress": True},
            {"sessionId": "c", "id": "c", "pid": 3, "kind": "background", "name": "hung", "state": "working",
             "last_active": t(5)},
            {"sessionId": "d", "pid": 4, "kind": "interactive", "name": "chat", "status": "idle", "last_active": t(2)},
        ],
        "beads": [{"id": "precog-x1", "title": "t", "updated_at": t(1)}, {"id": "precog-y2", "title": "lost", "updated_at": t(48)},
                  {"id": "precog-z3", "title": "ancient", "updated_at": t(24 * 30)}],
        "slots": parse_slots("2     leased       ~/s/2  (held by agent:x)\n3     leased       ~/s/3  (held by agent:y)\n"
                             "                   claude (63805)\n"),
        "plannotator": [],
        "inbox": [{"id": "m1", "subject": "pick", "session": "z", "last_author": "agent", "last_at": t(1),
                   "open_questions": ["A or B?"]}],
    }
    sec = classify(data, now)
    assert len(sec["Needs you"]) == 2, sec["Needs you"]
    assert any("precog-x1" in line for line in sec["Stalled"]), sec["Stalled"]
    assert any("hung" in line for line in sec["Stalled"])
    assert any("precog-y2" in line for line in sec["Stalled"])
    assert not any("Bead `precog-x1`" in line for line in sec["Stalled"])
    assert sec["Open tabs"] and "untracked" in sec["Open tabs"][0]
    assert sec["Loose ends"] == ["Slot 2 leased by `agent:x` with no process running [return](claudego://act/slot/2)"]
    assert not any("precog-y2" in line for line in sec["Old"])
    assert any("precog-z3" in line for line in sec["Old"]), sec["Old"]
    print("ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        now = datetime.now(timezone.utc)
        body = render(classify(collect(), now))
        if "--stdout" in sys.argv:
            print(body, end="")
        else:
            write(body, now)
