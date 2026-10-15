#!/usr/bin/env bash
# Run inside a bench container:
#   bash /workspace/erpsmith/dev/setup-bench.sh <frappe-branch> <python> <site> <redis-db> <node>
# Idempotent: re-running skips what already exists.
set -euo pipefail
BRANCH=$1 PY=$2 SITE=$3 RDB=$4 NODE=$5
. "$HOME/.nvm/nvm.sh" && nvm use "$NODE" >/dev/null
git config --global --add safe.directory '*'   # bind mount is owned by the host user
printf '[client]\nskip-ssl\n' > "$HOME/.my.cnf"   # the image's MariaDB 11.x client requires TLS; the dev MariaDB 10.6 has none
cd "$HOME"
[ -d frappe-bench ] || bench init --skip-redis-config-generation --frappe-branch "$BRANCH" --python "$PY" frappe-bench
cd frappe-bench
bench set-config -g db_host mariadb
bench set-config -g redis_cache "redis://redis-cache:6379/$RDB"
bench set-config -g redis_queue "redis://redis-queue:6379/$RDB"
bench set-config -g redis_socketio "redis://redis-queue:6379/$RDB"
ln -sfn /workspace/erpsmith apps/erpsmith
bench pip install -q -e apps/erpsmith
grep -qx erpsmith sites/apps.txt || { sed -i -e '$a\' sites/apps.txt; echo erpsmith >> sites/apps.txt; }   # apps.txt ships without a trailing newline
# A failed earlier run can leave a half-created site behind, so test that the site works, not that its folder exists.
bench --site "$SITE" list-apps >/dev/null 2>&1 || bench new-site "$SITE" --force --db-root-password 123 --admin-password admin --mariadb-user-host-login-scope='%'
bench --site "$SITE" list-apps | grep -q erpsmith || bench --site "$SITE" install-app erpsmith
bench --site "$SITE" set-config allow_tests true
bench --site "$SITE" set-config developer_mode 1
bench --site "$SITE" migrate
echo "READY $SITE"
