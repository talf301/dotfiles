#!/usr/bin/env bash
# iar-run-tests.sh — worktree-aware pytest runner for the IAR pipeline.
#
# Fixes precog-iar-infra-friction-wga #1/#2/#4 in one place instead of per-agent instructions:
#   #1 a worktree's own .venv lacks pytest and the shared Bazel/Poetry .venv resolves sc.* to the
#      MAIN checkout — this runs the main-repo venv's python with PYTHONPATH pointed at the
#      worktree's own src dirs so sc.* resolves to the branch under review.
#   #2 never touches bazel, so concurrent agents don't serialize behind one Bazel server.
#   #4 always runs with stdin closed, so a child REPL (bare `python`/`pytest`) can't hang the
#      calling process (e.g. `codex exec`) waiting on stdin.
#
# A treehouse slot has its own .venv (its .pth files resolve sc.* to the slot, and its pins can be
# newer than the main checkout's, e.g. livekit-agents), so that venv wins when present. Its bazel
# swipl dylibs can be dangling, so libswipl comes from the first bazel-bin (worktree, then main
# checkout) whose dylib exists.
#
# Usage:
#   iar-run-tests.sh <worktree-path> [pytest-args...]
set -euo pipefail

WORKTREE="${1:?usage: iar-run-tests.sh <worktree-path> [pytest-args...]}"
shift

MAIN_REPO="${MAIN_REPO:-/Users/tal/Documents/precog}"
PYTHON="$MAIN_REPO/.venv/bin/python"
[ -x "$WORKTREE/.venv/bin/python" ] && PYTHON="$WORKTREE/.venv/bin/python"

[ -x "$PYTHON" ] || { echo "iar-run-tests.sh: no venv python at $PYTHON (bazel run //:py-venv?)" >&2; exit 1; }
[ -d "$WORKTREE" ] || { echo "iar-run-tests.sh: no such worktree: $WORKTREE" >&2; exit 1; }

# Every package's src dir, not just the one the last bug happened to be in — a helper hardcoded to
# a single package would silently resolve other packages' sc.* imports back to the main checkout,
# which is exactly the failure mode this exists to fix.
py_path=""
while IFS= read -r src; do
  py_path="${py_path:+$py_path:}$src"
done < <(find "$WORKTREE" -maxdepth 4 -type d -name src -not -path '*/.venv/*' -not -path '*/node_modules/*')

[ -n "$py_path" ] || { echo "iar-run-tests.sh: no */src dirs found under $WORKTREE" >&2; exit 1; }

# Generated protobuf modules (*_pb2.py) live in bazel-bin virtual-imports dirs, not under src — so
# they otherwise resolve to the MAIN checkout's bazel-bin via the venv's .pth entries. When the
# worktree's protos differ from the main checkout's (e.g. an unmerged enum addition), that stale
# pb2 makes every proto-codec import crash. If the worktree has its own bazel-built protos (e.g.
# `bazel build //fleet/common:py-proto-library`), put them FIRST so the worktree's proto sources
# win. PYTHONPATH precedes site-packages .pth entries, so this shadows cleanly.
while IFS= read -r vimp; do
  py_path="$vimp:$py_path"
done < <(find -L "$WORKTREE/bazel-bin" -type d -path '*/_virtual_imports/*' -not -path '*/external/*' 2>/dev/null)

swipl_lib=""
for d in "$WORKTREE"/bazel-bin/_solib_*/*swipl* "$MAIN_REPO"/bazel-bin/_solib_*/*swipl*; do
  [ -e "$d/libswipl.dylib" ] && { swipl_lib="$d"; break; }
done

cd "$WORKTREE"
exec env PYTHONPATH="$py_path" DYLD_LIBRARY_PATH="$swipl_lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}" \
  "$PYTHON" -m pytest "$@" < /dev/null
