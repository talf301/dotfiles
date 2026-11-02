#!/usr/bin/env python3
"""Profile Claude Code token usage from ~/.claude/projects transcripts.

Cost is a list-price-equivalent weight ($/MTok, skill pricing table cached 2026-10-06), used to rank, not a bill.
"""
import json, os, re, sys, collections as C
from datetime import datetime

ROOT = os.path.expanduser("~/.claude/projects")
# input, output, cache_read per MTok; writes = 1.25x (5m) / 2x (1h) input
PRICE = {
    "opus-5-5": (4, 20, 0.20), "opus-5": (5, 25, 0.50), "opus-4": (5, 25, 0.50),
    "sonnet-5": (2, 10, 0.20), "sonnet-4": (3, 15, 0.30),
    "haiku-5": (0.10, 0.50, 0.01), "haiku-4": (1, 5, 0.10),
    "fable": (10, 50, 0.25), "mythos": (10, 50, 0.25),
}

def rates(model):
    for k, v in PRICE.items():
        if k in model:
            return v
    return None

def project(dirname):
    d = dirname.lstrip("-")
    if "treehouse-precog" in d or d.endswith("Documents-precog") or "worktrees-precog" in d:
        return "precog"
    if "scratch-workspaces" in d:
        return "claude-desktop-scratch"
    d = re.sub(r"^Users-tal-?", "", d)
    return d or "~"

def cls(path):
    if "/workflows/" in path: return "workflow-agent"
    if "/subagents/" in path: return "subagent"
    return "main"

Z = lambda: C.defaultdict(float)
by = {k: C.defaultdict(Z) for k in ("project", "class", "model", "kind", "session")}
seen = set()
sess_meta = {}
misses = []  # (cost, session, gap_min, tokens)
startup = []  # first-turn context size of main sessions
tool_calls = C.Counter()
unpriced = C.Counter()

files = []
for dp, _, fs in os.walk(ROOT):
    files += [os.path.join(dp, f) for f in fs if f.endswith(".jsonl")]

for path in files:
    rel = os.path.relpath(path, ROOT)
    proj = project(rel.split(os.sep)[0])
    kind_c = cls(path)
    sid = rel.split(os.sep)[1].removesuffix(".jsonl")
    last_ts, first = None, True
    title = None
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"customTitle"' in line or '"agentName"' in line:
                try:
                    o = json.loads(line); title = title or o.get("customTitle") or o.get("agentName")
                except Exception: pass
            if '"usage"' not in line: continue
            try: o = json.loads(line)
            except Exception: continue
            m = o.get("message") or {}
            u = m.get("usage")
            if o.get("type") != "assistant" or not u: continue
            rid = o.get("requestId") or m.get("id")
            if rid in seen: continue
            seen.add(rid)
            model = m.get("model", "?")
            if model == "<synthetic>": continue
            r = rates(model)
            if not r: unpriced[model] += 1; continue
            inp, out, cr = u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("cache_read_input_tokens", 0)
            cc = u.get("cache_creation") or {}
            w5, w1 = cc.get("ephemeral_5m_input_tokens", 0), cc.get("ephemeral_1h_input_tokens", 0)
            if not cc: w5 = u.get("cache_creation_input_tokens", 0)
            cost = (inp * r[0] + out * r[1] + cr * r[2] + w5 * r[0] * 1.25 + w1 * r[0] * 2) / 1e6
            ts = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")) if o.get("timestamp") else None
            kind = o.get("sessionKind", "?") if kind_c == "main" else kind_c
            rec = dict(cost=cost, inp=inp, out=out, cr=cr, cw=w5 + w1, n=1,
                       think=(u.get("output_tokens_details") or {}).get("thinking_tokens", 0))
            keys = dict(project=proj, **{"class": kind_c}, model=model, kind=kind, session=f"{proj}/{sid}")
            for dim, key in keys.items():
                for f, v in rec.items(): by[dim][key][f] += v
            sess_meta[f"{proj}/{sid}"] = title or sess_meta.get(f"{proj}/{sid}")
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use": tool_calls[b.get("name")] += 1
            if kind_c == "main" and first:
                startup.append(w5 + w1 + cr + inp)
            elif w5 + w1 > 30000 and cr < (w5 + w1) and ts and last_ts:
                misses.append((((w5 + w1) * r[0] * 1.25 - (w5 + w1) * r[2]) / 1e6,
                               (ts - last_ts).total_seconds() / 60, w5 + w1, proj))
            first = False
            last_ts = ts

def table(dim, n=12):
    rows = sorted(by[dim].items(), key=lambda kv: -kv[1]["cost"])
    tot = sum(v["cost"] for v in by[dim].values())
    print(f"\n## by {dim}  (total ${tot:,.0f})")
    print(f"{'key':48} {'cost$':>9} {'%':>5} {'reqs':>7} {'outMTok':>8} {'cwMTok':>8} {'crMTok':>8} hit%")
    for k, v in rows[:n]:
        name = (sess_meta.get(k) or k)[:48] if dim == "session" else k[:48]
        hit = 100 * v["cr"] / max(1, v["cr"] + v["cw"] + v["inp"])
        print(f"{name:48} {v['cost']:9,.0f} {100*v['cost']/tot:5.1f} {v['n']:7.0f} {v['out']/1e6:8.1f} {v['cw']/1e6:8.1f} {v['cr']/1e6:8.1f} {hit:4.0f}")

dates = sorted(f for f in files)
print(f"files={len(files)} requests={len(seen)} unpriced={dict(unpriced)}")
for d in ("project", "class", "kind", "model", "session"): table(d, 15 if d == "session" else 12)

T = by["class"]
tot = {f: sum(v[f] for v in T.values()) for f in ("cost", "out", "cw", "cr", "inp", "think")}
print(f"\n## cost split (opus-5-5-equivalent shares)")
print(f"cache-read MTok {tot['cr']/1e6:.0f}, cache-write MTok {tot['cw']/1e6:.0f}, output MTok {tot['out']/1e6:.1f}, uncached in MTok {tot['inp']/1e6:.1f}")
startup.sort()
if startup:
    print(f"\n## main-session first-turn context: n={len(startup)} median={startup[len(startup)//2]:,} p90={startup[int(len(startup)*.9)]:,}")
misses.sort(reverse=True)
mc = sum(x[0] for x in misses)
gaps = C.Counter("<5m" if g < 5 else "5-60m" if g < 60 else ">1h" for _, g, _, _ in misses)
gapcost = C.defaultdict(float)
for c, g, _, _ in misses: gapcost["<5m" if g < 5 else "5-60m" if g < 60 else ">1h"] += c
print(f"\n## cache re-writes >30k tokens mid-session: n={len(misses)} excess=${mc:,.0f}")
for k in ("<5m", "5-60m", ">1h"): print(f"  gap {k:6} n={gaps[k]:5} excess=${gapcost[k]:,.0f}")
print("\n## top tools", tool_calls.most_common(15))
