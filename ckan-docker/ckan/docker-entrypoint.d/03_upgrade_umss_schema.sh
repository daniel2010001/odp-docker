#!/bin/bash
#
# Registers this extension's migration in the deployment path.
#
# The migration exists (`ckanext/umss/migration/umss/versions/`) and is written to
# run under `ckan db upgrade -p umss` — its `env.py` keeps its own
# `umss_alembic_version` table so it never touches core's — but until this file
# nothing in the deployment path ever ran it: the only schema step in the
# repository was the extension's CI workflow (`ckan -c test.ini db init`, core
# only), and `migrate_db_for` is a pytest fixture. On a fresh deployment the table
# the five publication actions write to therefore does not exist, and
# `publication_request_list` answers `500`.
#
# Three things this file is careful about. Each one is measured, not assumed:
#
# 1. **No `--skip-core`.** Measured 2026-10-07: with `-p <plugin>`, that flag turns
#    the command into a silent no-op that still prints `SUCCESS`. Anything that
#    trusts the exit status would call an un-migrated database healthy.
# 2. **The check reads the table, not the exit status.** `SUCCESS` is not
#    evidence; `select to_regclass('public.publication_requests')` is. The name is
#    **plural**, and that is load-bearing: the claim that "the dev database has no
#    store table" came from looking up the singular, and two independent sessions
#    took that empty result as mutual confirmation.
# 3. **`return`, never `exit`.** The entrypoint SOURCES this file — `. "$f"` over
#    `/docker-entrypoint.d/*` in both `/srv/app/start_ckan_development.sh` and
#    `/srv/app/start_ckan.sh` — so an `exit` here would kill PID 1. The container
#    is `restart: unless-stopped`, so a failed migration must leave a loud trail
#    instead of becoming a crash loop. This file is therefore only valid when it is
#    sourced, exactly like `01_setup_datapusher.sh`.
#
# Ordering is safe in both paths: the base entrypoint runs the core schema step
# first (`prerun.py` in dev) and sources `/docker-entrypoint.d/*` afterwards.

#: The table the migration creates. Plural, and see the note above.
UMSS_SCHEMA_TABLE="publication_requests"

# 0 when the table exists, 1 when it does not, 2 when it cannot be determined.
# The third case is deliberately distinct from the second and fails closed below:
# "I could not look" is not "it is not there".
umss_schema_table_present() {
	python3 - "$UMSS_SCHEMA_TABLE" <<'PY'
import os
import sys

table = sys.argv[1]
url = os.environ.get("CKAN_SQLALCHEMY_URL")
if not url:
    print("umss-schema: CKAN_SQLALCHEMY_URL is not set, so the table cannot be checked", file=sys.stderr)
    sys.exit(2)
try:
    from sqlalchemy import create_engine, text
except Exception as exc:  # pragma: no cover - the image always ships sqlalchemy
    print("umss-schema: sqlalchemy is unavailable: %s" % exc, file=sys.stderr)
    sys.exit(2)
try:
    # `to_regclass` answers NULL for a name that does not exist, so this is an
    # existence check and not a query that throws.
    with create_engine(url).connect() as connection:
        present = connection.execute(
            text("select to_regclass('public.' || :t)"), {"t": table}
        ).scalar()
except Exception as exc:
    print("umss-schema: could not query the database: %s" % exc, file=sys.stderr)
    sys.exit(2)
sys.exit(0 if present else 1)
PY
}

# `present`, `missing` or `unknown`. The third word exists so that "I could not
# look" is never reported as "it is not there": both fail closed, but they mean
# different things to whoever reads the log.
umss_schema_state() {
	umss_schema_table_present
	case $? in
	0) echo present ;;
	1) echo missing ;;
	*) echo unknown ;;
	esac
}

echo "umss-schema: registering this extension's migration (ckan db upgrade -p umss)"
before="$(umss_schema_state)"
echo "umss-schema: $UMSS_SCHEMA_TABLE before the upgrade: $before"

# `--skip-core` is deliberately absent. See the note at the top of this file.
if ! ckan -c "$CKAN_INI" db upgrade -p umss; then
	echo "umss-schema: 'ckan db upgrade -p umss' exited non-zero; the schema was NOT verified" >&2
	return 1
fi

after="$(umss_schema_state)"
if [ "$after" = present ]; then
	echo "umss-schema: $UMSS_SCHEMA_TABLE is present after the upgrade: ok"
	return 0
fi

if [ "$after" = unknown ]; then
	echo "umss-schema: the upgrade reported success but the table could NOT BE CHECKED, so nothing is verified." >&2
else
	echo "umss-schema: $UMSS_SCHEMA_TABLE is STILL MISSING after an upgrade that reported success." >&2
fi
echo "umss-schema: do not trust that SUCCESS: with '-p <plugin>' plus '--skip-core' the CLI is a silent no-op." >&2
echo "umss-schema: check that the 'umss' plugin resolves here and that the database in CKAN_SQLALCHEMY_URL is the" >&2
echo "umss-schema: one CKAN actually uses — the effective database comes from the environment, not the ini." >&2
echo "umss-schema: re-run by hand with: ckan -c $CKAN_INI db upgrade -p umss" >&2
return 1
