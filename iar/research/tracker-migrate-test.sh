#!/usr/bin/env bash
# W23 spike: migrate a br (beads_rust) store to bd (steveyegge/beads, Dolt) and diff every issue.
# Reads the source store only through `sqlite3 .backup` (no writes to it). All work is in a temp dir.
# Usage: tracker-migrate-test.sh [SOURCE_BEADS_DIR]   (default: the Precog store)
# Exit 0 = every non-tombstone issue, field, label, comment and dependency survived; 1 = a diff.
set -euo pipefail
SRC=${1:-/Users/tal/.local/share/beads/precog/.beads}
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
echo "br $(br --version | awk '{print $2}')  bd $(bd --version 2>/dev/null | awk '{print $3}')"

# 1. Snapshot the br store and export fresh JSONL from the snapshot.
mkdir -p "$T/br/.beads"
sqlite3 "$SRC/beads.db" ".backup '$T/br/.beads/beads.db'"
cp "$SRC/metadata.json" "$SRC/config.yaml" "$T/br/.beads/"
BEADS_DIR="$T/br/.beads" br sync --flush-only --force -q >/dev/null 2>&1 || true
BEADS_DIR="$T/br/.beads" br list --all --json --limit 0 >"$T/br-list.json"

# 2. br leaves ephemeral issues out of its JSONL export. Append them from `br show --json`,
#    without the ephemeral flag (see tracker_compare.py show2jsonl).
cp "$T/br/.beads/issues.jsonl" "$T/full.jsonl"
python3 "$HERE/tracker_compare.py" missing "$T/br-list.json" "$T/full.jsonl" >"$T/missing.ids"
while read -r id; do
  [ -n "$id" ] || continue
  BEADS_DIR="$T/br/.beads" br show "$id" --json | python3 "$HERE/tracker_compare.py" show2jsonl >>"$T/full.jsonl"
done <"$T/missing.ids"
echo "issues in br DB but not in br JSONL (ephemeral), added from br show: $(tr '\n' ' ' <"$T/missing.ids")"

# 3. Import into a new embedded-Dolt bd store and export it back.
mkdir -p "$T/bd" && cd "$T/bd"
bd init --non-interactive --stealth -p "$(sed -n 's/^# issue_prefix: //p' "$SRC/config.yaml")" >/dev/null 2>&1
start=$(date +%s)
bd import "$T/full.jsonl" >"$T/import.log" 2>&1
echo "bd import: $(($(date +%s) - start)) s; $(grep -c 'Skipped dependency' "$T/import.log" || true) skipped deps"
bd export -o "$T/bd.jsonl" >/dev/null 2>&1

# 4. Compare per issue.
python3 "$HERE/tracker_compare.py" diff "$T/full.jsonl" "$T/bd.jsonl"
