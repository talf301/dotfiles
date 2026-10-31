#!/usr/bin/env python3
"""Write board.md in Tal's vault: one static view of every piece of work in flight.

Renders the reconciler's snapshot (reconcile.py: Claude sessions, beads, treehouse slots, open Plannotator
sessions, Inbox threads) without a model, and adds a Stalled section for work that went quiet without a READY:,
a QUESTION: or an Inbox post. launchd runs `reconcile.py --dry-run --board` every minute, which writes this board
from its tick's snapshot; the file is rewritten only when its content changes, so Obsidian refreshes and iCloud
syncs only on real changes.

Usage: board.py [--stdout] [--self-test]   (a run of its own takes a fresh snapshot)
"""

import os
import sys
from datetime import datetime, timedelta, timezone

from reconcile import BEAD_RE, parse_time, snapshot

HOME = os.path.expanduser("~")
VAULT = f"{HOME}/Library/Mobile Documents/com~apple~CloudDocs/vault"
BOARD = f"{VAULT}/board.md"
BEADS_DIR = f"{HOME}/.local/share/beads/precog/.beads"

WORKING_QUIET = timedelta(hours=4)  # a working session silent this long is probably hung
RECENT = timedelta(days=3)  # idle tabs and unreported endings older than this move to loose ends
OLD = timedelta(days=7)  # anything quiet longer than this folds into the Old section


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
    if len(text) <= n:
        return text
    text = text[: n - 1] + "…"
    # A cut inside `code` or **bold** would swallow the rest of the board line (its links too); close it.
    if text.count("`") % 2:
        text += "`"
    if text.count("**") % 2:
        text += "**"
    return text


def classify(data, now):
    """Return {section: [line, …]}. Pure, so --self-test can check it."""
    sec = {name: [] for name in SECTIONS}
    sessions = data["sessions"]
    live_ids = {s["sessionId"] for s in sessions if s.get("pid")}
    bead_sessions = {BEAD_RE.match(s.get("name") or "")[1] for s in sessions if BEAD_RE.match(s.get("name") or "")}
    thread_sessions = {t["session"] for t in data["inbox"]}
    # A session blocked on `plannotator ... --gate` shows as busy but waits on Tal's approve/deny.
    gated = {p["session_id"]: p["url"] for p in sorted(data["plannotator"], key=lambda p: p.get("started") or "")
             if "--gate" in (p.get("target") or "")}
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
            add("Loose ends", f"Your Inbox reply waits on a stopped session: {link(t)} · {when}"
                f" [resolve](claudego://act/resolve/{t['id']})", last)
        elif t.get("quiet"):
            sec["Old"].append(f"Inbox (Quiet) {link(t)} · {when} [resolve](claudego://act/resolve/{t['id']})")
        elif t["last_author"] != "person":
            add("Ready / to read", f"Inbox {link(t)} · {when} [resolve](claudego://act/resolve/{t['id']})", last)

    for s in sorted(sessions, key=lambda s: s.get("last_active") or "", reverse=True):
        if s.get("name") == "first-mate" and s.get("state") != "working":
            continue
        active = parse_time(s.get("last_active"))
        quiet = now - active if active else timedelta(days=999)
        said = s.get("last_message") or ""
        name = s.get("name") or s.get("title") or s["sessionId"][:8]
        title = f" ({clip(s['title'], 50)})" if s.get("title") and s["title"] != name else ""
        pr = f" [PR]({s['pr']})" if s.get("pr") else ""
        jump = f" [open](claudego://s/{s['sessionId']})"
        if s.get("kind") == "background" and s.get("id") and s.get("state") != "working":
            jump += f" [remove](claudego://act/rm/{s['id']})"
        line = f"**{name}**{title} — {clip(said)} · {ago(active, now)}{pr}{jump}"
        reported = s["sessionId"] in thread_sessions
        if s.get("kind") == "interactive":
            if s.get("status") == "waiting":
                add("Needs you", line, active)
            elif s.get("status") == "busy" and s["sessionId"] in gated:
                add("Ready / to read", f"{line} [review]({gated[s['sessionId']]})", active)
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

    for name, error in sorted(data.get("errors", {}).items()):
        add("Loose ends", f"Reconciler source `{name}` failed, no actions this tick: {clip(error, 140)}", now)
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
        body += ["", f"> [!note]- Old ({len(sec['Old'])}): quiet more than {OLD.days} days, or Quiet in the Inbox", *[f"> - {line}" for line in sec["Old"]]]
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
            {"sessionId": "e", "pid": 5, "kind": "interactive", "name": "gated", "status": "busy", "last_active": t(1)},
        ],
        "beads": [{"id": "precog-x1", "title": "t", "updated_at": t(1)}, {"id": "precog-y2", "title": "lost", "updated_at": t(48)},
                  {"id": "precog-z3", "title": "ancient", "updated_at": t(24 * 30)}],
        "slots": [{"slot": "2", "path": "~/s/2", "holder": "agent:x", "live": False},
                  {"slot": "3", "path": "~/s/3", "holder": "agent:y", "live": True}],
        "plannotator": [{"pid": 9, "mode": "annotate", "url": "http://gate/", "target": "annotate d.html --gate",
                         "session": "gated", "session_id": "e"}],
        "inbox": [{"id": "m1", "subject": "pick", "session": "z", "last_author": "agent", "last_at": t(1),
                   "open_questions": ["A or B?"]}],
    }
    assert clip("merged at `134d3abc` now", 15) == "merged at `134…`"
    sec = classify(data, now)
    assert len(sec["Needs you"]) == 2, sec["Needs you"]
    assert any("gated" in line and "http://gate/" in line for line in sec["Ready / to read"]), sec["Ready / to read"]
    assert not any("gated" in line for line in sec["Working"])
    assert any("precog-x1" in line for line in sec["Stalled"]), sec["Stalled"]
    assert any("hung" in line for line in sec["Stalled"])
    assert any("precog-y2" in line for line in sec["Stalled"])
    assert not any("Bead `precog-x1`" in line for line in sec["Stalled"])
    assert sec["Open tabs"] and "untracked" in sec["Open tabs"][0]
    assert sec["Loose ends"] == ["Slot 2 leased by `agent:x` with no process running [return](claudego://act/slot/2)"]
    assert not any("precog-y2" in line for line in sec["Old"])
    assert any("precog-z3" in line for line in sec["Old"]), sec["Old"]
    data["errors"] = {"br": "LedgerError: tracker list: no answer in 30s"}
    assert any("`br` failed" in line for line in classify(data, now)["Loose ends"])
    print("ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        os.environ.setdefault("BEADS_DIR", BEADS_DIR)
        now = datetime.now(timezone.utc)
        body = render(classify(snapshot(now), now))
        if "--stdout" in sys.argv:
            print(body, end="")
        else:
            write(body, now)
