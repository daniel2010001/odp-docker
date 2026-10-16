#!/usr/bin/env bash
#
# Guards the production `frontend`'s session key: `SESSION_SECRET`.
#
# Run from anywhere:  bash tests/test-frontend-session-secret.sh
#
# The portal seals the CKAN credential in an httpOnly cookie (AES-256-GCM) keyed
# by `SESSION_SECRET`. Outside `vite dev` there is deliberately no default: a
# default key would let anyone forge a session. So the production compose must
# hand the key to `frontend`, and must refuse to start without it rather than
# start a portal whose every login answers `500`.
#
# What is enforced:
#
#   1. **The wiring.** `docker-compose.unified.yml` passes `SESSION_SECRET` to the
#      `frontend` service in the required form `${SESSION_SECRET:?...}`. A
#      default (`:-`), a bare `${SESSION_SECRET}` or a literal fails: each one
#      starts the stack without a key, or with a known one.
#   2. **The declaration.** The root `.env.example` declares `SESSION_SECRET=`
#      with an **empty** value. A committed value would be a shared key.
#
# Must-fail fixtures prove the checker refuses each wrong form; a checker that
# accepts everything is a green row that means nothing.
#
# What this guard CANNOT see, stated rather than implied:
#
#   - **Compose's interpolation.** It checks the text, not what Compose does with
#     it. Measured with docker compose 5.6.0 on 2026-10-16 instead: `:?` makes
#     every command on the file fail with `required variable ... is missing a
#     value`, and a variable used in the ROOT compose is read from the ROOT
#     `.env` -- never from `ckan-docker/.env`, which only feeds the `include`d
#     file. That is why the example lives at the root.
#   - **The value.** The real `.env` is the operator's and is never read here.
#   - **The dev stack.** `docker-compose.dev.unified.yml` is out of scope on
#     purpose: under `vite dev` the portal has a development fallback key.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }

# The value of `SESSION_SECRET` in the `environment` of the `frontend` service,
# or nothing. The service block runs from `  frontend:` to the next two-space
# key, so `frontend-proxy` and every other service are never read.
frontend_session_secret() {
	awk '
		/^  [^ #][^:]*:/ { in_frontend = ($0 ~ /^  frontend:[[:space:]]*$/) }
		in_frontend && /^      SESSION_SECRET:/ {
			sub(/^      SESSION_SECRET:[[:space:]]*/, "")
			print
			exit
		}
	' "$1"
}

# Exit 0 when the compose file wires the key in the required form.
compose_requires_key() {
	local value
	value="$(frontend_session_secret "$1")"
	value="${value#\"}"
	[[ "$value" == '${SESSION_SECRET:?'* ]]
}

# Exit 0 when the example declares the key exactly once and empty.
example_declares_empty_key() {
	local lines
	lines="$(grep -E '^[[:space:]]*SESSION_SECRET[[:space:]]*=' "$1" 2>/dev/null)"
	[[ "$(printf '%s\n' "$lines" | grep -c .)" -eq 1 ]] &&
		[[ "$lines" =~ ^[[:space:]]*SESSION_SECRET[[:space:]]*=[[:space:]]*$ ]]
}

# --- The real tree -----------------------------------------------------------

if compose_requires_key "$REPO_ROOT/docker-compose.unified.yml"; then
	pass "docker-compose.unified.yml: frontend requires SESSION_SECRET"
else
	fail "docker-compose.unified.yml: frontend must pass SESSION_SECRET as \${SESSION_SECRET:?...}, got: '$(frontend_session_secret "$REPO_ROOT/docker-compose.unified.yml")'"
fi

if example_declares_empty_key "$REPO_ROOT/.env.example"; then
	pass ".env.example: declares SESSION_SECRET with an empty value"
else
	fail ".env.example: must declare exactly one 'SESSION_SECRET=' with an empty value"
fi

# --- Must-fail fixtures ------------------------------------------------------

FIXTURES="$(mktemp -d)"
trap 'rm -rf "$FIXTURES"' EXIT

compose_fixture() {
	printf 'services:\n  frontend:\n    environment:\n      CKAN_INTERNAL_URL: http://ckan:5000\n%s\n  frontend-proxy:\n    environment:\n      SESSION_SECRET: ${SESSION_SECRET:?only here}\n' "$1" >"$FIXTURES/compose.yml"
}

for wrong in \
	'      SESSION_SECRET: ${SESSION_SECRET:-dev}' \
	'      SESSION_SECRET: ${SESSION_SECRET}' \
	'      SESSION_SECRET: hardcoded' \
	''; do
	compose_fixture "$wrong"
	if compose_requires_key "$FIXTURES/compose.yml"; then
		fail "checker accepted a frontend wired as '${wrong:-<absent, only in frontend-proxy>}'"
	else
		pass "checker refuses a frontend wired as '${wrong:-<absent, only in frontend-proxy>}'"
	fi
done

for wrong in 'SESSION_SECRET=committed-value' '# SESSION_SECRET=' $'SESSION_SECRET=\nSESSION_SECRET='; do
	printf '%s\n' "$wrong" >"$FIXTURES/env.example"
	label="${wrong//$'\n'/\\n}"
	if example_declares_empty_key "$FIXTURES/env.example"; then
		fail "checker accepted an example reading '$label'"
	else
		pass "checker refuses an example reading '$label'"
	fi
done

if [[ "$failures" -ne 0 ]]; then
	printf '\n%d check(s) failed\n' "$failures"
	exit 1
fi
printf '\nall checks passed\n'
