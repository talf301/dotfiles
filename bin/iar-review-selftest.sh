#!/usr/bin/env bash
# Self-test for iar-review.sh with stubbed codex and br on PATH: a real APPROVED gives exit 0, a real
# CHANGES REQUESTED exit 1, a 429 failure and a run with no verdict line exit 3. The stub echoes the
# prompt (which names every verdict) into the log, like real codex, so a log-scraping parser fails here.
# Usage: iar-review-selftest.sh [path/to/iar-review.sh]   Exits non-zero on the first failure.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
review="${1:-$here/iar-review.sh}"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/stub" "$tmp/wt"
export BEADS_DIR="$tmp/beads" LOG_DIR="$tmp/logs" PATH="$tmp/stub:$PATH"

printf '#!/bin/sh\nexit 0\n' > "$tmp/stub/br"
cat > "$tmp/stub/codex" <<'EOF'
#!/usr/bin/env bash
out=""; prev=""; for a in "$@"; do [ "$prev" = -o ] && out="$a"; prev="$a"; done
prompt="${*: -1}"; echo "user: $prompt"
case "$prompt" in *"picking the tests"*) final="TESTS: none" ;; *) final="$STUB_FINAL" ;; esac
if [ "$STUB_MODE" = 429 ]; then echo "ERROR: unexpected status 429 Too Many Requests" >&2; exit 1; fi
echo "codex: $final"; [ -n "$out" ] && printf '%s\n' "$final" > "$out"
exit 0
EOF
chmod +x "$tmp/stub/br" "$tmp/stub/codex"

check() {  # check <want-exit> <want-in-output> <mode> <final message> <description>
  local rc=0
  STUB_MODE=$3 STUB_FINAL=$4 "$review" b-test "$tmp/wt" HEAD true > "$tmp/out" 2>&1 || rc=$?
  if [ "$rc" != "$1" ] || ! grep -q -- "$2" "$tmp/out"; then
    echo "FAIL  $5: expected exit $1 and '$2', got exit $rc:"; cat "$tmp/out"; exit 1
  fi
  echo "pass  $5 (exit $rc)"
}

check 0 "VERDICT: APPROVED" ok "$(printf 'No findings.\nVERDICT: APPROVED')" "real APPROVED"
check 1 "VERDICT: CHANGES REQUESTED" ok "$(printf 'x.sh:1 bug\nVERDICT: CHANGES REQUESTED')" "real CHANGES REQUESTED"
check 3 "429 Too Many Requests" 429 "" "codex 429 failure"
check 3 "no verdict" ok "Findings but I forgot the verdict line." "no verdict line"
echo "all iar-review checks passed"
