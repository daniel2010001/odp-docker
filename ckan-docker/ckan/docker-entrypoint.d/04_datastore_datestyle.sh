#!/bin/bash
#
# Makes sure the DataStore database has `datestyle = 'ISO, DMY'`.
#
# xloader parses `03/04/2024` day-first (`ckanext.xloader.parse_dates_dayfirst`) and
# Postgres must read the same text the same way, or the two xloader paths disagree
# (Run 5 of `loader-eval.md`). On a NEW volume
# `postgresql/docker-entrypoint-initdb.d/20_create_datastore.sh` sets it, but that
# script only ever runs once, on an empty data directory. This file covers the
# volumes that already exist, and it is idempotent, so new ones pass through it too.
#
# Things this file is careful about:
#
# 1. **`return`, never `exit`.** The entrypoint SOURCES this file (`. "$f"` over
#    `/docker-entrypoint.d/*` in both start scripts), so an `exit` would kill PID 1,
#    and the container is `restart: unless-stopped`. Like `03_upgrade_umss_schema.sh`
#    it is only valid when sourced.
# 2. **Three outcomes, not two.** ok (already set, or set now and verified), STILL
#    WRONG (the ALTER ran and a new connection still sees something else) and COULD
#    NOT BE CHECKED (no URL, no sqlalchemy, no connection). "I could not look" is not
#    "it is wrong". A failure returns 1 and CKAN keeps starting; DMY dates are then
#    the thing to look at, not a crash loop.
# 3. **The verification is a NEW connection.** `ALTER DATABASE ... SET` only applies
#    to sessions that start afterwards, so the same connection would prove nothing.
# 4. **No secret is ever printed.** `CKAN_DATASTORE_WRITE_URL` holds the password
#    and sqlalchemy errors can echo the URL, so the Python below reports the
#    exception CLASS only, never its text.
#
# The write user owns the database (`CREATE DATABASE ... OWNER "$CKAN_DB_USER"`), which
# is what lets it ALTER it. The name comes from `current_database()` and is quoted as
# an identifier, so it is not assumed to be `datastore`.

#: The value that has to be in effect for a new session.
DATASTORE_DATESTYLE="ISO, DMY"

# `show`: exit 0 when a new session sees DATASTORE_DATESTYLE, 1 when it sees something
# else, 2 when it cannot be determined. `alter`: exit 0 when the ALTER committed, 2 when
# it did not. Each call is its own process, hence its own new connection.
datastore_datestyle_py() {
	python3 - "$1" "$DATASTORE_DATESTYLE" <<'PY'
import os
import sys

mode, wanted = sys.argv[1], sys.argv[2]
url = os.environ.get("CKAN_DATASTORE_WRITE_URL")
if not url:
    print("datastore-datestyle: CKAN_DATASTORE_WRITE_URL is not set", file=sys.stderr)
    sys.exit(2)
try:
    from sqlalchemy import create_engine, text
except Exception as exc:  # pragma: no cover - the image always ships sqlalchemy
    print("datastore-datestyle: sqlalchemy is unavailable (%s)" % type(exc).__name__, file=sys.stderr)
    sys.exit(2)
try:
    engine = create_engine(url)
    with engine.connect() as connection:
        if mode == "show":
            current = connection.execute(text("SHOW datestyle")).scalar()
            sys.exit(0 if current == wanted else 1)
        name = connection.execute(text("SELECT current_database()")).scalar()
        quoted = engine.dialect.identifier_preparer.quote(name)
        connection.execute(text("ALTER DATABASE %s SET datestyle TO '%s'" % (quoted, wanted)))
        connection.commit()
except SystemExit:
    raise
except Exception as exc:
    # The class only: the message of a connection error can contain the URL.
    print("datastore-datestyle: database error (%s); details withheld, they can echo the URL"
          % type(exc).__name__, file=sys.stderr)
    sys.exit(2)
sys.exit(0)
PY
}

echo "datastore-datestyle: making sure the DataStore database uses datestyle '$DATASTORE_DATESTYLE'"
datastore_datestyle_py show
case $? in
0)
	echo "datastore-datestyle: datestyle is already $DATASTORE_DATESTYLE, nothing to do"
	return 0
	;;
1) ;;
*)
	echo "datastore-datestyle: datestyle COULD NOT BE CHECKED, so nothing is verified." >&2
	return 1
	;;
esac

if ! datastore_datestyle_py alter; then
	echo "datastore-datestyle: the ALTER failed; datestyle was NOT changed (see the line above)." >&2
	return 1
fi

datastore_datestyle_py show
case $? in
0)
	echo "datastore-datestyle: datestyle is $DATASTORE_DATESTYLE after the ALTER: ok"
	return 0
	;;
1)
	echo "datastore-datestyle: datestyle is STILL WRONG on a new connection after an ALTER that succeeded." >&2
	echo "datastore-datestyle: a role-level setting (ALTER ROLE ... SET datestyle) overrides the database one." >&2
	;;
*)
	echo "datastore-datestyle: the ALTER succeeded but datestyle COULD NOT BE CHECKED afterwards." >&2
	;;
esac
return 1
