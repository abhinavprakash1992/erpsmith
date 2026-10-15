#!/usr/bin/env bash
# Run the app's tests on both dev benches.
#   dev/test.sh                            # whole app
#   dev/test.sh erpsmith.tests.test_mcp    # one module
set -euo pipefail
cd "$(dirname "$0")"
tracked=$(git ls-files ../private)  # on its own line: a git error here aborts (set -e) instead of passing the guard
if [ -n "$tracked" ]; then
  echo "private/ holds customer data and must never be tracked: git rm -r --cached private"; exit 1
fi
if grep -rnIiE "edif[y]|kuzaga[n]" --exclude-dir=.git --exclude-dir=.superpowers --exclude-dir=private ..; then
  echo "Customer-specific text found above: remove it before committing."; exit 1
fi
ARGS="--app erpsmith"; [ -n "${1:-}" ] && ARGS="--module $1"
# `bench run-tests` exits 0 on v15 even when tests fail, so judge each bench by what it printed. Run both, then report.
out=$(mktemp); trap 'rm -f "$out"' EXIT
failed=0; summary=""
for v in 15 16; do
  echo "=== Frappe v$v"
  docker compose exec -T "bench$v" bash -lc "cd ~/frappe-bench && bench --site test$v.localhost run-tests $ARGS" 2>&1 | tee "$out" || true
  ran=$(grep -E '^Ran [0-9]+ tests?' "$out" | tail -1 || true)
  if [ -n "$ran" ] && awk '/^Ran [0-9]+ tests?/{r=1; next} r && /^OK/{ok=1} END{exit !ok}' "$out" \
      && ! grep -qE '^(Traceback|ERROR:)|FAILED \(' "$out"; then
    summary="$summary
v$v: OK ($ran)"
  else
    summary="$summary
v$v: FAILED"; failed=1
  fi
done
echo "$summary"
exit $failed
