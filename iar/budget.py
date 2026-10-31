#!/usr/bin/env python3
"""budget: parse a bead's `budget:` block and keep its run ledger (see iar/autonomy.md "Budgets").

  budget.py BEAD
      Check the block and print what is left. Exit 0: valid, with GPU-hours and runs left.
  budget.py run BEAD RUN_ID --status started --gpu-hours EST
      Reserve EST GPU-hours for a new run, then write its `run:` comment. A per-bead lock
      makes concurrent starts safe: a start that does not fit is refused (exit 3).
  budget.py run BEAD RUN_ID --status done|failed|killed
      Record the end of a run. Its reserved estimate keeps counting.
  budget.py --self-test
      Uses a scratch tracker store, allowlist and lock dir.

Exit codes: 0 ok, 1 no or invalid block (or tracker error), 3 budget used up, expired, or a
start that does not fit.

The allowlist is WORKFLOW.md `compute_allowlist` (iar/workflow.py). Env overrides:
IAR_WORKFLOW (file), IAR_STATE_DIR (lock dir, default ~/.local/state/iar).
"""

import argparse
import datetime
import decimal
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger  # noqa: E402  (the only module that calls the tracker)
import workflow  # noqa: E402

FIELDS = ("compute", "gpu_hours", "max_runs", "kill_if", "until")
STATUSES = ("started", "done", "failed", "killed")
RUN_RE = re.compile(r"^run: (\S+) compute: (\S+) gpu_hours: (\S+) status: (\w+)\s*$")
EXIT_INVALID, EXIT_USED_UP = 1, 3


class BudgetError(Exception):
    def __init__(self, msg, code=EXIT_INVALID):
        super().__init__(msg)
        self.code = code


def allowlist():
    try:
        return set(workflow.load()[0]["compute_allowlist"])
    except workflow.WorkflowError as e:
        raise BudgetError(f"cannot read compute allowlist: {e}")


def _positive_int(name, value):
    try:
        v = int(value)
    except ValueError:
        v = 0
    if v <= 0:
        raise BudgetError(f"{name}: must be a positive integer, got {value!r}")
    return v


def _hours(name, value):
    """GPU-hours as an exact Decimal, so sums never round past a limit."""
    try:
        v = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        v = None
    if v is None or not v.is_finite() or v <= 0:
        raise BudgetError(f"{name}: must be a positive finite number, got {value!r}")
    return v


def parse(description):
    """Return the budget dict from a bead description, or raise BudgetError."""
    blocks = [b for b in re.findall(r"^```[^\n]*\n(.*?)^```", description or "", re.M | re.S)
              if b.strip().startswith("budget")]
    if not blocks:
        raise BudgetError("no ```budget:``` block in the bead description")
    if len(blocks) > 1:
        raise BudgetError("more than one budget block")
    if blocks[0].strip().splitlines()[0].strip() != "budget:":
        raise BudgetError("the block's first line must be exactly `budget:`")
    out = {}
    for line in blocks[0].strip().splitlines()[1:]:
        if not line.strip():
            continue
        m = re.match(r"^\s+(\w+):\s*(.*?)\s*$", line)
        if not m:
            raise BudgetError(f"bad line in budget block: {line.strip()!r}")
        key, value = m.group(1), re.sub(r"\s+#.*$", "", m.group(2))
        if key not in FIELDS:
            raise BudgetError(f"unknown field {key!r} (allowed: {', '.join(FIELDS)})")
        if key in out:
            raise BudgetError(f"field {key!r} given twice")
        out[key] = value
    missing = [k for k in FIELDS if not out.get(k)]
    if missing:
        raise BudgetError(f"missing field(s): {', '.join(missing)}")
    if out["compute"] not in allowlist():
        raise BudgetError(f"compute {out['compute']!r} is not in the compute allowlist "
                          "(new compute needs a gated question)")
    out["gpu_hours"] = _hours("gpu_hours", out["gpu_hours"])
    out["max_runs"] = _positive_int("max_runs", out["max_runs"])
    try:  # Python 3.9 fromisoformat does not take "Z"
        until = datetime.datetime.fromisoformat(re.sub(r"Z$", "+00:00", out["until"]))
    except ValueError:
        until = None
    if until is None or until.tzinfo is None:
        raise BudgetError(f"until: need an ISO time with a zone, e.g. 2026-11-07T00:00Z, "
                          f"got {out['until']!r}")
    out["until"] = until
    return out


def runs(comments):
    """Latest state of each run, from `run:` comments in order: {id: (gpu_hours, status)}."""
    out = {}
    for c in comments:
        m = RUN_RE.match(c.get("text", "").strip())
        if m:
            out[m.group(1)] = (_hours(f"run {m.group(1)} gpu_hours", m.group(3)),
                               m.group(4))
    return out


def state(bead):
    """(budget, runs, used GPU-hours) for a bead. Every run counts, whatever its status."""
    issue = ledger.show(bead)
    b = parse(issue.get("description", ""))
    r = runs(issue.get("comments") or [])
    return b, r, sum(h for h, _ in r.values())


def check(bead, now=None):
    b, r, used = state(bead)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    running = sum(1 for _, s in r.values() if s == "started")
    hours_left, runs_left = b["gpu_hours"] - used, b["max_runs"] - len(r)
    print(f"compute: {b['compute']}\n"
          f"gpu_hours: {hours_left} left ({used} of {b['gpu_hours']} used)\n"
          f"runs: {runs_left} left ({len(r)} of {b['max_runs']} used, {running} running)\n"
          f"until: {b['until'].isoformat()}\n"
          f"kill_if: {b['kill_if']}")
    if now >= b["until"]:
        raise BudgetError("budget expired", EXIT_USED_UP)
    if hours_left <= 0 or runs_left <= 0:
        raise BudgetError("budget used up", EXIT_USED_UP)
    return 0


def record(bead, run_id, status, gpu_hours=None):
    if status not in STATUSES:
        raise BudgetError(f"status must be one of {', '.join(STATUSES)}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bead):
        raise BudgetError(f"bad bead id {bead!r}")
    if not re.fullmatch(r"[A-Za-z0-9._:/@-]+", run_id):
        raise BudgetError(f"bad run id {run_id!r}")
    if (gpu_hours is None) != (status != "started"):
        raise BudgetError("give --gpu-hours (the estimate to reserve) only with --status started")
    if gpu_hours is not None:
        gpu_hours = _hours("gpu_hours", gpu_hours)
    lock_dir = os.environ.get("IAR_STATE_DIR") or os.path.expanduser("~/.local/state/iar")
    os.makedirs(lock_dir, exist_ok=True)
    with open(os.path.join(lock_dir, f"budget-{bead}.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held until the comment is written
        b, r, used = state(bead)
        if status == "started":
            if run_id in r:
                raise BudgetError(f"run {run_id} already exists")
            if datetime.datetime.now(datetime.timezone.utc) >= b["until"]:
                raise BudgetError("budget expired", EXIT_USED_UP)
            if len(r) + 1 > b["max_runs"]:
                raise BudgetError(f"max_runs {b['max_runs']} reached", EXIT_USED_UP)
            if used + gpu_hours > b["gpu_hours"]:
                raise BudgetError(f"{gpu_hours} GPU-hours do not fit: "
                                  f"{b['gpu_hours'] - used} left", EXIT_USED_UP)
        else:
            if r.get(run_id, (0, ""))[1] != "started":
                raise BudgetError(f"run {run_id} is not running")
            gpu_hours = r[run_id][0]  # the estimate counts, not the actual hours
        text = f"run: {run_id} compute: {b['compute']} gpu_hours: {gpu_hours} status: {status}"
        ledger.comment(bead, text)
    print(text)
    return 0


def self_test():
    with tempfile.TemporaryDirectory() as root:
        allow = os.path.join(root, "WORKFLOW.md")
        config = dict(workflow.load()[0], compute_allowlist=["ray-a100", "sky-h100"])
        with open(allow, "w") as f:
            f.write("---\n" + json.dumps(config) + "\n---\nbrief\n")
        env = {"BEADS_DIR": os.path.join(root, ".beads"), "IAR_WORKFLOW": allow,
               "IAR_STATE_DIR": os.path.join(root, "state")}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            subprocess.run(["br", "init", "--prefix", "bt"], cwd=root, check=True,
                           capture_output=True, timeout=30)

            def bead(**fields):
                body = "".join(f"  {k}: {v}\n" for k, v in fields.items())
                return ledger.create("t", description=f"Train.\n\n```\nbudget:\n{body}```\n")["id"]

            def code(fn, *a):
                try:
                    return fn(*a)
                except BudgetError as e:
                    return e.code, str(e)

            ok = dict(compute="ray-a100   # never provision", gpu_hours=10, max_runs=3,
                      kill_if="loss is NaN", until="2099-01-01T00:00Z")

            # valid block
            b = bead(**ok)
            assert code(check, b) == 0
            p = parse(ledger.show(b)["description"])
            assert p["compute"] == "ray-a100" and p["gpu_hours"] == 10 and p["max_runs"] == 3, p

            # unknown field, compute not allowlisted, missing block / field, bad until
            c, msg = code(check, bead(**ok, region="us-east-1"))
            assert c == EXIT_INVALID and "unknown field 'region'" in msg, msg
            for head in ("budget: extra", "budget:other", "budgets:"):
                d = ledger.show(b)["description"].replace("budget:\n", head + "\n", 1)
                c, msg = code(check, ledger.create("t", description=d)["id"])
                assert c == EXIT_INVALID and "exactly `budget:`" in msg, (head, msg)
            c, msg = code(check, bead(**dict(ok, compute="new-cluster")))
            assert c == EXIT_INVALID and "not in the compute allowlist" in msg, msg
            c, msg = code(check, ledger.create("t", description="no block")["id"])
            assert c == EXIT_INVALID and "no ```budget:```" in msg, msg
            c, msg = code(check, bead(**{k: v for k, v in ok.items() if k != "kill_if"}))
            assert c == EXIT_INVALID and "kill_if" in msg, msg
            for bad in ("inf", "nan", "0", "-1"):
                c, msg = code(check, bead(**dict(ok, gpu_hours=bad)))
                assert c == EXIT_INVALID and "positive" in msg, (bad, msg)
            c, msg = code(check, bead(**dict(ok, max_runs="2.5")))
            assert c == EXIT_INVALID and "positive integer" in msg, msg
            assert code(record, b, "rx", "started", "inf")[0] == EXIT_INVALID
            assert code(record, b, "rx", "started")[0] == EXIT_INVALID  # no estimate
            c, msg = code(check, bead(**dict(ok, until="2099-01-01T00:00")))
            assert c == EXIT_INVALID and "zone" in msg, msg

            # used-up block: hours, runs, and time
            assert record(b, "r1", "started", 6) == 0
            assert code(record, b, "r2", "started", 5)[0] == EXIT_USED_UP  # 6+5 > 10
            assert code(record, b, "r1", "failed", 1)[0] == EXIT_INVALID  # estimate is fixed
            assert record(b, "r1", "failed") == 0  # failed runs keep counting
            assert runs(ledger.show(b)["comments"]) == {"r1": (6.0, "failed")}
            assert record(b, "r2", "started", 4) == 0
            assert code(check, b)[0] == EXIT_USED_UP  # 10 of 10 GPU-hours
            assert code(record, b, "r2", "started", 1)[1] == "run r2 already exists"
            b2 = bead(**dict(ok, max_runs=1))
            assert record(b2, "r1", "started", 1) == 0
            c, msg = code(record, b2, "r2", "started", 1)
            assert c == EXIT_USED_UP and "max_runs" in msg, msg
            assert code(check, bead(**dict(ok, until="2020-01-01T00:00Z")))[0] == EXIT_USED_UP

            b5 = bead(**dict(ok, gpu_hours=0.3))  # 0.1 + 0.2 fits 0.3
            assert record(b5, "f1", "started", 0.1) == 0 and record(b5, "f2", "started", 0.2) == 0

            # tiny estimates round-trip (repr may use 1e-05) and keep counting
            b4 = bead(**dict(ok, gpu_hours=0.00002))
            assert record(b4, "t1", "started", 0.00001) == 0
            assert code(record, b4, "t2", "started", 0.000011)[0] == EXIT_USED_UP
            assert runs(ledger.show(b4)["comments"]) == {"t1": (decimal.Decimal("0.00001"),
                                                                "started")}
            b6 = bead(**dict(ok, gpu_hours=1))  # no tolerance: 1e-10 over the limit is refused
            assert record(b6, "e1", "started", 1) == 0
            assert code(record, b6, "e2", "started", "1e-10")[0] == EXIT_USED_UP

            # concurrent reservations: 5 starts of 3 h on a 10 h budget -> exactly 3 fit
            b3 = bead(**dict(ok, max_runs=6))
            procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", b3,
                                       f"c{i}", "--status", "started", "--gpu-hours", "3"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for i in range(5)]
            codes = sorted(p.wait(timeout=60) for p in procs)
            assert codes == [0, 0, 0, EXIT_USED_UP, EXIT_USED_UP], codes
            r = runs(ledger.show(b3)["comments"])
            assert len(r) == 3 and sum(h for h, _ in r.values()) == 9, r
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("budget self-test: ok")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        self_test()
        return 0
    p = argparse.ArgumentParser(prog="budget.py", description=__doc__.split("\n")[0])
    if argv[:1] == ["run"]:
        p.add_argument("cmd")
        p.add_argument("bead")
        p.add_argument("run_id")
        p.add_argument("--status", required=True, choices=STATUSES)
        p.add_argument("--gpu-hours")
    else:
        p.add_argument("bead")
    a = p.parse_args(argv)
    try:
        if argv[:1] == ["run"]:
            return record(a.bead, a.run_id, a.status, a.gpu_hours)
        return check(a.bead)
    except BudgetError as e:
        print(f"budget: {e}", file=sys.stderr)
        return e.code
    except (ledger.LedgerError, OSError) as e:
        print(f"budget: {e}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
