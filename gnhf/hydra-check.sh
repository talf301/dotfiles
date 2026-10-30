#!/bin/bash
# Hydra acceptance check for gnhf runs. Exit 0 only if every gate passes.
# Gates: hydra pytest, pyright 1.1.414, seeded datagen digest == frozen baseline of the run's base commit,
# collected test count >= baseline. Metric: net hydra/ production lines vs the base commit (lower is better).
# usage: hydra-check.sh            check the slot that holds the cwd (gnhf-run wrote build/gnhf/{base,env.sh})
#        hydra-check.sh baseline   freeze the baseline; HEAD must be the base commit with hydra/ clean
set -u
HERE=$(dirname "$(realpath "$0")")
SLOT=$(git rev-parse --show-toplevel) || exit 2
RUN=$SLOT/build/gnhf
BASE_SHA=$(cat "$RUN/base") || exit 2
BASE=$HOME/.local/share/precog/gnhf/baseline-${BASE_SHA:0:12}
SEEDS=160
PROCS=10
DIGEST_FILES=$((SEEDS * 3))  # datagen_digest.py writes one file per seed for each of its 3 configs
cd "$SLOT" || exit 2
source "$RUN/env.sh"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
# benchmarks/harness_test.py times parallel worker processes and flakes under load; it does not cover tau_retail.
PYTEST_ARGS=(hydra -p no:cacheprovider --ignore=hydra/src/sc/hydra/benchmarks/harness_test.py)

count_tests() {
  local n
  n=$("$PY" -m pytest "${PYTEST_ARGS[@]}" -q --collect-only 2>/dev/null | grep -Eo '^[0-9]+ tests? collected' | grep -Eo '^[0-9]+')
  echo "${n:-0}"
}

# Starts the datagen digest in the background, split into PROCS seed slices; wait_digest collects them.
digest_pids=()
start_digest() {  # start_digest <out-dir> <log-dir>
  rm -rf "$1"
  mkdir -p "$1" "$2"
  for p in $(seq 0 $((PROCS - 1))); do
    "$PY" "$HERE/datagen_digest.py" "$1" $(seq $p $PROCS $((SEEDS - 1))) >"$2/digest-$p.log" 2>&1 &
    digest_pids+=($!)
  done
}
wait_digest() {  # returns the number of digest workers that failed
  local failed=0 pid
  for pid in "${digest_pids[@]}"; do wait "$pid" || failed=$((failed + 1)); done
  return $failed
}

if [ "${1:-}" = baseline ]; then
  if [ -f "$BASE/test_count" ]; then
    echo "baseline ${BASE_SHA:0:10} exists: $BASE"
    exit 0
  fi
  if [ "$(git rev-parse HEAD)" != "$BASE_SHA" ] || [ -n "$(git status --porcelain -- hydra)" ]; then
    echo "baseline needs HEAD at $BASE_SHA with a clean hydra/"
    exit 2
  fi
  start=$(date +%s)
  start_digest "$BASE/digest" "$BASE/logs"
  tests=$(count_tests)
  wait_digest
  failed_workers=$?
  files=$(ls "$BASE/digest" | wc -l | tr -d ' ')
  if [ "$failed_workers" -ne 0 ] || [ "$files" -ne "$DIGEST_FILES" ] || [ "$tests" -eq 0 ]; then
    echo "baseline FAILED: failed_workers=$failed_workers files=$files/$DIGEST_FILES tests=$tests (see $BASE/logs/)"
    exit 2
  fi
  # test_count is written last: it marks the baseline as complete.
  echo "$tests" >"$BASE/test_count"
  echo "baseline ${BASE_SHA:0:10} tests=$tests files=$files $(($(date +%s) - start))s: $BASE"
  exit 0
fi

[ -f "$BASE/test_count" ] || { echo "CHECK FAIL [no baseline at $BASE: run hydra-check.sh baseline at the base commit]"; exit 1; }
LOGS=$RUN/logs
rm -rf "$LOGS"
start=$(date +%s)
start_digest "$RUN/digest" "$LOGS"
"$PY" -m pytest "${PYTEST_ARGS[@]}" -q -x >"$LOGS/pytest.log" 2>&1
pytest_rc=$?
tests=$(count_tests)
npx -y pyright@1.1.414 --pythonpath "$SLOT/.venv/bin/python" hydra >"$LOGS/pyright.log" 2>&1
pyright_rc=$?
wait_digest
failed_workers=$?

expected=$DIGEST_FILES
got=$(ls "$RUN/digest" | wc -l | tr -d ' ')
diff -rq "$BASE/digest" "$RUN/digest" >"$LOGS/digest-diff.txt" 2>&1
digest_diffs=$(grep -c . "$LOGS/digest-diff.txt")
first=$(grep -m1 -Eo '[a-z]+_[0-9]{4}\.txt' "$LOGS/digest-diff.txt" | head -1)
[ -n "$first" ] && diff "$BASE/digest/$first" "$RUN/digest/$first" | head -c 4000 >"$LOGS/digest-first-diff.txt"

base_tests=$(cat "$BASE/test_count")
# Net lines split into production code and tests (*_test.py, conftest.py); the metric is production lines.
# A copy of the index with intent-to-add entries counts new untracked files; the slot's own index is untouched.
cp "$(git rev-parse --path-format=absolute --git-path index)" "$RUN/index-for-net-lines"
export GIT_INDEX_FILE=$RUN/index-for-net-lines
git add -N -- hydra 2>/dev/null
read -r src_add src_del test_add test_del < <(git diff --numstat "$BASE_SHA" -- hydra | awk '
  $3 ~ /(_test\.py|conftest\.py)$/ {ta+=$1; td+=$2; next} {sa+=$1; sd+=$2}
  END {print sa+0, sd+0, ta+0, td+0}')
net=$((src_add - src_del))
net_tests=$((test_add - test_del))

fail=()
[ $pytest_rc -ne 0 ] && fail+=("pytest(see $LOGS/pytest.log)")
[ $pyright_rc -ne 0 ] && fail+=("pyright(see $LOGS/pyright.log)")
[ "$failed_workers" -ne 0 ] && fail+=("digest-workers $failed_workers failed(see $LOGS/digest-*.log)")
[ "$got" -ne "$expected" ] && fail+=("digest-files $got/$expected(see $LOGS/digest-*.log)")
[ "$digest_diffs" -ne 0 ] && fail+=("digest-diff $digest_diffs files(see $LOGS/digest-diff.txt, $LOGS/digest-first-diff.txt)")
[ "$tests" -eq 0 ] && fail+=("test-collection(no tests collected)")
[ "$tests" -lt "$base_tests" ] && fail+=("test-count $tests<$base_tests")

secs=$(($(date +%s) - start))
summary="net_lines=$net (+$src_add/-$src_del) net_test_lines=$net_tests (+$test_add/-$test_del) vs ${BASE_SHA:0:10} tests=$tests/$base_tests seeds=$SEEDS ${secs}s"
if [ ${#fail[@]} -eq 0 ]; then
  echo "CHECK PASS $summary"
  exit 0
fi
echo "CHECK FAIL [${fail[*]}] $summary"
exit 1
