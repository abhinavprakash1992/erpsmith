#!/usr/bin/env bash
# ERPNext dev site for the live smoke (Plan 4): gets ERPNext into a bench, creates erp15/erp16.localhost with
# erpnext + erpsmith installed, and a demo company. Idempotent. The unit-test sites (test15/test16) stay Frappe-only.
#   dev/setup-erpnext.sh bench15     # site erp15.localhost, ERPNext version-15
#   dev/setup-erpnext.sh bench16     # site erp16.localhost, ERPNext version-16
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  bench15) SITE=erp15.localhost BRANCH=version-15 NODE=22 ;;
  bench16) SITE=erp16.localhost BRANCH=version-16 NODE=24 ;;
  *) echo "usage: dev/setup-erpnext.sh <bench15|bench16>"; exit 2 ;;
esac
docker compose exec -T "$1" bash -l -s "$SITE" "$BRANCH" "$NODE" <<'INNER'
set -euo pipefail
SITE=$1 BRANCH=$2 NODE=$3
. "$HOME/.nvm/nvm.sh" && nvm use "$NODE" >/dev/null
cd ~/frappe-bench
[ -d apps/erpnext ] || bench get-app erpnext --branch "$BRANCH"
bench --site "$SITE" list-apps >/dev/null 2>&1 || bench new-site "$SITE" --force --db-root-password 123 --admin-password admin --mariadb-user-host-login-scope='%'
installed=$(bench --site "$SITE" list-apps)
echo "$installed" | grep -q erpnext || bench --site "$SITE" install-app erpnext
echo "$installed" | grep -q erpsmith || bench --site "$SITE" install-app erpsmith
bench --site "$SITE" set-config developer_mode 1
bench --site "$SITE" migrate
# ERPNext's own programmatic setup (erpnext.setup.setup_wizard.setup_complete: presets, fiscal year, company, defaults, the same
# code the wizard runs, without its user and sample-data steps). A bare Company insert fails: the presets (Warehouse Type, Item Group,
# Customer Group...) only come from the wizard.
cd sites && "$HOME/frappe-bench/env/bin/python" - "$SITE" <<'PY'
import sys

import frappe
from frappe.utils import getdate, nowdate

frappe.init(site=sys.argv[1], sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
if not frappe.db.exists("Company", "ERPSmith Demo Co"):
	from erpnext.setup.setup_wizard.setup_wizard import setup_complete

	year = getdate(nowdate()).year
	setup_complete(frappe._dict(country="India", currency="INR", company_name="ERPSmith Demo Co", company_abbr="EDC",
		chart_of_accounts="Standard", fy_start_date=f"{year}-01-01", fy_end_date=f"{year}-12-31", language="en", timezone="Asia/Kolkata"))
frappe.db.set_single_value("System Settings", "setup_complete", 1)
frappe.db.commit()
print("COMPANY", frappe.get_all("Company", fields=["name", "abbr", "default_currency"]))
PY
echo "READY $SITE"
INNER
