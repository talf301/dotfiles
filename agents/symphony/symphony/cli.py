"""Command line.

  symphony [PATH]                       run the service (PATH defaults to ./WORKFLOW.md)
  symphony run [PATH | --workflow PATH] same
  symphony check [PATH | --workflow PATH]
                                        preflight: load, validate, one read-only Linear call
  symphony ls                           tickets the running service holds (from status.json)
  symphony open TICKET                  show the ticket's Rex tab (codex resume <thread>)
  symphony logs TICKET [-n N]           the ticket's structured log lines
  symphony login [--port N]             one-time browser OAuth to Linear's MCP server; tokens
                                        go to the Keychain (service symphony-linear-mcp)
  symphony bridge-event --issue TAL-N   experiment-run bridge: JSON event on stdin; upserts the
                                        run-status comment and posts the event comment once

Exit codes: 0 normal shutdown or success, 1 startup failure, check failure or abnormal exit,
2 missing workflow file. bridge-event: 0 written (or already there), 2 bad input, 3 not
logged in, 1 other failure (retry is safe). Operator commands without a path use the workflow
named in status.json, else ./WORKFLOW.md.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import rex as rex_mod
from .config import ServiceConfig, validate_dispatch_config
from .errors import SymphonyError
from .log import log, setup_logging, state_dir
from .workflow import WorkflowStore, resolve_workflow_path
from .workspace import ThreadStore, workspace_key

SUBCOMMANDS = ("run", "ls", "open", "logs", "check", "login", "bridge-event")
BRIDGE_EXIT_INPUT = 2
BRIDGE_EXIT_LOGIN = 3
STALE_MIN_S = 120


def _err(msg: str) -> None:
    print(f"symphony: {msg}", file=sys.stderr)


def status_path() -> Path:
    return state_dir() / "status.json"


def log_path() -> Path:
    return state_dir() / "symphony.jsonl"


def _read_status() -> dict | None:
    try:
        data = json.loads(status_path().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _workflow_path(explicit: str | None, *, from_status: bool = False) -> Path:
    if explicit is None and from_status:
        st = _read_status()
        if st and st.get("workflow"):
            return Path(st["workflow"])
    return resolve_workflow_path(explicit, Path.cwd())


def _load_config(path: Path) -> ServiceConfig:
    if not path.is_file():
        raise FileNotFoundError(f"workflow file not found: {path}")
    return WorkflowStore(path).load_initial()[1]


# -- run --------------------------------------------------------------------------------------

def cmd_run(path: Path, *, stop: threading.Event | None, factories: dict) -> int:
    if not path.is_file():
        _err(f"workflow file not found: {path}")
        return 2
    setup_logging(log_path())
    lock = _service_lock()
    if lock is None:
        _err("another symphony service is running")
        return 1
    try:
        return _run_locked(path, stop=stop, factories=factories)
    finally:
        lock.close()


def _service_lock():
    """Exclusive lock on <state>/symphony.lock for the life of the service; None if held."""
    lock_file = state_dir() / "symphony.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_file, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _run_locked(path: Path, *, stop: threading.Event | None, factories: dict) -> int:
    from .notify import MacNotifier
    from .orchestrator import Orchestrator, thread_worker_factory

    store = WorkflowStore(path)
    try:
        store.load_initial()
    except SymphonyError as e:
        log("startup_failed", "error", error=str(e), category=e.category)
        _err(f"startup failed: {e}")
        return 1
    kwargs = {"tracker_factory": factories["tracker"]} if factories.get("tracker") else {}
    orch = Orchestrator(store, worker_factory=factories.get("worker") or thread_worker_factory,
                        notifier=factories.get("notifier") or MacNotifier(),
                        status_path=status_path(), **kwargs)
    if stop is None:
        stop = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        orch.run(stop)
    except SymphonyError as e:
        log("startup_failed", "error", error=str(e), category=e.category)
        _err(f"startup failed: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 - abnormal exit must be visible and nonzero
        log("service_crashed", "error", error=f"{type(e).__name__}: {e}")
        _err(f"service stopped abnormally: {type(e).__name__}: {e}")
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    log("service_stopped")
    return 0


# -- check ------------------------------------------------------------------------------------

def cmd_check(path: Path, *, factories: dict) -> int:
    from .tracker import make_tracker

    print(f"workflow: {path}")
    if not path.is_file():
        print("FAIL workflow file not found")
        return 2
    try:
        cfg = WorkflowStore(path).load_initial()[1]
    except SymphonyError as e:
        print(f"FAIL {e}")
        return 1
    print(f"ok   config (workspace root {cfg.workspace_root})")
    ok = True
    try:
        validate_dispatch_config(cfg, os.environ)
        print("ok   dispatch preflight")
    except SymphonyError as e:
        print(f"FAIL {e}")
        return 1
    if cfg.codex.transport == "daemon" and not Path(cfg.codex.socket).exists():
        print(f"FAIL codex daemon socket missing: {cfg.codex.socket}")
        ok = False
    try:
        tracker = (factories.get("tracker") or make_tracker)(
            cfg.tracker, os.environ, ThreadStore(cfg.workspace_root).turn_started_at)
        viewer = getattr(tracker, "viewer_id", None)
        if viewer is not None:
            print(f"ok   linear viewer {viewer()}")
    except SymphonyError as e:
        print(f"FAIL {e}")
        ok = False
    return 0 if ok else 1


# -- ls ---------------------------------------------------------------------------------------

def _age_s(st: dict, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(st["generated_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _thread_lookup(st: dict) -> Callable[[str], tuple[str | None, int | None]]:
    """identifier -> (thread id, thread total tokens) from the ThreadStore, best effort."""
    try:
        threads = ThreadStore(_load_config(Path(st["workflow"])).workspace_root)
    except Exception:  # noqa: BLE001 - ls must work without a loadable workflow
        return lambda ident: (None, None)

    def lookup(ident: str):
        rec = threads.read(ident)
        return (rec.thread_id, rec.total_tokens) if rec else (None, None)

    return lookup


def cmd_ls(*, now: datetime | None = None) -> int:
    st = _read_status()
    if st is None:
        print(f"no status at {status_path()}; is the service running?")
        return 1
    now = now or datetime.now(timezone.utc)
    age = _age_s(st, now)
    limit = max(STALE_MIN_S, 3 * (st.get("poll_interval_ms") or 30000) / 1000)
    head = f"status {status_path()} ({'age unknown' if age is None else f'{age:.0f}s old'})"
    if age is None or age > limit or not _pid_alive(st.get("pid")):
        head += ": STALE, the service may be stopped"
    print(head)
    if st.get("error"):
        print(f"config error: {st['error']}")
    lookup = _thread_lookup(st)
    rows = []
    for r in st.get("running", []):
        claim = "parking" if r.get("parking") else "running"
        if r.get("retry_attempt"):
            claim += f" (attempt {r['retry_attempt']})"
        rows.append((r.get("issue_identifier"), r.get("state") or "-", claim,
                     r.get("thread_id") or lookup(r.get("issue_identifier"))[0] or "-",
                     str((r.get("tokens") or {}).get("total_tokens", 0)),
                     r.get("last_event") or "-"))
    for r in st.get("retrying", []):
        thread, tokens = lookup(r.get("issue_identifier"))
        due = (r.get("due_in_ms") or 0) / 1000
        rows.append((r.get("issue_identifier"), "-",
                     f"retry {r.get('attempt')} in {due:.0f}s", thread or "-",
                     "-" if tokens is None else str(tokens),
                     r.get("error") or "-"))
    if not rows:
        print("no tickets claimed")
    else:
        header = ("TICKET", "STATE", "CLAIM", "THREAD", "TOKENS", "LAST EVENT")
        widths = [max(len(str(x[i])) for x in (header, *rows)) for i in range(5)]
        for row in (header, *rows):
            print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)) + "  " + str(row[5]))
    totals = st.get("codex_totals") or {}
    print(f"tokens total {totals.get('total_tokens', 0)}, "
          f"running {totals.get('seconds_running', 0)}s")
    return 0


# -- open / logs ------------------------------------------------------------------------------

def cmd_open(ticket: str, path: Path, *, rex=None) -> int:
    rex = rex or rex_mod.run_rex
    try:
        cfg = _load_config(path)
    except (OSError, SymphonyError) as e:
        _err(str(e))
        return 1
    threads = ThreadStore(cfg.workspace_root)
    rec = threads.read(ticket)
    if rec is None:
        _err(f"no Codex thread recorded for {ticket}")
        return 1
    cwd = cfg.workspace_root / workspace_key(ticket)
    store = threads.dir_for(ticket)
    try:
        rex_mod.ensure_tab(ticket, cwd if cwd.is_dir() else cfg.workspace_root, rec.thread_id,
                           session_label=cfg.ext.rex_session, store_dir=store, rex=rex)
        rex_mod.show_tab(ticket, session_label=cfg.ext.rex_session, store_dir=store, rex=rex)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
        _err(str(e))
        return 1
    print(f"{ticket}: showing tab in Rex session {cfg.ext.rex_session} "
          f"(thread {rec.thread_id})")
    return 0


def cmd_logs(ticket: str, n: int) -> int:
    path = log_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        _err(f"no log at {path}")
        return 1
    hits = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and str(rec.get("issue_identifier", "")).upper() == ticket:
            hits.append(line)
    for line in hits[-n:] if n > 0 else hits:
        print(line)
    return 0


# -- login / bridge-event ---------------------------------------------------------------------

def cmd_login(*, port: int, login=None) -> int:
    from . import mcp_linear

    try:
        kwargs = {"port": port}
        if login is None:  # real run: print at once and accept a pasted redirect on a terminal
            kwargs["out"] = lambda s: print(s, flush=True)
            if sys.stdin.isatty():
                kwargs["paste_input"] = input
        tools = (login or mcp_linear.login)(**kwargs)
    except SymphonyError as e:
        _err(f"login failed: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 - OAuth and network errors; never print tokens
        _err(f"login failed: {type(e).__name__}")
        return 1
    print(f"logged in to Linear MCP; token stored in Keychain service "
          f"{mcp_linear.KEYCHAIN_SERVICE}")
    print(f"{len(tools)} tools: {', '.join(sorted(tools))}")
    return 0


def cmd_bridge_event(issue: str, *, tools=None, stdin=None) -> int:
    """Read one JSON event on stdin; print a JSON result on stdout."""
    from . import linear, mcp_linear

    def fail(code: int, category: str, message: str) -> int:
        print(json.dumps({"error": category}))
        _err(message)
        return code

    try:
        event = json.loads((stdin or sys.stdin).read())
    except ValueError:
        return fail(BRIDGE_EXIT_INPUT, "bad_event", "stdin is not JSON")
    session = None
    if tools is None:
        tools = session = mcp_linear.service_session()
    try:
        result = linear.bridge_event(tools, issue, event)
    except ValueError as e:
        return fail(BRIDGE_EXIT_INPUT, "bad_event", str(e))
    except SymphonyError as e:
        code = BRIDGE_EXIT_LOGIN if e.category == "tracker_auth" else 1
        return fail(code, e.category, str(e))
    finally:
        if session is not None:
            session.close()
    print(json.dumps(result))
    return 0


# -- main -------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="symphony", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check"):
        s = sub.add_parser(name)
        s.add_argument("path", nargs="?")
        s.add_argument("--workflow", dest="workflow")
    sub.add_parser("ls")
    o = sub.add_parser("open")
    o.add_argument("ticket")
    o.add_argument("--workflow")
    lg = sub.add_parser("logs")
    lg.add_argument("ticket")
    lg.add_argument("-n", type=int, default=50, help="last N lines (0 = all)")
    li = sub.add_parser("login")
    li.add_argument("--port", type=int, default=None,
                    help="localhost redirect port (default 47615)")
    be = sub.add_parser("bridge-event")
    be.add_argument("--issue", required=True, help="Linear issue identifier, e.g. TAL-171")
    be.add_argument("--json", default="-", choices=["-"], help="event source (stdin)")
    return p


def main(argv: list[str] | None = None, *, stop: threading.Event | None = None,
         tracker_factory=None, worker_factory=None, notifier=None, rex=None, login=None,
         tools=None, stdin=None) -> int:
    """Keyword arguments are test seams; production passes none."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["run", *argv]
    args = _parser().parse_args(argv)
    factories = {"tracker": tracker_factory, "worker": worker_factory, "notifier": notifier}
    if args.cmd in ("run", "check"):
        if args.path and args.workflow:
            _err("give the workflow path once (positional or --workflow)")
            return 2
        path = _workflow_path(args.path or args.workflow)
        if args.cmd == "run":
            return cmd_run(path, stop=stop, factories=factories)
        return cmd_check(path, factories=factories)
    if args.cmd == "ls":
        return cmd_ls()
    if args.cmd == "login":
        from .mcp_linear import REDIRECT_PORT

        return cmd_login(port=args.port or REDIRECT_PORT, login=login)
    if args.cmd == "bridge-event":
        return cmd_bridge_event(args.issue.upper(), tools=tools, stdin=stdin)
    if args.cmd == "open":
        return cmd_open(args.ticket.upper(), _workflow_path(args.workflow, from_status=True),
                        rex=rex)
    return cmd_logs(args.ticket.upper(), args.n)


if __name__ == "__main__":
    sys.exit(main())
