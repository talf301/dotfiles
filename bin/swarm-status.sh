#!/usr/bin/env bash
# swarm-status.sh — one-glance view of the IAR swarm. Read-only; mutates nothing.
#
# The Gas City "dashboard" at solo-watch scale: what agents are doing right now,
# what is waiting on YOU, and any stale (crashed-worker) claims.
#
#   swarm-status.sh          # print the snapshot
#
# Requires: br, jq.  bash 3.2-safe (stock macOS).
set -euo pipefail
export BEADS_DIR="${BEADS_DIR:-/Users/tal/.local/share/beads/precog/.beads}"

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }
rows() {  # $1 = jq filter producing "id\ttitle\tassignee" lines; reads br json on stdin
  jq -r "$1" 2>/dev/null | while IFS=$'\t' read -r id title who; do
    [ -z "$id" ] && continue
    printf '  %-14s %s\033[2m%s\033[0m\n' "$id" "$title" "${who:+  ($who)}"
  done
}

section "In flight (in-progress beads — owner in parens)"
br list --status in_progress --json 2>/dev/null \
  | rows '.issues[] | [.id, .title, .assignee] | @tsv' \
  | grep . || echo "  (none)"

section "Waiting on YOU (escalated — needs-human, latest comment shown)"
nh=$(br list -l needs-human --status open --status in_progress --json 2>/dev/null | jq -r '.issues[].id')
if [ -z "$nh" ]; then
  echo "  (none)"
else
  for id in $nh; do
    printf '  %-14s %s\n' "$id" "$(br list --id "$id" --json 2>/dev/null | jq -r '.issues[0].title')"
    br comments "$id" --json 2>/dev/null | jq -r '.[-1] // empty | "      ↳ [\(.author)] \(.text)"' 2>/dev/null
  done
fi

section "Stale claims (possible crashed workers)"
br scheduler --format json 2>/dev/null \
  | jq -r '.recommendations[].evidence.stale_claim
           | select(.recommended_action=="reclaim")
           | "  \(.assignee)  (idle \(.updated_age_minutes)m) — br update <id> --assignee \"\" --status open"' \
  | grep . || echo "  (none)"
echo
