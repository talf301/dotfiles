#!/usr/bin/env python3
"""linear_mirror: one-way mirror of beads into Tal's private Linear team TAL (precog-am8w W25).

Beads stay the ledger; Linear is a view (web and mobile). One Linear issue per bead that is not closed, and a
bead closed after it was mirrored moves its issue to Done (Canceled when the close reason says superseded,
won't do, duplicate, obsolete or moot). Fields: title, status, a short summary, the bead id and the Inbox link.
Child beads get a label named after their parent bead (one label per epic; no Linear projects).

Status: open -> Todo (ready) or Backlog (blocked by an open bead; also deferred), in_progress -> In Progress,
an open question about the bead, an open question bead or the label needs-human -> In Review, closed -> Done or
Canceled.

Idempotent: the issues of team TAL are read on each run, and each mirrored issue's description ends with
`Bead: <id> (v<hash of the description>)`. A run writes only what differs (title, state, a missing label, the
description hash), so a rerun writes nothing. After a create, the bead gets a `linear: <identifier> <url>`
comment; a run that finds the issue but not the comment (a crash in between) writes the comment only.
At most MAX_WRITES Linear writes per run; the rest go on the next run.

Answers (W25b, answers()): on the issue of an open question bead, Tal's comment `answer: <text>` or his move to the
Approved state ("approve") is an answer; anyone else's is ignored. Read only here: reconcile imports the earliest
through iar/answer. The mirror keeps an answered question issue in Approved until the bead closes.

Gate (autonomy.md): writes to team TAL are not gated. Every write names its team, and any team other than
TAL is refused.

Auth: a Linear OAuth app, client_credentials grant (actor=app). Client id and secret come from the macOS
Keychain (`security find-generic-password -s linear-iar-mirror -a client_id|client_secret -w`), never from a
file. The token is cached in $IAR_STATE_DIR/linear-token.json (default ~/.local/state/iar) with its expiry and
fetched again on a 401.

Run by iar/reconcile.py: `reconcile.py --dry-run` lists the writes (with no credentials it plans from the
`linear:` bead comments only); a live tick writes them when the WORKFLOW.md switch `linear-mirror` is on.

  linear_mirror.py --check       read only: get a token, read team TAL, print its states, labels and issue count
  linear_mirror.py --self-test   scratch br store and a fake Linear (token endpoint and GraphQL) on localhost
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

IAR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, IAR)
import ledger  # noqa: E402
import question  # noqa: E402

TAL_TEAM = "c2a9d57a-f287-4779-a6d5-e75d9e079dab"  # Linear team TAL (private); the only team the mirror writes to
API_URL = "https://api.linear.app/graphql"
TOKEN_URL = "https://api.linear.app/oauth/token"
KEYCHAIN_SERVICE = "linear-iar-mirror"
TIMEOUT_S = 30
MAX_WRITES = 50  # ponytail: a first run of about 100 beads takes two ticks; raise it if Tal wants it in one
STATES = ("Backlog", "Todo", "In Progress", "In Review", "Done", "Canceled")
MARK_RE = re.compile(r"^Bead: (\S+) \(v([0-9a-f]{8})\)\s*$", re.M)
COMMENT_RE = re.compile(r"^linear: (\S+) (\S+)")
BEAD_RE = re.compile(r"^([a-z0-9]+-[a-z0-9.]+): ")  # as in reconcile.py: an Inbox subject that names a bead
CANCELED_RE = re.compile(r"(?i)supersed|won'?t|wontfix|duplicate|obsolete|\bmoot\b|cancel")
TAL_USER = "c5f408e0-0224-4cff-9fd0-97df724acd09"  # Tal's Linear user: the only author whose answers count
APPROVED = "Approved"  # a TAL state; Tal moving a question issue to it is the answer "approve"
ANSWER_RE = re.compile(r"(?is)\A\s*answer:\s*(.*?)\s*\Z")


class LinearError(RuntimeError):
    pass


# --- what Linear should show: pure ---

def summary(desc, limit=280):
    """The first paragraph of the description that is not a heading, on one line."""
    for para in re.split(r"\n\s*\n", desc or ""):
        text = " ".join(line.strip() for line in para.splitlines() if not line.lstrip().startswith("#")).strip()
        if text:
            return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
    return ""


def state_of(b, asked):
    """Linear state name for bead `b`; `asked` is the set of bead ids that an open question bead asks about."""
    labels = b.get("labels") or []
    if b["status"] == "closed":
        return "Canceled" if CANCELED_RE.search(b.get("close_reason") or "") else "Done"
    if "question" in labels or "needs-human" in labels or b["id"] in asked:
        return "In Review"
    if b["status"] == "in_progress":
        return "In Progress"
    blocked = any(d.get("dependency_type") == "blocks" and d.get("status") != "closed"
                  for d in b.get("dependencies") or [])
    return "Backlog" if blocked or b["status"] != "open" else "Todo"


def thread_links(inbox, inbox_url):
    """{bead id: Inbox link} from reconcile's unresolved threads whose subject starts with `<bead>: ` (last wins)."""
    return {m[1]: f"{inbox_url}#thread={t['id']}" for t in sorted(inbox, key=lambda t: t.get("last_at") or "")
            if (m := BEAD_RE.match(t.get("subject") or ""))}


def desired(beads, links, inbox_url="", epics=None):
    """{bead id: {title, state, label, description, fp}} for the beads to mirror (with comments, deps, parent).
    epics: {bead id: its top ancestor} (scope() fills it); default: the parent. The label is that epic, and an
    epic with mirrored children gets its own label. A question bead shows its question and its own Inbox thread."""
    epics = epics or {b["id"]: b["parent"] for b in beads if b.get("parent")}
    asked, roots, texts, out = set(), set(epics.values()), {}, {}
    for b in beads:
        if "question" in (b.get("labels") or []):
            f = question.parse_body(b.get("description"))
            if b["status"] != "closed":
                asked.add(f.get("asking"))
            text = f.get("question") or ""
            if (f.get("inbox_thread") or "").startswith("msg_"):
                links = {**links, b["id"]: f"{inbox_url}#thread={f['inbox_thread']}"}
        else:
            text = summary(b.get("description"))
        texts[b["id"]] = text
    for b in beads:
        body = "\n".join(x for x in (texts[b["id"]], f"Inbox: {links[b['id']]}" if b["id"] in links else "") if x)
        fp = hashlib.sha1(f"{b['id']}\n{body}".encode()).hexdigest()[:8]
        out[b["id"]] = {"title": b["title"][:255], "state": state_of(b, asked), "fp": fp,
                        "answered": {"question", "answered"} <= set(b.get("labels") or []),
                        "label": epics.get(b["id"]) or (b["id"] if b["id"] in roots else None),
                        "description": f"{body}\n\nBead: {b['id']} (v{fp})".lstrip()}
    return out


def plan(want, remote, comments):
    """Linear writes (and bead comments) that make Linear show `want`.
    remote: {"labels": {name: id}, "issues": {bead: {id, identifier, url, title, state, labels, fp}}}; an issue known
    only from a bead comment (offline plan) has state None and is not compared. comments: {bead: [text, ...]}."""
    writes = [{"op": "label", "name": n} for n in sorted({w["label"] for w in want.values()} - {None}
                                                         - set(remote["labels"]))]
    for bid, w in sorted(want.items()):
        have = remote["issues"].get(bid)
        if not have:
            writes.append({"op": "create", "bead": bid, **w})
            continue
        if not any(c.startswith(f"linear: {have['identifier']} ") for c in comments.get(bid, [])):
            writes.append({"op": "comment", "bead": bid, "text": f"linear: {have['identifier']} {have['url']}"})
        if have.get("state") is None:
            continue
        change = {k: w[k] for k in ("title", "state", "fp") if w[k] != have[k]}
        # An answered question keeps Approved until it closes: the tick imported Tal's move before the mirror ran.
        # A move nobody imported (not Tal's) goes back to In Review.
        if w["answered"] and have["state"] == APPROVED and w["state"] == "In Review":
            change.pop("state", None)
        if w["label"] and w["label"] not in have["labels"]:
            change["label"] = w["label"]
        if change:
            if "fp" in change:
                change["description"] = w["description"]
            writes.append({"op": "update", "bead": bid, "issue": have["id"], "identifier": have["identifier"],
                           "team": have["team"], "labels": have["labels"], **change})
    return writes


def brief(w):
    """One short line per write for reconcile.jsonl (no descriptions: a dry run repeats every minute)."""
    if w["op"] == "label":
        return f"label {w['name']}"
    if w["op"] == "create":
        return f"create {w['bead']} [{w['state']}] {w['title'][:60]}"
    if w["op"] == "comment":
        return f"comment {w['bead']} {w['text']}"
    return f"update {w['bead']} {w['identifier']} " + ",".join(k for k in ("title", "state", "fp", "label") if k in w)


def tal_answer(issue):
    """{text, at} of the earliest answer Tal gave on a question issue: a comment `answer: <text>`, or a move to the
    Approved state ("approve"). Comments and moves by anyone else (the mirror app included) are ignored."""
    got = [(c["createdAt"], m[1]) for c in issue["comments"]["nodes"]
           if (c.get("user") or {}).get("id") == TAL_USER and (m := ANSWER_RE.match(c.get("body") or "")) and m[1]]
    got += [(h["createdAt"], "approve") for h in issue["history"]["nodes"]
            if (h.get("actor") or {}).get("id") == TAL_USER and (h.get("toState") or {}).get("name") == APPROVED]
    return {"text": min(got)[1], "at": min(got)[0]} if got else None


# --- Linear client ---

def credentials():
    out = []
    for account in ("client_id", "client_secret"):
        p = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
        if p.returncode != 0 or not p.stdout.strip():
            raise LinearError(f"no {account} in the Keychain (service {KEYCHAIN_SERVICE}): {p.stderr.strip()}")
        out.append(p.stdout.strip())
    return out


def state_dir():
    d = os.environ.get("IAR_STATE_DIR") or os.path.expanduser("~/.local/state/iar")
    os.makedirs(d, exist_ok=True)
    return d


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return 200, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"errors": [{"message": body[:300]}]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LinearError(f"{url}: {type(e).__name__}: {e}")


class Linear:
    def __init__(self, team=TAL_TEAM):
        self.team = self.guard(team)

    @staticmethod
    def guard(team):
        if team != TAL_TEAM:
            raise LinearError(f"refused: team {team} is not TAL ({TAL_TEAM}); Linear writes outside TAL are gated")
        return team

    def token(self, fresh=False):
        path = os.path.join(state_dir(), "linear-token.json")
        if not fresh:
            try:
                with open(path) as f:
                    cached = json.load(f)
                if cached["expires_at"] > time.time() + 60:
                    return cached["access_token"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        cid, secret = credentials()
        code, out = _post(TOKEN_URL, urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": cid, "client_secret": secret, "scope": "read,write",
        }).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
        if code != 200 or not isinstance(out, dict) or not out.get("access_token"):
            raise LinearError(f"token endpoint: HTTP {code}: {json.dumps(out)[:300]}")
        with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(path), delete=False) as f:
            json.dump({"access_token": out["access_token"],
                       "expires_at": time.time() + float(out.get("expires_in") or 3600)}, f)
        os.chmod(f.name, 0o600)
        os.replace(f.name, path)
        return out["access_token"]

    def gql(self, query, variables=None):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        for fresh in (False, True):
            code, out = _post(API_URL, body, {"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token(fresh)}"})
            auth = code == 401 or any((e.get("extensions") or {}).get("code") == "AUTHENTICATION_ERROR"
                                      for e in (out or {}).get("errors") or [])
            if not auth:
                break
        if code != 200 or (out or {}).get("errors"):
            raise LinearError(f"graphql: HTTP {code}: {json.dumps((out or {}).get('errors'))[:300]}")
        return out["data"]

    def fetch(self):
        """{"states": {name: id}, "labels": {name: id}, "issues": {bead: issue}} of team TAL (issues by their mark)."""
        out = {"states": {s["name"]: s["id"] for s in self.pages("states", "id name")},
               "labels": {x["name"]: x["id"] for x in self.pages("labels", "id name")}, "issues": {}}
        for i in self.pages("issues", "id identifier url title description state { name } labels { nodes { name } }",
                            ", includeArchived: true"):
            if m := MARK_RE.search(i.get("description") or ""):
                out["issues"][m[1]] = {"id": i["id"], "identifier": i["identifier"], "url": i["url"],
                                       "title": i["title"], "state": i["state"]["name"], "fp": m[2],
                                       "team": self.team, "labels": [x["name"] for x in i["labels"]["nodes"]]}
        return out

    def pages(self, field, nodes, args=""):
        """Every node of team TAL's `field` connection, 250 per request."""
        q = (f"query($team: String!, $after: String) {{ team(id: $team) {{ id {field}(first: 250, after: $after"
             f"{args}) {{ nodes {{ {nodes} }} pageInfo {{ hasNextPage endCursor }} }} }} }}")
        out, after = [], None
        while True:
            team = self.gql(q, {"team": self.team, "after": after})["team"]
            if not team or team["id"] != self.team:
                raise LinearError("team TAL is not visible to the app: give the app access to the TAL team")
            out += team[field]["nodes"]
            if not team[field]["pageInfo"]["hasNextPage"]:
                return out
            after = team[field]["pageInfo"]["endCursor"]

    def question_issue(self, identifier):
        """A mirrored question issue with all its comments and state history (read only; team TAL only)."""
        out = {}
        for field, nodes in (("comments", "body createdAt user { id }"),
                             ("history", "createdAt actor { id } toState { name }")):
            out[field], after = {"nodes": []}, None
            while True:
                i = self.gql(f"query($id: String!, $after: String) {{ issue(id: $id) {{ team {{ id }} {field}("
                             f"first: 250, after: $after) {{ nodes {{ {nodes} }} pageInfo {{ hasNextPage endCursor }} "
                             "} } }", {"id": identifier, "after": after})["issue"]
                if not i or i["team"]["id"] != self.team:
                    raise LinearError(f"{identifier} is not an issue of team TAL")
                out[field]["nodes"] += i[field]["nodes"]
                if not i[field]["pageInfo"]["hasNextPage"]:
                    break
                after = i[field]["pageInfo"]["endCursor"]
        return out

    def mutate(self, name, query, variables):
        """One mutation; `success: false` is an error, so a failed write is never reported as done."""
        r = (self.gql(query, variables) or {}).get(name) or {}
        if r.get("success") is not True:
            raise LinearError(f"{name}: success={r.get('success')}")
        return r

    def label(self, name):
        return self.mutate("issueLabelCreate", "mutation($input: IssueLabelCreateInput!) { issueLabelCreate("
                           "input: $input) { success issueLabel { id } } }",
                           {"input": {"name": name, "teamId": self.guard(self.team)}})["issueLabel"]["id"]

    def create(self, fields):
        return self.mutate("issueCreate", "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
                           "{ success issue { id identifier url } } }",
                           {"input": {**fields, "teamId": self.guard(self.team)}})["issue"]

    def update(self, issue, team, fields):
        self.guard(team)
        self.mutate("issueUpdate", "mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, "
                    "input: $input) { success } }", {"id": issue, "input": fields})


# --- one run ---

def scope(remote_beads):
    """Beads to mirror, with comments and deps: every bead that is not closed, and mirrored ones that closed."""
    issues = ledger.list_issues(status="all")
    ids = {i["id"] for i in issues if i["status"] != "closed"}
    ids |= set(remote_beads) & {i["id"] for i in issues}
    beads = ledger.show_many(sorted(ids))
    close = {i["id"]: i.get("close_reason") for i in issues}
    return [{**b, "close_reason": b.get("close_reason") or close.get(b["id"])} for b in beads]


def epics_of(beads):
    """{bead id: its top ancestor} for beads with a parent; fetches ancestors that are not in `beads` (closed)."""
    parent = {b["id"]: b.get("parent") for b in beads}
    while missing := sorted({p for p in parent.values() if p and p not in parent}):
        parent.update({b["id"]: b.get("parent") for b in ledger.show_many(missing)})
    out = {}
    for bid, p in parent.items():
        seen = {bid}
        while p and parent.get(p) and parent[p] not in seen:  # a cycle stops the walk
            seen.add(p)
            p = parent[p]
        if p:
            out[bid] = p
    return {b["id"]: out[b["id"]] for b in beads if b["id"] in out}


def offline(issues_with_comments):
    """remote as known from `linear:` bead comments alone (no Linear read)."""
    out = {}
    for b in issues_with_comments:
        for c in b.get("comments") or []:
            if m := COMMENT_RE.match(c.get("text") or ""):
                out[b["id"]] = {"identifier": m[1], "url": m[2], "state": None}
    return {"states": {}, "labels": {}, "issues": out}


def apply(writes, client, remote, done):
    """Do the writes in order (labels first); appends one brief line per write done to `done`. Stops at the first
    error, so `done` still shows what was written before it."""
    labels = dict(remote["labels"])
    for w in writes[:MAX_WRITES]:
        if w["op"] == "label":
            labels[w["name"]] = client.label(w["name"])
        elif w["op"] == "create":
            r = client.create({"title": w["title"], "description": w["description"],
                               "stateId": remote["states"][w["state"]],
                               "labelIds": [labels[w["label"]]] if w["label"] else []})
            ledger.comment(w["bead"], f"linear: {r['identifier']} {r['url']}")
        elif w["op"] == "comment":
            ledger.comment(w["bead"], w["text"])
        else:
            fields = {k: v for k, v in (("title", w.get("title")), ("description", w.get("description"))) if v}
            if "state" in w:
                fields["stateId"] = remote["states"][w["state"]]
            if "label" in w:
                fields["labelIds"] = [labels[n] for n in w["labels"] if n in labels] + [labels[w["label"]]]
            client.update(w["issue"], w["team"], fields)
        done.append(brief(w))


def answers(questions, client=None):
    """W25b: Tal's Linear answers to open, undelivered question beads (reconcile's snap["questions"]) whose issue the
    mirror made. Returns ({qid: {text, at, source: "linear"}}, error or None). Read only; reconcile imports them
    through iar/answer (import-answer), so the first recorded answer wins and the delivery happens once."""
    # Answered questions are read too until delivered: Tal's Linear answer given before a timeout answer overrides it
    # (iar/answer). A second human answer is late for iar/answer, as from the Inbox.
    want = sorted(q["id"] for q in questions if "delivered" not in (q.get("labels") or []))
    out = {}
    try:
        issues = offline(ledger.show_many(want) if want else [])["issues"]
        if issues:
            client = client or Linear()
        for qid, i in sorted(issues.items()):
            if got := tal_answer(client.question_issue(i["identifier"])):
                out[qid] = {**got, "source": "linear"}
    except (LinearError, ledger.LedgerError, subprocess.TimeoutExpired) as e:
        return out, f"{type(e).__name__}: {e}"
    return out, None


def run(dry_run, inbox=(), inbox_url="", client=None):
    """One mirror pass. Returns {"writes": [brief], "done": [brief] (live only), "error": str or None}."""
    out = {"writes": [], "error": None}
    try:
        client = client or Linear()
        try:
            remote = client.fetch()
        except LinearError as e:
            if not dry_run:
                raise
            remote, out["error"] = None, f"offline plan (no Linear read): {e}"
        beads = scope(remote["issues"] if remote else {})
        remote = remote or offline(beads)
        if not dry_run and (missing := set(STATES) - set(remote["states"])):
            raise LinearError(f"team TAL has no state {sorted(missing)}")
        writes = plan(desired(beads, thread_links(inbox, inbox_url), inbox_url, epics_of(beads)), remote,
                      {b["id"]: [c.get("text") or "" for c in b.get("comments") or []] for b in beads})
        out["writes"] = [brief(w) for w in writes]
        if not dry_run:
            out["done"] = []
            apply(writes, client, remote, out["done"])
    except (LinearError, ledger.LedgerError, subprocess.TimeoutExpired) as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# --- self-test: scratch br store, fake Linear on localhost ---

def self_test():
    import http.server
    import threading

    team_issues, calls, tokens = [], [], []
    states = {n: f"st-{n}" for n in STATES}
    labels = {}

    class Fake(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            if self.path == "/oauth/token":
                form = urllib.parse.parse_qs(body)
                assert form["grant_type"] == ["client_credentials"] and form["client_secret"] == ["sec"], form
                tokens.append(f"tok{len(tokens) + 1}")
                return self.reply(200, {"access_token": tokens[-1], "expires_in": 3600})
            if self.headers["Authorization"] != f"Bearer {tokens[-1] if tokens else ''}":
                return self.reply(401, {"errors": [{"message": "bad token"}]})
            req = json.loads(body)
            q, v = req["query"], req["variables"]
            calls.append(q.split("(")[0].split()[0] + (" " + q.split("{")[1].split("(")[0].strip() if "mutation" in q
                                                       else ""))
            if "issue(id:" in q:
                i = next((i for i in team_issues if i["identifier"] == v["id"]), None)
                field, start = "comments" if "comments(" in q else "history", int(v["after"] or 0)  # pages of 2
                rows = (i or {}).get(field, [])
                return self.reply(200, {"data": {"issue": i and {"team": {"id": TAL_TEAM}, field: {
                    "nodes": rows[start:start + 2],
                    "pageInfo": {"hasNextPage": start + 2 < len(rows), "endCursor": str(start + 2)}}}}})
            if q.startswith("query"):
                field = q.split("{ id ")[1].split("(")[0]
                calls[-1] += f" {field}"
                nodes = {"states": [{"id": i, "name": n} for n, i in states.items()],
                         "labels": [{"id": i, "name": n} for n, i in labels.items()],
                         "issues": [{**i, "state": {"name": i["state"]},
                                     "labels": {"nodes": [{"name": n} for n in i["labels"]]}} for i in team_issues]}
                start = int(v["after"] or 0)  # pages of 2, so the self-test covers paging
                return self.reply(200, {"data": {"team": {"id": v["team"], field: {
                    "nodes": nodes[field][start:start + 2],
                    "pageInfo": {"hasNextPage": start + 2 < len(nodes[field]), "endCursor": str(start + 2)}}}}})
            inp = v["input"]
            if "issueLabelCreate" in q:
                assert inp["teamId"] == TAL_TEAM
                labels[inp["name"]] = f"lb-{inp['name']}"
                return self.reply(200, {"data": {"issueLabelCreate": {"success": True,
                                                                      "issueLabel": {"id": labels[inp["name"]]}}}})
            by_state = {i: n for n, i in states.items()}
            by_label = {i: n for n, i in labels.items()}
            if "issueCreate" in q:
                assert inp["teamId"] == TAL_TEAM
                n = len(team_issues) + 1
                team_issues.append({"id": f"u{n}", "identifier": f"TAL-{n}", "url": f"https://linear.app/x/TAL-{n}",
                                    "title": inp["title"], "description": inp["description"],
                                    "state": by_state[inp["stateId"]], "labels": [by_label[x] for x in inp["labelIds"]]})
                return self.reply(200, {"data": {"issueCreate": {"success": True, "issue": {
                    k: team_issues[-1][k] for k in ("id", "identifier", "url")}}}})
            i = next(i for i in team_issues if i["id"] == v["id"])
            if inp.get("title") == "Refused":
                return self.reply(200, {"data": {"issueUpdate": {"success": False}}})
            i.update({k: inp[k] for k in ("title", "description") if k in inp})
            if "stateId" in inp:
                i["state"] = by_state[inp["stateId"]]
            if "labelIds" in inp:
                i["labels"] = [by_label[x] for x in inp["labelIds"]]
            return self.reply(200, {"data": {"issueUpdate": {"success": True}}})

    global API_URL, TOKEN_URL, credentials
    saved = API_URL, TOKEN_URL, credentials, {k: os.environ.get(k) for k in ("BEADS_DIR", "IAR_STATE_DIR")}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as root:
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            API_URL, TOKEN_URL = f"{base}/graphql", f"{base}/oauth/token"
            os.environ.update(BEADS_DIR=os.path.join(root, ".beads"), IAR_STATE_DIR=os.path.join(root, "state"))
            ledger.init("lm")

            # The team guard: any team but TAL is refused, for the client and for an update.
            for bad in (lambda: Linear("0000-other-team"), lambda: Linear().update("u1", "0000-other-team", {})):
                try:
                    bad()
                    raise AssertionError("a write outside team TAL was not refused")
                except LinearError as e:
                    assert "refused" in str(e), e
            assert not calls

            epic = ledger.create("Epic", type="epic", description="# Head\n\nThe epic goal.\nSecond line.")
            a = ledger.create("Ready child", type="task", parent=epic["id"])
            b = ledger.create("Blocked child", type="task", parent=epic["id"])
            ledger.dep(b["id"], a["id"])
            sub = ledger.create("Nested", type="task", parent=b["id"])
            c = ledger.create("Working", type="task", status="in_progress")
            q = ledger.create("Q about c", labels=["question"],
                              description=f"question: ok?\nasking: {c['id']}\ninbox_thread: msg_Q1\n\nlong context")
            old = ledger.create("Closed before", type="task")
            ledger.close(old["id"], reason="done")
            inbox = [{"id": "th1", "subject": f"{c['id']}: integrate?", "last_at": "2026-10-30T00:00:00Z"}]
            mine = {epic["id"], a["id"], b["id"], sub["id"], c["id"], q["id"]}

            # Dry run with no credentials: plans from bead comments, writes nothing anywhere.
            def no_keychain():
                raise LinearError("no client_id in the Keychain")
            credentials = no_keychain
            r = run(True, inbox, "http://inbox/")
            assert r["error"].startswith("offline plan"), r
            assert sorted(r["writes"]) == sorted([f"label {epic['id']}"] + [
                f"create {epic['id']} [Todo] Epic", f"create {a['id']} [Todo] Ready child",
                f"create {b['id']} [Backlog] Blocked child", f"create {c['id']} [In Review] Working",
                f"create {sub['id']} [Todo] Nested",
                f"create {q['id']} [In Review] Q about c"]), r
            assert not calls and not team_issues

            # Live run: one issue per bead, the label, a `linear:` comment on each bead.
            credentials = lambda: ("cid", "sec")
            r = run(False, inbox, "http://inbox/")
            assert r["error"] is None and len(r["done"]) == 7, r
            assert len(team_issues) == 6 and labels == {epic["id"]: f"lb-{epic['id']}"}
            got = {MARK_RE.search(i["description"])[1]: i for i in team_issues}
            assert set(got) == mine
            assert got[a["id"]]["labels"] == got[sub["id"]]["labels"] == got[epic["id"]]["labels"] == [epic["id"]]
            assert got[q["id"]]["description"].startswith("ok?\nInbox: http://inbox/#thread=msg_Q1\n\nBead: ")
            assert got[epic["id"]]["description"].startswith("The epic goal. Second line.\n\nBead: ")
            assert "Inbox: http://inbox/#thread=th1\n" in got[c["id"]]["description"]
            for bid in mine:
                assert [x["text"] for x in ledger.show(bid)["comments"]] == [
                    f"linear: {got[bid]['identifier']} {got[bid]['url']}"]

            # Rerun: no duplicates, no writes.
            n = len(calls)
            r = run(False, inbox, "http://inbox/")
            assert r == {"writes": [], "error": None, "done": []}, r
            assert len(team_issues) == 6 and set(calls[n:]) == {"query states", "query labels", "query issues"}
            assert calls[n:].count("query issues") == 3, calls[n:]  # 6 issues, pages of 2

            # Closed bead -> Done; closed as superseded -> Canceled; the unblocked open bead -> Todo.
            ledger.close(a["id"], reason="integrated into u/tfriedman/pr/x at abc")
            ledger.close(q["id"], reason="superseded by lm-9")
            r = run(False, inbox, "http://inbox/")
            assert r["error"] is None, r
            assert {i["identifier"]: i["state"] for i in team_issues} == {
                got[epic["id"]]["identifier"]: "Todo", got[a["id"]]["identifier"]: "Done",
                got[b["id"]]["identifier"]: "Todo", got[c["id"]]["identifier"]: "In Progress",
                got[sub["id"]]["identifier"]: "Todo",
                got[q["id"]]["identifier"]: "Canceled"}, team_issues
            assert len(team_issues) == 6 and run(False, inbox, "http://inbox/")["writes"] == []

            # A crash after create, before the bead comment: the rerun writes the comment, not a second issue.
            d = ledger.create("Crashed", type="task")
            team_issues.append({"id": "u99", "identifier": "TAL-99", "url": "https://linear.app/x/TAL-99",
                                "title": "Crashed", "state": "Todo", "labels": [],
                                "description": desired([{**ledger.show(d["id"]), "close_reason": None}],
                                                       {})[d["id"]]["description"]})
            r = run(False, inbox, "http://inbox/")
            assert r["done"] == [f"comment {d['id']} linear: TAL-99 https://linear.app/x/TAL-99"], r
            assert len(team_issues) == 7

            # Linear answers success: false: an error, not done; the rerun tries again.
            ledger.update(d["id"], title="Refused")
            for _ in range(2):
                r = run(False, inbox, "http://inbox/")
                assert r["done"] == [] and "issueUpdate: success=False" in r["error"], r
            ledger.update(d["id"], title="Crashed")

            # A rejected cached token: one new token, then the call succeeds.
            with open(os.path.join(root, "state", "linear-token.json"), "w") as f:
                json.dump({"access_token": "stale", "expires_at": time.time() + 3600}, f)
            before = len(tokens)
            assert run(False, inbox, "http://inbox/") == {"writes": [], "error": None, "done": []}
            assert len(tokens) == before + 1

            # W25b: answers from Linear. Only Tal's `answer:` comments and moves to Approved count; the earliest wins.
            other, at = "u-someone-else", lambda s: f"2026-10-31T00:00:{s:02d}.000Z"
            q2 = ledger.create("Q2 about c", labels=["question"], description=f"question: cache?\nasking: {c['id']}")
            q3 = ledger.create("Q3 about c", labels=["question"], description=f"question: ship?\nasking: {c['id']}")
            assert run(False, inbox, "http://inbox/")["error"] is None
            q4 = ledger.create("Q4 new", labels=["question"], description=f"question: x?\nasking: {c['id']}")
            iss = {MARK_RE.search(i["description"])[1]: i for i in team_issues}
            qs = lambda: [{"id": x["id"], "labels": ledger.show(x["id"])["labels"]} for x in (q2, q3)]
            assert answers(qs()) == ({}, None)
            iss[q2["id"]]["comments"] = [
                {"body": "answer: Drop", "createdAt": at(1), "user": {"id": other}},  # not Tal: ignored
                {"body": "looks fine", "createdAt": at(2), "user": {"id": TAL_USER}},  # not an answer
                {"body": "Answer:  Keep \n", "createdAt": at(3), "user": {"id": TAL_USER}},  # on page 2
                {"body": "answer: Drop", "createdAt": at(5), "user": {"id": TAL_USER}}]  # later: loses
            iss[q2["id"]]["history"] = [{"createdAt": at(4), "actor": {"id": TAL_USER},
                                         "toState": {"name": APPROVED}}]
            iss[q3["id"]]["history"] = [{"createdAt": at(1), "actor": {"id": other}, "toState": {"name": APPROVED}},
                                        {"createdAt": at(6), "actor": {"id": TAL_USER}, "toState": {"name": APPROVED}}]
            iss[q3["id"]]["state"] = APPROVED
            n = len(calls)
            got, err = answers(qs() + [{"id": q4["id"], "labels": ["question"]}])
            assert err is None and got == {q2["id"]: {"text": "Keep", "at": at(3), "source": "linear"},
                                           q3["id"]: {"text": "approve", "at": at(6), "source": "linear"}}, got
            assert calls[n:] == ["query"] * 5, calls[n:]  # q2: 2 pages of comments + history; q3: 1 + 1
            # The mirror keeps Approved on an answered question (the tick imported the move); a move on an unanswered
            # question (nobody imported it) or on any other bead goes back to In Review.
            ledger.label(q3["id"], add=["answered"])
            iss[q2["id"]]["state"] = iss[c["id"]]["state"] = APPROVED
            r = run(False, inbox, "http://inbox/")
            assert sorted(r["done"]) == sorted([f"create {q4['id']} [In Review] Q4 new"] + [
                f"update {x['id']} {iss[x['id']]['identifier']} state" for x in (q2, c)]), r
            assert iss[q3["id"]]["state"] == APPROVED and iss[q2["id"]]["state"] == iss[c["id"]]["state"] == "In Review"
            # An answered question is read until it is delivered (a timeout answer can still lose to Tal's).
            ledger.label(q2["id"], add=["delivered"])
            n = len(calls)
            assert answers(qs())[0].keys() == {q3["id"]} and calls[n:] == ["query"] * 2
            # A Linear failure is reported, not raised.
            os.remove(os.path.join(root, "state", "linear-token.json"))
            credentials = no_keychain
            assert answers(qs()) == ({}, "LinearError: no client_id in the Keychain")
        finally:
            server.shutdown()
            API_URL, TOKEN_URL, credentials = saved[:3]
            for k, v in saved[3].items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("linear_mirror self-test: ok")


def main(argv):
    if argv == ["--self-test"]:
        self_test()
        return 0
    if argv == ["--check"]:
        try:
            r = Linear().fetch()
        except LinearError as e:
            print(f"linear_mirror: {e}", file=sys.stderr)
            return 1
        missing = sorted(set(STATES) - set(r["states"]))
        print(json.dumps({"states": sorted(r["states"]), "missing_states": missing, "labels": len(r["labels"]),
                          "mirrored_issues": len(r["issues"])}))
        return 1 if missing else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
