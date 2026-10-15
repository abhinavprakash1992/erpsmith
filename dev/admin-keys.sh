#!/usr/bin/env bash
# Print API key exports for Administrator on a dev bench.
#   eval "$(dev/admin-keys.sh 15)"
set -euo pipefail
cd "$(dirname "$0")"
v=$1
run() { docker compose exec -T "bench$v" bash -lc "cd ~/frappe-bench && bench --site test$v.localhost execute $1" | tail -1; }
secret=$(run "frappe.core.doctype.user.user.generate_keys --args \"['Administrator']\"" | python3 -c 'import json,sys; print(json.load(sys.stdin)["api_secret"])')
key=$(run "frappe.db.get_value --args \"['User', 'Administrator', 'api_key']\"" | tr -d '"')
echo "export API_KEY=$key API_SECRET=$secret"
