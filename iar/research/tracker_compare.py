#!/usr/bin/env python3
"""Helpers for tracker-migrate-test.sh (W23 br -> bd spike).

  missing LIST.json JSONL   print ids that `br list --all --json` has but the JSONL lacks
  show2jsonl                read `br show --json` on stdin, print one JSONL issue line
  diff SRC.jsonl DST.jsonl  compare a br export with a bd export; exit 1 on any difference
"""
import json
import sys
from datetime import datetime

FIELDS = ("title", "description", "design", "acceptance_criteria", "notes", "status",
          "priority", "issue_type", "assignee", "close_reason")


def rows(path):
    d = json.load(open(path))
    return d["issues"] if isinstance(d, dict) else d


def load(path):
    out = {}
    for line in open(path):
        i = json.loads(line)
        if i.get("_type", "issue") == "issue":
            out[i["id"]] = i
    return out


def ts(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() if v else None


def diff(src_path, dst_path):
    src, dst = load(src_path), load(dst_path)
    tomb = {k for k, v in src.items() if v["status"] == "tombstone"}
    live = {k: v for k, v in src.items() if k not in tomb}

    def deps(i):
        return {(d["depends_on_id"], d["type"]) for d in i.get("dependencies") or []
                if d["depends_on_id"] not in tomb}

    def comments(i):
        return sorted((c.get("author"), c["text"]) for c in i.get("comments") or [])

    fails = []
    for k, a in live.items():
        b = dst.get(k)
        if b is None:
            fails.append(f"{k}: missing")
            continue
        for f in FIELDS:
            if (a.get(f) or None) != (b.get(f) or None):
                fails.append(f"{k}.{f}: {a.get(f)!r} -> {b.get(f)!r}")
        for f in ("created_at", "closed_at"):  # bd keeps whole seconds; allow < 1 s
            x, y = ts(a.get(f)), ts(b.get(f))
            if (x is None) != (y is None) or (x is not None and abs(x - y) >= 1):
                fails.append(f"{k}.{f}: {a.get(f)} -> {b.get(f)}")
        if set(a.get("labels") or []) != set(b.get("labels") or []):
            fails.append(f"{k}.labels differ")
        if comments(a) != comments(b):
            fails.append(f"{k}.comments {len(comments(a))} -> {len(comments(b))}")
        if deps(a) != deps(b):
            fails.append(f"{k}.deps {sorted(deps(a) ^ deps(b))}")
    extra = sorted(set(dst) - set(live))

    def n(d, f):
        return sum(len(i.get(f) or []) for i in d.values())

    print(f"issues    br={len(live)} (+{len(tomb)} tombstones, not migrated) bd={len(dst)}")
    print(f"comments  br={n(live, 'comments')} bd={n(dst, 'comments')}")
    print(f"labels    br={n(live, 'labels')} bd={n(dst, 'labels')}")
    print(f"deps      br={sum(len(deps(i)) for i in live.values())} bd={n(dst, 'dependencies')}"
          f" ({n(live, 'dependencies') - sum(len(deps(i)) for i in live.values())} br deps point at tombstones)")
    print(f"ephemeral br={sum(1 for i in live.values() if i.get('ephemeral'))}"
          f" bd={sum(1 for i in dst.values() if i.get('ephemeral') or i.get('wisp'))}")
    for f in fails[:20]:
        print("DIFF", f)
    if len(fails) > 20:
        print(f"DIFF ... {len(fails) - 20} more")
    if extra:
        print("EXTRA in bd:", extra)
    ok = not fails and not extra
    print("RESULT:", "PASS" if ok else f"FAIL ({len(fails)} diffs)")
    return 0 if ok else 1


def main():
    cmd = sys.argv[1]
    if cmd == "missing":
        have = set(load(sys.argv[3]))
        print("\n".join(sorted({r["id"] for r in rows(sys.argv[2])} - have)))
    elif cmd == "show2jsonl":
        d = json.load(sys.stdin)
        d = d[0] if isinstance(d, list) else d
        # bd imports "ephemeral" as a wisp: hidden from list/export, purgeable, and deps to it
        # are skipped. In br the flag only hides the issue from JSONL, so migrate it as permanent.
        for k in ("dependents", "rollup", "ephemeral"):
            d.pop(k, None)
        print(json.dumps(d))
    elif cmd == "diff":
        sys.exit(diff(sys.argv[2], sys.argv[3]))


if __name__ == "__main__":
    main()
