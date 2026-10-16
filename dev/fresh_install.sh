#!/usr/bin/env bash
# Fresh-install check (spec §1 criterion 1): create a throwaway site on a dev bench, install the app, check it, drop the site.
#   dev/fresh_install.sh bench15     # site fresh15.localhost
#   dev/fresh_install.sh bench16     # site fresh16.localhost
# The bench already has the app linked; CI does the from-scratch `bench init` + `bench get-app`.
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  bench15) SITE=fresh15.localhost ;;
  bench16) SITE=fresh16.localhost ;;
  *) echo "usage: dev/fresh_install.sh <bench15|bench16>"; exit 2 ;;
esac
BENCH=$1
# The site name is one of the two fixed names above, so this script can only ever drop a site it created itself.
docker compose exec -T "$BENCH" bash -l -s "$SITE" <<'INNER'
set -euo pipefail
SITE=$1
cd ~/frappe-bench
drop() { bench drop-site "$SITE" --force --no-backup --db-root-password 123 >/dev/null 2>&1 || true; }
drop          # a leftover from an earlier failed run of this script
trap drop EXIT
bench new-site "$SITE" --db-root-password 123 --admin-password admin --mariadb-user-host-login-scope='%'
bench --site "$SITE" install-app erpsmith
bench --site "$SITE" list-apps | grep -q erpsmith || { echo "erpsmith missing from list-apps"; exit 1; }
notes=$(bench --site "$SITE" execute frappe.db.count --args '["MCP Memory"]' | tail -1)
[ "$notes" -gt 0 ] || { echo "no seed notes in MCP Memory"; exit 1; }
tools=$(bench --site "$SITE" execute erpsmith.mcp.list_tools | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))')
[ "$tools" -eq 59 ] || { echo "expected 59 tools, got $tools"; exit 1; }
echo "seed notes: $notes, tools: $tools"
INNER
echo "FRESH INSTALL OK $BENCH"
