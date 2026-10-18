#!/usr/bin/env bash
#
# Tests for `worker/start_ckan_worker.sh`.
#
# Run from anywhere:  bash ckan-docker/ckan/tests/test-start-ckan-worker.sh
#
# Unlike the entrypoint.d scripts, this one is EXECUTED: it is PID 1 of the
# `ckan-worker` service, so `set -eu` and `exit` are right, and the tests run it
# as a child process and read its exit status. What they pin:
#
#   1. It refuses to start (status 1, message on stderr) without
#      `CKAN___API_TOKEN__JWT__DECODE__SECRET`. The job payload carries the API token
#      the WEB container minted, and the worker decodes it, so both containers need the
#      same secret from the environment; a worker with a different one fails every job
#      at decode time, which is far harder to trace than a refused start.
#   2. `ckan.plugins` in the ini follows `CKAN__PLUGINS` (the worker must load the same
#      plugins as the web container, or it cannot import the xloader job).
#   3. `SECRET_KEY` is generated only when the ini ships it empty (CKAN refuses to build
#      the app without one), and an existing one is never replaced.
#   4. The last act is `exec ckan -c <ini> jobs worker`: no wrapper process in between,
#      so the container's signals reach the worker.
#
# This file must NOT live in `docker-entrypoint.d/` (see the sibling tests).

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../worker/start_ckan_worker.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
BASE_PATH="$PATH"
mkdir -p "$BIN"

# `ckan` records its argv; `config-tool <ini> key=value` also edits the ini like the
# real one (replace the line when the key exists, append otherwise).
cat > "$BIN/ckan" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${STUB_LOG:-/dev/null}"
if [ "${1:-}" = "config-tool" ]; then
	ini="$2"
	pair="$3"
	key="${pair%%=*}"
	value="${pair#*=}"
	grep -Ev "^${key} *=" "$ini" > "$ini.tmp"
	printf '%s = %s\n' "$key" "$value" >> "$ini.tmp"
	mv "$ini.tmp" "$ini"
fi
exit 0
STUB
chmod +x "$BIN/ckan"

failures=0
ok() { printf '  ok   %s\n' "$1"; }
notok() {
	printf '  FAIL %s\n' "$1"
	failures=$((failures + 1))
}
assert_eq() { # label expected actual
	if [ "$2" = "$3" ]; then
		ok "$1"
	else
		notok "$1 (expected [$2], got [$3])"
	fi
}
assert_contains() { # label haystack needle
	case "$2" in
	*"$3"*) ok "$1" ;;
	*) notok "$1 (missing [$3] in [$(printf '%s' "$2" | head -c 300)])" ;;
	esac
}

# Scenario knobs: ${SECRET-} for the JWT decode secret, INI_SECRET_KEY for the SECRET_KEY
# line the ini ships with.
setup() {
	INI="$WORK/ckan.ini"
	printf 'SECRET_KEY = %s\nckan.plugins = image_view text_view\n' "${INI_SECRET_KEY-}" > "$INI"
	export CKAN_INI="$INI"
	export CKAN__PLUGINS="image_view datastore xloader envvars"
	export CKAN___API_TOKEN__JWT__DECODE__SECRET="${SECRET-jwt-secret}"
	export STUB_LOG="$WORK/ckan.log"
	export PATH="$BIN:$BASE_PATH"
	: > "$STUB_LOG"
}

echo "a worker without the JWT decode secret refuses to start"
SECRET= setup
before="$(cat "$INI")"
out="$(bash "$SCRIPT" 2>&1)"
status=$?
assert_eq "exit status" "1" "$status"
assert_contains "the message names the variable" "$out" "CKAN___API_TOKEN__JWT__DECODE__SECRET"
assert_eq "ckan was never invoked" "" "$(cat "$STUB_LOG")"
assert_eq "the ini is untouched" "$before" "$(cat "$INI")"

echo "a configured worker syncs the plugins and execs the jobs worker"
setup
out="$(bash "$SCRIPT" 2>&1)"
status=$?
assert_eq "exit status" "0" "$status"
assert_contains "ckan.plugins follows CKAN__PLUGINS" "$(cat "$INI")" "ckan.plugins = image_view datastore xloader envvars"
assert_eq "the last ckan call is the jobs worker" "-c $INI jobs worker" "$(tail -n 1 "$STUB_LOG")"

echo "an empty SECRET_KEY is generated"
INI_SECRET_KEY= setup
bash "$SCRIPT" > /dev/null 2>&1
key="$(sed -n 's/^SECRET_KEY = //p' "$INI")"
if [ "${#key}" -ge 32 ]; then ok "SECRET_KEY has $((${#key})) chars"; else notok "SECRET_KEY too short [${#key}]"; fi

echo "an existing SECRET_KEY is kept"
INI_SECRET_KEY="already-set" setup
bash "$SCRIPT" > /dev/null 2>&1
assert_eq "SECRET_KEY unchanged" "SECRET_KEY = already-set" "$(grep '^SECRET_KEY' "$INI")"

echo
if [ "$failures" -eq 0 ]; then
	echo "all tests passed"
	exit 0
fi
echo "$failures test(s) failed"
exit 1
