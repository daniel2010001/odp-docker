#!/usr/bin/env bash
#
# Tests for `docker-entrypoint.d/01_setup_datapusher.sh`.
#
# Run from anywhere:  bash ckan-docker/ckan/tests/test-setup-datapusher.sh
#
# These tests SOURCE the script instead of executing it, because the container
# does exactly that: `/srv/app/start_ckan_development.sh` runs every file in
# `/docker-entrypoint.d` with `. "$f"`, in a shell with neither `-e` nor `-u`.
# Two properties follow from that, and both are what these tests pin:
#
#   1. A failed mint must not take PID 1 down. Only an explicit `exit` inside a
#      sourced script can do that, so the guard has to `return`.
#   2. A failed mint must not leave an unusable token behind. CKAN's DataPusher
#      plugin raises on an empty `ckan.datapusher.api_token`
#      (`ckanext/datapusher/plugin.py:52`), so an empty capture is a crash loop. The
#      base entrypoint writes the placeholder `ckan.datapusher.api_token=xxx`
#      before this file runs (`/srv/app/start_ckan_development.sh:6`), so refusing
#      to write leaves that placeholder: CKAN starts and the datapusher stays
#      broken (403, resources stuck in `pending`) rather than the stack refusing to
#      boot. That trade is deliberate -- `restart: unless-stopped` turns any crash
#      into a loop.
#
# This file must NOT live in `docker-entrypoint.d/`: `Dockerfile.dev` COPYs that
# whole directory into the image and the entrypoint sources every `*.sh` in it,
# so a test placed there would run on every container start.
#
# No `set -e` on purpose: the harness decides what a non-zero status means, and
# the sourced script is written for a shell that does not abort on errors.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../docker-entrypoint.d/01_setup_datapusher.sh"

# Shaped like the real token: three base64url segments. Not a real JWT, and it
# does not need to be -- nothing here verifies a signature.
VALID_TOKEN="eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJhYmMifQ.c2lnbmF0dXJl"
# What the base entrypoint writes before this file is sourced: a placeholder, not
# a working token. The guard must leave exactly this behind, untouched.
PREVIOUS_TOKEN="xxx"
# The real text CKAN printed on stdout when `api_token_create` rejected the call
# for a missing `expires_in`: the old command substitution captured THIS, and it
# is not a token either.
ERROR_TEXT="ValidationError: {'expires_in': ['Missing value']}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
BASE_PATH="$PATH"
mkdir -p "$BIN"

# A minimal stand-in for the two `ckan` invocations the guard makes.
cat > "$BIN/ckan" <<'STUB'
#!/usr/bin/env bash
# `ckan -c <ini> user token add <user> <name> ...` prints the minted token.
if [ "${1:-}" = "-c" ]; then
	printf 'mint\n' >> "${STUB_LOG:-/dev/null}"
	printf '%s\n' "${STUB_TOKEN:-}"
	exit 0
fi
# `ckan config-tool <ini> key=value` sets the key in the ini.
if [ "${1:-}" = "config-tool" ]; then
	ini="$2"
	pair="$3"
	key="${pair%%=*}"
	value="${pair#*=}"
	if grep -q "^${key}=" "$ini"; then
		grep -v "^${key}=" "$ini" > "$ini.tmp"
		mv "$ini.tmp" "$ini"
	fi
	printf '%s=%s\n' "$key" "$value" >> "$ini"
	exit 0
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

# Sources the guard in a subshell and prints a marker afterwards. The marker
# only appears if the shell survived, which is how a stray `exit` shows up.
run_guard() {
	(
		. "$SCRIPT"
		echo "__SURVIVED__:$?"
	)
}

# Scenario knobs, all overridable per test: ${PROVIDED-} for the operator's env
# variable, MINT_OUTPUT for what the fake CLI prints, PLUGINS for the plugin set.
setup() {
	INI="$WORK/ckan.ini"
	printf 'ckan.datapusher.api_token=%s\n' "$PREVIOUS_TOKEN" > "$INI"
	export CKAN_INI="$INI"
	export CKAN__PLUGINS="${PLUGINS-datapusher}"
	export CKAN__DATAPUSHER__API_TOKEN="${PROVIDED-}"
	export STUB_TOKEN="${MINT_OUTPUT-}"
	export STUB_LOG="$WORK/mint.log"
	export PATH="$BIN:$BASE_PATH"
	: > "$STUB_LOG"
}

marker_status() { # out -> the status the guard returned, or "no-marker"
	case "$1" in
	*__SURVIVED__:*) printf '%s' "${1##*__SURVIVED__:}" ;;
	*) printf 'no-marker' ;;
	esac
}

echo "a minted token is written to the ini"
PROVIDED= MINT_OUTPUT="$VALID_TOKEN" setup
out="$(run_guard 2>&1)"
assert_contains "the shell survives the script" "$out" "__SURVIVED__:"
assert_eq "the guard reports success" "0" "$(marker_status "$out")"
assert_contains "the mint ran" "$(cat "$STUB_LOG")" "mint"
assert_eq "the ini carries the minted token" \
	"ckan.datapusher.api_token=$VALID_TOKEN" "$(cat "$CKAN_INI")"

echo "a mint that produced nothing leaves the ini untouched"
PROVIDED= MINT_OUTPUT= setup
before="$(cat "$CKAN_INI")"
out="$(run_guard 2>&1)"
assert_contains "the shell survives the script" "$out" "__SURVIVED__:"
assert_eq "the guard reports failure" "1" "$(marker_status "$out")"
assert_eq "the ini is untouched" "$before" "$(cat "$CKAN_INI")"
assert_contains "the refusal is the guard's own message" "$out" "refusing to write"

echo "a mint that printed an error is not written either"
PROVIDED= MINT_OUTPUT="$ERROR_TEXT" setup
before="$(cat "$CKAN_INI")"
out="$(run_guard 2>&1)"
assert_contains "the shell survives the script" "$out" "__SURVIVED__:"
assert_eq "the guard reports failure" "1" "$(marker_status "$out")"
assert_eq "the ini is untouched" "$before" "$(cat "$CKAN_INI")"
assert_contains "the error text never reaches the ini" "$(cat "$CKAN_INI")" "$PREVIOUS_TOKEN"

echo "a token provided by the operator skips the mint"
PROVIDED="provided.jwt.value" MINT_OUTPUT= setup
before="$(cat "$CKAN_INI")"
out="$(run_guard 2>&1)"
assert_contains "the shell survives the script" "$out" "__SURVIVED__:"
assert_eq "the mint was not called" "" "$(cat "$STUB_LOG")"
assert_eq "the ini is untouched" "$before" "$(cat "$CKAN_INI")"

echo "a plugin set without datapusher does nothing"
PROVIDED= MINT_OUTPUT="$VALID_TOKEN" PLUGINS="stats activity" setup
before="$(cat "$CKAN_INI")"
out="$(run_guard 2>&1)"
assert_contains "the shell survives the script" "$out" "__SURVIVED__:"
assert_eq "the mint was not called" "" "$(cat "$STUB_LOG")"
assert_eq "the ini is untouched" "$before" "$(cat "$CKAN_INI")"

echo
if [ "$failures" -eq 0 ]; then
	echo "all tests passed"
	exit 0
fi
echo "$failures test(s) failed"
exit 1
