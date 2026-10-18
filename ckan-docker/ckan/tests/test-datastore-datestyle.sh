#!/usr/bin/env bash
#
# Tests for `docker-entrypoint.d/04_datastore_datestyle.sh`.
#
# Run from anywhere:  bash ckan-docker/ckan/tests/test-datastore-datestyle.sh
#
# The script is SOURCED by the entrypoint (`. "$f"` over `/docker-entrypoint.d/*`),
# so it must `return`, never `exit`, and the shell that sourced it must survive.
# It makes sure the DataStore database has `datestyle = 'ISO, DMY'` (xloader's
# dayfirst parsing and Postgres must agree, Run 5) on volumes that already exist,
# where `postgresql/docker-entrypoint-initdb.d/20_create_datastore.sh` never runs.
#
# Properties pinned here:
#   1. Already `ISO, DMY`: no ALTER, and it says so.
#   2. Not set: it ALTERs, then verifies on a NEW connection; ok only if verified.
#   3. Still wrong after the ALTER, or the ALTER fails, or the check cannot be made:
#      three different messages, status 1, and the shell survives.
#   4. No secret is ever printed: CKAN_DATASTORE_WRITE_URL holds the password, and
#      sqlalchemy exceptions can echo the URL. Checked against the stubs AND against
#      the real embedded Python with a fake `sqlalchemy` whose errors echo the URL.
#
# This file must NOT live in `docker-entrypoint.d/` (the entrypoint sources every
# `*.sh` there). No `set -e` on purpose: the harness decides what non-zero means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../docker-entrypoint.d/04_datastore_datestyle.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
mkdir -p "$BIN"

SECRET="s3cr3t-p4ss"
URL="postgresql://datastore_user:${SECRET}@db:5432/datastore"

# A `python3` stand-in. The script calls it as `python3 - <mode>`; it swallows the
# program on stdin and exits with the status the case wants for that mode:
# `show` 0 (is ISO, DMY) / 1 (is not) / 2 (cannot tell), `alter` 0 / 2.
# `show` is called before the ALTER and again to verify, so PY_SHOW is a
# space-separated list consumed one entry per call.
cat > "$BIN/python3" <<'STUB'
#!/usr/bin/env bash
cat > /dev/null
mode="$2"
printf '%s\n' "$mode" >> "$PY_LOG"
case "$mode" in
show)
	n="$(grep -c '^show$' "$PY_LOG")"
	set -- $PY_SHOW
	eval "exit \${$n:-2}"
	;;
alter) exit "${PY_ALTER:-0}" ;;
esac
exit 2
STUB
chmod +x "$BIN/python3"

pass=0
fail=0
check() {
	local label="$1" expected="$2" actual="$3"
	if [ "$expected" = "$actual" ]; then
		pass=$((pass + 1))
		printf 'ok   %s\n' "$label"
	else
		fail=$((fail + 1))
		printf 'FAIL %s\n     expected: %s\n     actual:   %s\n' "$label" "$expected" "$actual"
	fi
}
has() { grep -qF -- "$2" <<<"$1" && echo yes || echo no; }

# Sources the script in a shell of its own with the stubs on PATH; stdout and stderr
# are merged, the last line is the status the script returned (proof of survival).
source_it() {
	: > "$PY_LOG"
	PY_SHOW="$1" PY_ALTER="${2:-0}" CKAN_DATASTORE_WRITE_URL="$URL" PATH="$BIN:$PATH" \
		bash -c '. "$1"; printf "\nsurvived:%s" "$?"' _ "$SCRIPT" 2>&1
}
PY_LOG="$WORK/py.log"
export PY_LOG

# ── 1. Already set: no ALTER ───────────────────────────────────────────────────
out="$(source_it "0")"
check "already set: returns 0" "survived:0" "$(printf '%s' "$out" | tail -n 1)"
check "already set: says so" "yes" "$(has "$out" 'datastore-datestyle: datestyle is already ISO, DMY')"
check "already set: no ALTER" "no" "$(grep -q '^alter$' "$PY_LOG" && echo yes || echo no)"

# ── 2. Set now and verified on a new connection ────────────────────────────────
out="$(source_it "1 0")"
check "set now: returns 0" "survived:0" "$(printf '%s' "$out" | tail -n 1)"
check "set now: one ALTER" "1" "$(grep -c '^alter$' "$PY_LOG")"
check "set now: verified on a second look" "2" "$(grep -c '^show$' "$PY_LOG")"
check "set now: says ok" "yes" "$(has "$out" 'datastore-datestyle: datestyle is ISO, DMY after the ALTER: ok')"

# ── 3. Still wrong after the ALTER ─────────────────────────────────────────────
out="$(source_it "1 1")"
check "still wrong: returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "still wrong: says STILL WRONG" "yes" "$(has "$out" 'STILL WRONG')"
check "still wrong: not reported as could-not-check" "no" "$(has "$out" 'COULD NOT BE CHECKED')"

# ── 4. The ALTER fails ─────────────────────────────────────────────────────────
out="$(source_it "1" 2)"
check "alter fails: returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "alter fails: says the ALTER failed" "yes" "$(has "$out" 'the ALTER failed')"
check "alter fails: no verification attempted" "1" "$(grep -c '^show$' "$PY_LOG")"

# ── 5. Cannot check, before or after ───────────────────────────────────────────
out="$(source_it "2")"
check "cannot check first: returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "cannot check first: says so" "yes" "$(has "$out" 'COULD NOT BE CHECKED')"
check "cannot check first: no ALTER" "no" "$(grep -q '^alter$' "$PY_LOG" && echo yes || echo no)"
out="$(source_it "1 2")"
check "cannot verify: returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "cannot verify: says so, not STILL WRONG" "yes" "$(has "$out" 'COULD NOT BE CHECKED')"
check "cannot verify: not STILL WRONG" "no" "$(has "$out" 'STILL WRONG')"

# ── 6. No secret in any output, whatever the outcome ───────────────────────────
leak=no
for s in "0" "1 0" "1 1" "2" "1 2"; do
	out="$(source_it "$s")"
	grep -qE "$SECRET|datastore_user|postgresql://" <<<"$out" && leak=yes
done
out="$(source_it "1" 2)"
grep -qE "$SECRET|datastore_user|postgresql://" <<<"$out" && leak=yes
check "no URL or password in the output of any outcome" "no" "$leak"

# The real embedded Python, with a fake sqlalchemy whose errors echo the URL.
FAKE="$WORK/fake"
mkdir -p "$FAKE/sqlalchemy"
cat > "$FAKE/sqlalchemy/__init__.py" <<'PY'
def create_engine(url, **kw):
    raise RuntimeError("could not connect to " + url)
def text(s):
    return s
PY
REAL_PY="$(command -v python3)"
mkdir -p "$WORK/realbin"
ln -s "$REAL_PY" "$WORK/realbin/python3"
out="$(CKAN_DATASTORE_WRITE_URL="$URL" PYTHONPATH="$FAKE" PATH="$WORK/realbin:$PATH" \
	bash -c '. "$1"; printf "\nsurvived:%s" "$?"' _ "$SCRIPT" 2>&1)"
check "real python, exception echoes the URL: returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "real python: no URL or password leaks" "no" "$(grep -qE "$SECRET|datastore_user|postgresql://" <<<"$out" && echo yes || echo no)"
check "real python: the exception class is reported" "yes" "$(has "$out" 'RuntimeError')"
check "real python: reported as could-not-check" "yes" "$(has "$out" 'COULD NOT BE CHECKED')"

out="$(env -u CKAN_DATASTORE_WRITE_URL PYTHONPATH="$FAKE" PATH="$WORK/realbin:$PATH" \
	bash -c '. "$1"; printf "\nsurvived:%s" "$?"' _ "$SCRIPT" 2>&1)"
check "URL unset: returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "URL unset: names the variable" "yes" "$(has "$out" 'CKAN_DATASTORE_WRITE_URL is not set')"

# ── 7. Sourced-only script, executable in the image, never prints the variable ──
check "the script is executable" "yes" "$([ -x "$SCRIPT" ] && echo yes || echo no)"
check "it never calls a bare 'exit'" "no" "$(grep -qE '^[[:space:]]*exit([[:space:]]|$)' "$SCRIPT" && echo yes || echo no)"
check "it ALTERs with the quoted identifier of the current database" "yes" "$(grep -q 'ALTER DATABASE' "$SCRIPT" && grep -q 'quote' "$SCRIPT" && echo yes || echo no)"

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
