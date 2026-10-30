#!/usr/bin/env bash
# treehouse-check.sh — self-test for the treehouse status fixtures that W3/W4/W8 parse.
#
# Usage: treehouse-check.sh [--live] [--capture]
#   (no flag)  check that iar/fixtures/treehouse-status.{txt,json} have the fields parsers use
#   --live     also lease one slot of the precog pool, check status shows it, and return it
#   --capture  rewrite both fixtures from the live precog pool (run after a treehouse upgrade)
set -euo pipefail

REPO="${PRECOG_REPO:-$HOME/Documents/precog}"
FIX="$(cd "$(dirname "$0")" && pwd)/fixtures"
txt="$FIX/treehouse-status.txt"; json="$FIX/treehouse-status.json"

echo "treehouse $(treehouse --version 2>/dev/null)"

if [ "${1:-}" = --capture ]; then
  (cd "$REPO" && treehouse status 2>/dev/null) > "$txt"
  (cd "$REPO" && treehouse status --json 2>/dev/null) | jq . > "$json"
  echo "captured $txt and $json"
fi

# JSON: a top-level array (v2 and v3); every entry has the keys v2.3.0 already emits.
jq -e 'type == "array" and length > 0 and all(.[];
  has("name") and has("path") and has("status") and has("lease_id")
  and has("lease_holder") and has("leased_at") and has("processes"))' "$json" > /dev/null
jq -e 'any(.[]; .status == "leased" and .lease_holder != "")' "$json" > /dev/null
# Text: "<name>  <status>  <path>  (held by <holder>)" per slot.
grep -qE '^[0-9]+ +leased +~?/.*\(held by [^)]+\)' "$txt"
grep -qE '^[0-9]+ +available +~?/' "$txt"
echo "fixtures ok"

if [ "${1:-}" = --live ]; then
  cd "$REPO"
  lease=$(treehouse get --lease --json --no-fetch --lease-holder "agent:treehouse-check" 2>/dev/null)
  path=$(jq -r .path <<< "$lease"); lid=$(jq -r .lease_id <<< "$lease")
  st=$(treehouse status --json 2>/dev/null | jq -r --arg p "$path" '.[] | select(.path == $p) | .status') || st=""
  treehouse return --if-lease-id "$lid" "$path" 2>/dev/null
  # Check that OUR lease is gone, not that the slot is available: another agent may lease it at once.
  after=$(treehouse status --json 2>/dev/null | jq -r --arg p "$path" '.[] | select(.path == $p) | .lease_id')
  [ "$st" = leased ] && [ "$after" != "$lid" ] || { echo "live FAIL: $path status=$st lease after return=$after"; exit 1; }
  echo "live ok: leased and returned $path"
fi
