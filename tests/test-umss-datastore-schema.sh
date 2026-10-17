#!/usr/bin/env bash
#
# Guards the DataStore schema step in `ckan-docker/bin/test-umss`.
#
# Run from anywhere:  bash tests/test-umss-datastore-schema.sh
#
# `ckan-docker/postgresql/docker-entrypoint-initdb.d/30_setup_test_databases.sh`
# creates a BARE `datastore_test`: `CREATE DATABASE ... OWNER "$CKAN_DB_USER"`
# and nothing else. CKAN's `set-permissions` objects are not in it, so on a fresh
# stack every `datastore_create` in the suite fails with
#
#   function populate_full_text_trigger() does not exist
#
# and `datastore_info` fails with
#
#   relation "_table_metadata" does not exist
#
# (measured 2026-10-17). Until now the fix was done once, by hand, by running the
# `ckan datastore set-permissions` SQL as superuser against `datastore_test`.
# The runner now does that itself, and this file is what keeps it doing it.
#
# What is enforced:
#
#   1. **It is in the remote payload** of `bin/test-umss` -- the part that runs
#      inside `ckan-dev`, not on the host -- and it sits after the `_test` URL
#      guard, after the `export`s of the derived test URLs, and before `pytest`.
#      A step that ran before the guard could be pointed at the dev database.
#   2. **It is conditional.** The Python probes `datastore_test` for
#      `populate_full_text_trigger` (in `pg_proc`) and `_table_metadata` (in
#      `pg_views`) and only then applies anything. That decision is exercised
#      here by importing the SAME Python source the payload runs -- extracted
#      from `bin/test-umss` at run time, never copied into this file -- and
#      calling `missing_objects` with a fake cursor. A repeated run has to stay
#      silent and fast.
#   3. **The SQL is CKAN's own.** The payload calls
#      `ckanext.datastore.cli.permissions_sql` and carries no SQL of its own: no
#      `CREATE OR REPLACE` anywhere in the payload. A copy kept in this repo
#      would drift from the CKAN version in the image.
#   4. **The target is checked before anything is applied.** `permissions_sql`
#      emits `\connect "<db>"` meta-commands, which psql would run and psycopg2
#      cannot, so they are stripped -- but only after every `\connect` has been
#      shown to name exactly the database of the write URL, and that name is
#      shown to end in `_test`. `connect_problem` is exercised against
#      must-fail fixtures: a `\connect` to `datastore`, a `\connect` to a
#      non-`_test` database, and two `\connect`s to different databases.
#   5. **The payload still holds together as a `bash -lc` argument.** It is one
#      single-quoted shell string, so a single quote anywhere in it ends the
#      payload early. `bash -n` cannot see that. Enforced as: the payload region
#      carries no `'` at all.
#   6. **The database that was actually OPENED is the one that gets the schema.**
#      The name checked in 4 comes from the URL path, and the path is not what
#      the driver opens. Two spellings, two layers: psycopg2 accepts
#      `?dbname=<other>` and opens that database (measured live on this stack,
#      psycopg2 2.9.12, 2026-10-17), and SQLAlchemy's `create_connect_args()` --
#      which is what `target_guard.py` reads -- resolves `?database=<other>` the
#      same way. So `main()` runs `select current_database()` as its very first
#      statement, and refuses -- with nothing else executed -- unless that name
#      is the expected one, ends in `_test`, and is the one every `\connect`
#      names. This is exercised here by running the real `main()` against a stub
#      `psycopg2` and a stub `permissions_sql`, placed first on the path for that
#      run only.
#
# What this guard CANNOT see, stated rather than implied:
#
#   - **The real database.** Nothing here connects to Postgres. That the grants
#     succeed as the owning role (`$CKAN_DB_USER`, the role in the write URL) is
#     a fact about the live stack, proved by the live run, not by this file.
#   - **psycopg2 itself.** The stub EMULATES the one behaviour that matters
#     here -- a `database`/`dbname` parameter in the URL wins over the path --
#     because that is the failure this step guards against. Both spellings are
#     stubbed on purpose: `?dbname=` is the one real psycopg2 2.9.12 accepts
#     (measured live on 2026-10-17), while `?database=` is the one SQLAlchemy's
#     `create_connect_args()` resolves. That the step really refuses on the real
#     driver, and that `select current_database()` really answers with the
#     connection it opened, is proved by the live run.
#   - **`?host=`.** A query string can also point the connection at another
#     server whose database happens to carry the same name. `current_database()`
#     cannot see the server, and the payload has no second source of truth for
#     the host, so this is a residual risk, not a checked property.
#   - **Multi-statement execution.** The payload hands the stripped SQL to
#     psycopg2 in one `execute`, in one transaction. Whether that is accepted is
#     a property of psycopg2 and of the SQL text, not of this file.
#   - **Ordering by content, not by control flow.** The ordering check compares
#     line numbers of three anchors in the file. A step that were moved inside an
#     `if false; then ... fi` block would still be "after the guard" here.
#   - **The `_test` suffix of the write URL itself** stays where it was: the
#     payload's own guard already refuses any URL whose last path segment is not
#     `*_test`, and this file does not re-derive it.
#
# Must-pass fixtures come first in every block: a checker that refuses
# everything would make the must-fail rows green for the wrong reason.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SUBJECT="${REPO_ROOT}/ckan-docker/bin/test-umss"

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }
assert_eq() { # label expected actual
	if [ "$2" = "$3" ]; then
		pass "$1"
	else
		fail "$1 (expected [$2], got [$3])"
	fi
}

if [ ! -f "$SUBJECT" ]; then
	echo "test-umss-datastore-schema: missing subject: $SUBJECT" >&2
	exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The remote payload: everything between the `bash -lc` opener and its closing
# `_ "$@"` line. Printed without those two lines.
payload() {
	awk '
		!started && /bash -lc/ { started = 1; next }
		!started { next }
		index($0, "_ \"$@\"") > 0 { exit }
		{ print }
	' "$SUBJECT"
}

# The body of the DataStore step's Python heredoc inside the payload, byte for
# byte. The payload holds more than one heredoc (the Solr core one comes first),
# so blocks are selected by CONTENT -- the one that probes
# `populate_full_text_trigger` -- and not by position. If no block matches, the
# output is empty and the checks below fail loudly instead of importing a
# different program (measured: importing the Solr block hangs on a urlopen).
# The opener is recognised by its QUOTED delimiter;
# `tests/test-heredoc-quoting.sh` enforces the quoting itself, this file only
# relies on it.
extract_python() {
	awk '
		!in_body && index($0, "<<\"PY\"") > 0 { in_body = 1; n = 0; delete buf; next }
		in_body && $0 == "PY" {
			in_body = 0
			hit = 0
			for (i = 1; i <= n; i++)
				if (index(buf[i], "populate_full_text_trigger") > 0) hit = 1
			if (hit) for (i = 1; i <= n; i++) print buf[i]
			next
		}
		in_body { buf[++n] = $0 }
	' "$SUBJECT"
}

payload > "$WORK/payload.txt"
extract_python > "$WORK/payload.py"

echo "the step is in the remote payload"
if [ -s "$WORK/payload.txt" ]; then
	pass "the remote payload was found in bin/test-umss"
else
	fail "the remote payload was not found: no bash -lc ... _ \"\$@\" region"
fi
if grep -qF -- "populate_full_text_trigger" "$WORK/payload.txt"; then
	pass "the payload probes populate_full_text_trigger (pg_proc)"
else
	fail "the payload does not look for populate_full_text_trigger"
fi
if grep -qF -- "_table_metadata" "$WORK/payload.txt"; then
	pass "the payload probes _table_metadata (pg_views)"
else
	fail "the payload does not look for _table_metadata"
fi

echo
echo "the payload is a shell script bash can parse"
# The payload is exactly what `bash -lc` runs inside ckan-dev, so it is a shell
# script and `bash -n` applies to it. This catches a broken heredoc or an
# unbalanced quote without Docker. What it does NOT catch, measured on
# 2026-10-17: a comment continuation line that lost its `#` becomes a valid
# command (`alcanza: ...`), parses fine, and only fails at run time. No static
# check separates that from code, so the live run is the check for that class.
payload > "$WORK/payload-check.sh"
if bash -n "$WORK/payload-check.sh" 2> "$WORK/payload-check.err"; then
	pass "bash -n accepts the payload text"
else
	fail "bash -n rejects the payload text: $(head -n 3 "$WORK/payload-check.err" | tr '\n' ' ')"
fi

echo
echo "the payload carries no single quote"
# The payload is one single-quoted shell string. A `'` anywhere in it -- code,
# comment or SQL copy -- ends that string early. `bash -n` does not see it.
if grep -qF -- "'" "$WORK/payload.txt"; then
	fail "the payload contains a single quote"
	while IFS= read -r line; do
		printf '        %s\n' "$line"
	done < <(grep -nF -- "'" "$WORK/payload.txt" | head -n 5)
else
	pass "the payload holds no single quote"
fi

echo
echo "the SQL is CKAN's, not a copy"
if grep -qF -- "permissions_sql" "$WORK/payload.txt"; then
	pass "the payload calls permissions_sql"
else
	fail "the payload does not call ckanext.datastore.cli.permissions_sql"
fi
if grep -qF -- "CREATE OR REPLACE" "$WORK/payload.txt"; then
	fail "the payload carries SQL of its own (CREATE OR REPLACE)"
else
	pass "the payload carries no SQL of its own"
fi

echo
echo "the step sits after the guard and the exports, and before pytest"
line_of() {
	grep -nF -m1 -- "$1" "$SUBJECT" | cut -d: -f1
}
guard_line="$(line_of "me niego a correr")"
export_line="$(line_of "export CKAN_DATASTORE_WRITE_URL=")"
step_line="$(line_of 'python - "$SQL" "$DSW" "$DSR" <<"PY"')"
pytest_line="$(line_of "python -m pytest")"
if [ -z "$guard_line" ] || [ -z "$export_line" ] || [ -z "$step_line" ] ||
	[ -z "$pytest_line" ]; then
	fail "an ordering anchor is missing: guard=[$guard_line] exports=[$export_line] step=[$step_line] pytest=[$pytest_line]"
else
	if [ "$step_line" -gt "$guard_line" ]; then
		pass "the step is after the _test URL guard (${guard_line} -> ${step_line})"
	else
		fail "the step (${step_line}) is not after the guard (${guard_line})"
	fi
	if [ "$step_line" -gt "$export_line" ]; then
		pass "the step is after the test URL exports (${export_line} -> ${step_line})"
	else
		fail "the step (${step_line}) is not after the exports (${export_line})"
	fi
	if [ "$step_line" -lt "$pytest_line" ]; then
		pass "the step is before pytest (${step_line} -> ${pytest_line})"
	else
		fail "the step (${step_line}) is not before pytest (${pytest_line})"
	fi
fi

# ── The same Python the payload runs, on the host ─────────────────────────────
#
# The two imports that only exist inside the container (`psycopg2`, and CKAN
# itself) live inside `main()`, so this module can be loaded on a host with no
# CKAN. Everything below calls the extracted source; nothing here re-implements
# it.

if [ ! -s "$WORK/payload.py" ]; then
	fail "the payload's Python could not be extracted"
fi

if ! command -v python3 > /dev/null 2>&1; then
	fail "python3 is not on PATH: the parsing logic cannot be exercised"
fi

cat > "$WORK/driver.py" <<'DRIVER'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("umss_payload", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
what = sys.argv[2]


class FakeCursor(object):
    """Only what `missing_objects` uses: one probe at a time."""

    def __init__(self, present):
        self.present = present
        self.last = None
        self.probes = 0

    def execute(self, query, params):
        self.last = params[0]
        self.probes += 1

    def fetchone(self):
        return (1 if self.last in self.present else 0,)


if what == "connect_problem":
    with open(sys.argv[3]) as fp:
        sql = fp.read()
    problem = mod.connect_problem(sql, sys.argv[4])
    print("ok" if problem is None else "refuse")
elif what == "strip_meta":
    with open(sys.argv[3]) as fp:
        sql = fp.read()
    out = mod.strip_meta(sql)
    left = [line for line in out.splitlines() if line.strip().startswith("\\")]
    if left:
        print("dirty:" + "|".join(left))
    else:
        print("clean:%d" % len([line for line in out.splitlines() if line.strip()]))
elif what == "missing_objects":
    present = set(name for name in sys.argv[3].split(",") if name)
    print(",".join(mod.missing_objects(FakeCursor(present))))
elif what == "main":
    print("exit:%d" % mod.main(sys.argv[3:]))
else:
    print("unknown-mode:" + what)
DRIVER

logic() { # what args...
	python3 "$WORK/driver.py" "$WORK/payload.py" "$@"
}

echo
echo "the step is conditional: the probe decides"
assert_eq "both objects present: nothing is missing" \
	"" "$(logic missing_objects "populate_full_text_trigger,_table_metadata")"
assert_eq "only the view present: the trigger is missing" \
	"populate_full_text_trigger" "$(logic missing_objects "_table_metadata")"
assert_eq "only the trigger present: the view is missing" \
	"_table_metadata" "$(logic missing_objects "populate_full_text_trigger")"
assert_eq "nothing present: both are missing" \
	"populate_full_text_trigger,_table_metadata" "$(logic missing_objects "")"

echo
echo "the \\connect target is checked before anything is applied"

# SQL on stdin. Must-pass first.
expect_decision() { # label expected target
	cat > "$WORK/sql"
	local got
	got="$(logic connect_problem "$WORK/sql" "$3")"
	assert_eq "$1" "$2" "$got"
}

expect_decision "accepts the single \\connect CKAN emits" ok datastore_test <<'EOF'
/*
This script configures the permissions for the datastore.
    ckan -c /etc/ckan/default/ckan.ini datastore set-permissions | sudo -u postgres psql
*/
\connect datastore_test

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE datastore_test TO datastore_ro;
EOF

expect_decision "accepts the \\c shorthand for the same target" ok datastore_test <<'EOF'
\c datastore_test
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOF

expect_decision "accepts a quoted target" ok datastore_test <<'EOF'
\connect "datastore_test"
GRANT CONNECT ON DATABASE datastore_test TO datastore_ro;
EOF

expect_decision "refuses a \\connect to datastore" refuse datastore_test <<'EOF'
\connect datastore
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOF

expect_decision "refuses a \\connect to a non-_test database" refuse datastore_test <<'EOF'
\connect datastore_prod
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOF

expect_decision "refuses two \\connects to different databases" refuse datastore_test <<'EOF'
\connect datastore_test
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
\connect ckandb
GRANT CONNECT ON DATABASE ckandb TO datastore_ro;
EOF

expect_decision "refuses a \\connect with no database" refuse datastore_test <<'EOF'
\connect
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOF

expect_decision "refuses SQL that never names its target" refuse datastore_test <<'EOF'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE datastore_test TO datastore_ro;
EOF

echo
echo "the meta-commands are stripped, the statements are kept"
printf '%s\n' '\connect datastore_test' 'REVOKE CREATE ON SCHEMA public FROM PUBLIC;' \
	'GRANT CONNECT ON DATABASE datastore_test TO datastore_ro;' > "$WORK/sql"
assert_eq "no line starting with a backslash survives, both statements do" \
	"clean:2" "$(logic strip_meta "$WORK/sql")"

# ── The real `main()`, against a stub driver ───────────────────────────────────
#
# Everything above exercises the pure functions. What decides which database the
# schema lands in is `main()`: it is the only place that sees the connection. The
# name it verifies comes from the URL PATH, and the path is not what the driver
# opens -- measured in this repository
# (`src/ckanext-umss/ckanext/umss/tests/target_guard.py:15-18`), psycopg2 opens
# `.../datastore_test?database=ckandb` as `ckandb`. So these rows run the real
# `main()` with a stub `psycopg2` and a stub `permissions_sql` first on the path
# for that run only, and assert on the exact sequence of statements it was given.
#
# The stub EMULATES one libpq behaviour -- a `database`/`dbname` query parameter
# wins over the path, and spelling both differently is an error -- because that is
# the behaviour this step exists to survive. That psycopg2 really does it, and
# that `select current_database()` really answers with the connection it opened,
# is what the live run proves; this file cannot.

echo
echo "the database the driver actually opens is the one that is verified"

STUBS="$WORK/stubs"
mkdir -p "$STUBS/ckanext/datastore"
: > "$STUBS/ckanext/__init__.py"
: > "$STUBS/ckanext/datastore/__init__.py"

cat > "$STUBS/psycopg2.py" <<'STUB'
"""Stand-in for psycopg2 that records what the payload asks it to do.

The emulated behaviour is the one this step guards against: a `database` or
`dbname` parameter in the URL query wins over the path segment, so
`.../datastore_test?dbname=datastore` opens `datastore`. Real psycopg2 2.9.12
accepts the `dbname` spelling and rejects `database` as an unknown parameter;
SQLAlchemy's `create_connect_args()` resolves `database`. Both are stubbed: either
layer can be the one that resolves the URL. Spelling both, differently, is an
error, as it is for psycopg2.
"""

import os
from urllib.parse import parse_qs, urlsplit

LOG = os.environ["STUB_LOG"]


def _log(record):
    with open(LOG, "a") as fp:
        fp.write(record + "\n")


def _opened_database(url):
    query = parse_qs(urlsplit(url).query)
    spelled = set(query[key][0] for key in ("database", "dbname") if key in query)
    if len(spelled) > 1:
        raise ValueError("both database and dbname spelled, differently")
    if spelled:
        return spelled.pop()
    return os.environ.get("STUB_CURRENT_DB") or urlsplit(url).path.lstrip("/")


class Cursor(object):
    def __init__(self, conn):
        self.conn = conn
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if text == "select current_database()":
            _log("exec|current_database")
            self.row = (self.conn.database,)
        elif "pg_proc" in text:
            _log("exec|probe populate_full_text_trigger")
            self.row = (self._count("populate_full_text_trigger"),)
        elif "pg_views" in text:
            _log("exec|probe _table_metadata")
            self.row = (self._count("_table_metadata"),)
        else:
            lines = [line.strip() for line in sql.splitlines() if line.strip()]
            meta = len([line for line in lines if line.startswith("\\")])
            _log("exec|apply meta=%d statements=%d" % (meta, len(lines) - meta))

    def _count(self, name):
        return 1 if name in (os.environ.get("STUB_PRESENT") or "").split(",") else 0

    def fetchone(self):
        return self.row


class Connection(object):
    def __init__(self, url):
        self.database = _opened_database(url)
        _log("connect|" + url)

    def cursor(self):
        return Cursor(self)

    def commit(self):
        _log("commit")

    def close(self):
        _log("close")


def connect(url):
    return Connection(url)
STUB

cat > "$STUBS/ckanext/datastore/cli.py" <<'STUB'
"""Stand-in for `ckanext.datastore.cli.permissions_sql`."""

import os

LOG = os.environ["STUB_LOG"]


def permissions_sql(maindb, datastoredb, mainuser, writeuser, readuser):
    with open(LOG, "a") as fp:
        fp.write("perms|" + "|".join(
            (maindb, datastoredb, mainuser, writeuser, readuser)) + "\n")
    with open(os.environ["STUB_SQL_TEMPLATE"]) as fp:
        template = fp.read()
    for key, value in (
        ("{maindb}", maindb),
        ("{datastoredb}", datastoredb),
        ("{mainuser}", mainuser),
        ("{writeuser}", writeuser),
        ("{readuser}", readuser),
    ):
        template = template.replace(key, value)
    return template
STUB

STUB_LOG="$WORK/stub.log"
SQL_TEMPLATE="$WORK/permissions.sql"
# Fixture credentials only. Nothing here reads `.env`; the shapes match the
# container's URLs and the refusal must never print any of it.
PG_PW="fixture-pw"
URL_SQL="postgresql://ckandbuser:${PG_PW}@db/ckan_test"
URL_WRITE="postgresql://ckandbuser:${PG_PW}@db/datastore_test"
URL_READ="postgresql://datastore_ro:${PG_PW}@db/datastore_test"

# The five names CKAN's template is rendered with, cut down to what the stub
# needs: one meta-command to strip and one statement to keep.
write_template() { cat > "$SQL_TEMPLATE"; }
write_template <<'EOF'
\connect {datastoredb}
GRANT CONNECT ON DATABASE {datastoredb} TO {readuser};
EOF

assert_contains() { # label haystack needle
	case "$2" in
	*"$3"*) pass "$1" ;;
	*) fail "$1 (missing [$3] in [$(printf '%s' "$2" | tail -n 3)]]" ;;
	esac
}
assert_no_password() { # label output
	case "$2" in
	*"$PG_PW"*) fail "$1: the refusal printed the URL, password included" ;;
	*) pass "$1: the refusal prints names, never the URL" ;;
	esac
}

# Runs the payload's real main() with the stubs first on the path. CURRENT_DB and
# PRESENT are the fixture knobs; $1 is the write URL handed to main.
run_main() {
	: > "$STUB_LOG"
	OUT="$(PYTHONPATH="$STUBS${PYTHONPATH:+:$PYTHONPATH}" \
		STUB_LOG="$STUB_LOG" STUB_SQL_TEMPLATE="$SQL_TEMPLATE" \
		STUB_CURRENT_DB="${CURRENT_DB-}" STUB_PRESENT="${PRESENT-}" \
		python3 "$WORK/driver.py" "$WORK/payload.py" main \
		"$URL_SQL" "$1" "$URL_READ" 2>&1)"
	STATUS=$?
	LOG="$(cat "$STUB_LOG")"
}

expect_log() { # label line...
	local label="$1"
	shift
	assert_eq "$label" "$(printf '%s\n' "$@")" "$LOG"
}

# Must-pass first: a `main()` that refuses everything would make every must-fail
# row below green for the wrong reason.
CURRENT_DB=datastore_test PRESENT= run_main "$URL_WRITE"
assert_eq "both missing: main succeeds" "exit:0" "$(printf '%s\n' "$OUT" | tail -n 1)"
expect_log "both missing: current_database first, then the probes, then one apply in one transaction" \
	"connect|$URL_WRITE" \
	"exec|current_database" \
	"exec|probe populate_full_text_trigger" \
	"exec|probe _table_metadata" \
	"perms|ckan_test|datastore_test|ckandbuser|ckandbuser|datastore_ro" \
	"exec|apply meta=0 statements=1" \
	"commit" \
	"close"
assert_contains "both missing: it says so in one line" "$OUT" \
	"faltaba el esquema del DataStore en datastore_test"

# The finding this block exists for: the path says datastore_test, the driver
# opened something else. Nothing may run after the query that reveals it.
CURRENT_DB=ckandb PRESENT=populate_full_text_trigger,_table_metadata run_main "$URL_WRITE"
assert_eq "the driver opened ckandb: main fails" "exit:1" "$(printf '%s\n' "$OUT" | tail -n 1)"
expect_log "the driver opened ckandb: nothing after current_database is executed" \
	"connect|$URL_WRITE" \
	"exec|current_database" \
	"close"
assert_contains "the driver opened ckandb: it names the database it got" "$OUT" "ckandb"
assert_no_password "the driver opened ckandb" "$OUT"

CURRENT_DB= PRESENT=populate_full_text_trigger,_table_metadata run_main "$URL_WRITE?database=ckandb"
assert_eq "?database=ckandb: main fails" "exit:1" "$(printf '%s\n' "$OUT" | tail -n 1)"
expect_log "?database=ckandb: nothing after current_database is executed" \
	"connect|$URL_WRITE?database=ckandb" \
	"exec|current_database" \
	"close"
assert_contains "?database=ckandb: it names the database it got" "$OUT" "ckandb"
assert_no_password "?database=ckandb" "$OUT"

CURRENT_DB= PRESENT=populate_full_text_trigger,_table_metadata run_main "$URL_WRITE?dbname=ckandb"
assert_eq "?dbname=ckandb: main fails" "exit:1" "$(printf '%s\n' "$OUT" | tail -n 1)"
expect_log "?dbname=ckandb: nothing after current_database is executed" \
	"connect|$URL_WRITE?dbname=ckandb" \
	"exec|current_database" \
	"close"

# Conditional: both objects there means no SQL is asked for and nothing is said.
CURRENT_DB=datastore_test PRESENT=populate_full_text_trigger,_table_metadata run_main "$URL_WRITE"
assert_eq "both present: main succeeds" "exit:0" "$(printf '%s\n' "$OUT" | tail -n 1)"
expect_log "both present: no permissions SQL, no apply, no commit" \
	"connect|$URL_WRITE" \
	"exec|current_database" \
	"exec|probe populate_full_text_trigger" \
	"exec|probe _table_metadata" \
	"close"
assert_eq "both present: it prints nothing at all" "exit:0" "$OUT"

# The `\connect` check is consulted by main(), not merely available to it.
write_template <<'EOF'
\connect ckandb
GRANT CONNECT ON DATABASE ckandb TO {readuser};
EOF
CURRENT_DB=datastore_test PRESENT= run_main "$URL_WRITE"
assert_eq "the SQL targets another database: main fails" "exit:1" \
	"$(printf '%s\n' "$OUT" | tail -n 1)"
expect_log "the SQL targets another database: nothing is applied" \
	"connect|$URL_WRITE" \
	"exec|current_database" \
	"exec|probe populate_full_text_trigger" \
	"exec|probe _table_metadata" \
	"perms|ckan_test|datastore_test|ckandbuser|ckandbuser|datastore_ro" \
	"close"
assert_contains "the SQL targets another database: it says why" "$OUT" "ckandb"

write_template <<'EOF'
\connect {datastoredb}
GRANT CONNECT ON DATABASE {datastoredb} TO {readuser};
EOF

echo
if [ "$failures" -ne 0 ]; then
	printf '\n%d check(s) failed\n' "$failures"
	exit 1
fi
printf '\nall checks passed\n'