"""Terminal view of the agent tickets, for use through Rex without the Linear app.

  board  agent-labeled tickets grouped by state; In Review first (they need Tal)
  show   one ticket: description and the comment thread, oldest first
  reply  post an unsigned comment as Tal; on a parked ticket it wakes the agent

Reads and writes go through the same Linear MCP login as the service.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from datetime import datetime, timezone

from .config import TrackerConfig
from .linear import (LinearComment, Tools, _items, _name, _parse_ts,
                     classify_comment, list_comments, save_comment)

DONE_LIMIT = 5
TITLE_WIDTH = 56
SNIPPET_WIDTH = 72
AUTHOR = {"agent": "agent", "status": "run status", "bridge": "experiment-run",
          "symphony": "symphony", "human": "Tal"}


def _age(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "-"
    s = max(0, int((now - ts).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def _strip_signature(body: str) -> str:
    lines = body.strip().splitlines()
    if lines and lines[0].strip().startswith("🤖"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _list(tools: Tools, team: str, state: str, label: str | None, limit: int) -> list[dict]:
    args = {"team": team, "state": state, "limit": limit, "orderBy": "updatedAt"}
    if label:
        args["label"] = label
    return _items(tools.call("list_issues", args), "issues")


def latest_agent_comment(tools: Tools, ident: str) -> LinearComment | None:
    cs = [c for c in list_comments(tools, ident) if classify_comment(c.body) == "agent"]
    return max(cs, key=lambda c: c.created_at) if cs else None


def render_board(tools: Tools, cfg: TrackerConfig, *, running: Sequence[str] = (),
                 now: datetime | None = None, done_limit: int = DONE_LIMIT) -> str:
    now = now or datetime.now(timezone.utc)
    team = cfg.provider["team_key"]
    label = cfg.required_labels[0] if cfg.required_labels else None
    waiting = (cfg.provider.get("waiting_label") or "waiting").lower()
    review = cfg.provider.get("human_review_state") or "In Review"
    active = list(cfg.active_states)
    order = [review] + [s for s in active if s != review]
    groups = [(s, _list(tools, team, s, label, 50)) for s in order]
    terminal = cfg.terminal_states[0] if cfg.terminal_states else None
    if terminal and done_limit:
        groups.append((terminal, _list(tools, team, terminal, label, done_limit)[:done_limit]))
    running_set = {r.upper() for r in running}
    out = []
    for state, items in groups:
        heading = f"{state} — needs you" if state == review else state
        if state == terminal:
            heading += f" (last {done_limit})"
        out.append(f"{heading} ({len(items)})" if state != terminal else heading)
        if not items:
            out.append("  -")
        for it in items:
            ident = it.get("identifier") or it.get("id") or "?"
            labels = {(_name(x) or "").lower() for x in it.get("labels") or []}
            flags = []
            if ident.upper() in running_set:
                flags.append("running")
            if waiting in labels:
                flags.append("waiting on runs")
            age = _age(_parse_ts(it.get("updatedAt")), now)
            line = f"  {ident:<8} {age:>4}  {_clip(it.get('title') or '', TITLE_WIDTH)}"
            if flags:
                line += f"  [{', '.join(flags)}]"
            out.append(line)
            if state == review:
                c = latest_agent_comment(tools, ident)
                if c:
                    out.append(f"           ↳ {_clip(_strip_signature(c.body), SNIPPET_WIDTH)}")
        out.append("")
    out.append("symphony show TAL-N  ·  symphony reply TAL-N \"text\"")
    return "\n".join(out)


def render_issue(tools: Tools, ident: str, *, last: int = 10, now: datetime | None = None,
                 width: int = 88) -> str:
    now = now or datetime.now(timezone.utc)
    item = tools.call("get_issue", {"id": ident})
    if isinstance(item, dict) and isinstance(item.get("issue"), dict):
        item = item["issue"]
    labels = ", ".join(_name(x) or "" for x in item.get("labels") or [])
    out = [f"{item.get('identifier') or item.get('id') or ident}  {item.get('title', '')}",
           f"{_name(item.get('status')) or _name(item.get('state')) or '?'}"
           f"  ·  labels: {labels or '-'}  ·  updated "
           f"{_age(_parse_ts(item.get('updatedAt')), now)} ago",
           item.get("url") or "", ""]
    desc = (item.get("description") or "").strip()
    if desc:
        out += [_wrap(desc, width), ""]
    comments = sorted(list_comments(tools, ident), key=lambda c: c.created_at)
    hidden = max(0, len(comments) - last) if last > 0 else 0
    out.append(f"── {len(comments)} comments" + (f", showing last {last} (-n 0 for all)"
                                                   if hidden else ""))
    for c in comments[hidden:]:
        who = AUTHOR[classify_comment(c.body)]
        out += ["", f"[{who}, {_age(c.created_at, now)} ago]", _wrap(_strip_signature(c.body),
                                                                     width)]
    return "\n".join(out)


def _wrap(text: str, width: int) -> str:
    lines = []
    for ln in text.splitlines():
        lines.extend(textwrap.wrap(ln, width, replace_whitespace=False,
                                   drop_whitespace=True) or [""])
    return "\n".join(lines)


def reply(tools: Tools, ident: str, text: str) -> str | None:
    """Post `text` as Tal (unsigned, so it wakes a parked ticket). Refuses a signed body."""
    text = text.strip()
    if not text:
        raise ValueError("empty reply")
    if text.startswith("🤖"):
        raise ValueError("a reply starting with 🤖 would be read as a bot comment and not wake "
                         "the agent")
    return save_comment(tools, text, issue_ref=ident)

