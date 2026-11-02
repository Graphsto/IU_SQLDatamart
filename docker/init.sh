#!/bin/bash
set -euo pipefail

su_psql()  { psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres "$@"; }
adm_psql() { psql -v ON_ERROR_STOP=1 -U admin_user -d buechertausch "$@"; }

su_psql -f /sql/1createRolesAndUser.sql
su_psql -v admin_pw="$ADMIN_DB_PASSWORD" -v app_pw="$APP_DB_PASSWORD" <<'SQL'
ALTER ROLE admin_user PASSWORD :'admin_pw';
ALTER ROLE app_user   PASSWORD :'app_pw';
SQL
su_psql -f /sql/2createDatabase.sql

for f in 3createEnum 4createTables 5createFunctions; do adm_psql -f "/sql/$f.sql"; done
[ "${LOAD_DUMMY_DATA:-true}" = "true" ] && adm_psql -f /sql/7createDummyData.sql
adm_psql -f /sql/8createViews.sql
adm_psql -f /sql/9grantPrivileges.sql