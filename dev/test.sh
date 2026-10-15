#!/usr/bin/env bash
# Run the app's tests on both dev benches.
#   dev/test.sh                            # whole app
#   dev/test.sh erpsmith.tests.test_mcp    # one module
set -euo pipefail
cd "$(dirname "$0")"
if grep -rnIiE "edif[y]|kuzaga[n]" --exclude-dir=.git --exclude-dir=.superpowers ..; then
  echo "Customer-specific text found above: remove it before committing."; exit 1
fi
ARGS="--app erpsmith"; [ -n "${1:-}" ] && ARGS="--module $1"
for v in 15 16; do
  echo "=== Frappe v$v"
  docker compose exec -T "bench$v" bash -lc "cd ~/frappe-bench && bench --site test$v.localhost run-tests $ARGS"
done
