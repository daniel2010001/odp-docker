#!/usr/bin/env bash
#
# Guards the upload ceiling on the portal's own upload route, `/catalog/`.
#
# Run from anywhere:  bash tests/test-upload-body-limit.sh
#
# Resource uploads go through the portal (`/catalog/resource_create`), which
# injects the credential and forwards to CKAN. Two ceilings sit in front of it,
# and each one refuses an upload before the next one is reached:
#
#   - `frontend-proxy`'s nginx: `/catalog/` falls under `location /` unless it
#     has its own block, and nginx's default `client_max_body_size` is 1 MB --
#     a `413` before the portal sees a byte.
#   - adapter-node's `BODY_SIZE_LIMIT` (production `frontend`): its default is
#     512 KB, and past it the portal answers `400`, not `413`.
#
# The reference is the ceiling `/api/` already carries in the same proxy
# (`55M`: the 50 MB per resource of PRD RF-12 plus multipart overhead, and below
# CKAN's effective 100 MB). What is enforced:
#
#   1. `frontend-proxy/nginx.conf` and `frontend-proxy/dev-nginx.conf` each have
#      a `location /catalog/` whose `client_max_body_size` equals their own
#      `/api/` ceiling.
#   2. `location /` sets no `client_max_body_size` in either file: large bodies
#      are accepted on the upload route only, not on every page.
#   3. The production `frontend` sets `BODY_SIZE_LIMIT` to that same value.
#
# Must-fail fixtures prove the checker refuses each wrong shape.
#
# What this guard CANNOT see, stated rather than implied:
#
#   - **The units agree in this repo's spelling only.** nginx and adapter-node
#     both read `55M` as 55 * 1024 * 1024 bytes; the test compares the strings,
#     so a value written as bytes on one side and with a suffix on the other
#     fails here even though it would be equal. That is deliberate: one
#     spelling, compared literally.
#   - **CKAN's own ceiling** (`CKAN_MAX_UPLOAD_SIZE_MB`, read through
#     `CONFIG_FROM_ENV_VARS`) lives in `ckan-docker/.env`, which is the
#     operator's. Measured on 2026-10-16 in the dev stack: 100.
#   - **The dev frontend** imposes no body limit under `vite dev`, so the dev
#     compose has no `BODY_SIZE_LIMIT` to check.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }

# The `client_max_body_size` set directly inside `location <path> {`, or
# nothing. Blocks are tracked by brace depth: a `location` opens at depth 1
# (inside `server {`) and its directives sit at depth 2, so a nested block never
# leaks into its parent's answer.
location_body_size() {
	awk -v want="$2" '
		{
			line = $0
			sub(/#.*/, "", line)
		}
		depth == 1 && match(line, /location[[:space:]]+[^[:space:]{]+[[:space:]]*\{/) {
			path = substr(line, RSTART, RLENGTH)
			sub(/^location[[:space:]]+/, "", path)
			sub(/[[:space:]]*\{$/, "", path)
		}
		{
			opens = gsub(/\{/, "{", line)
			closes = gsub(/\}/, "}", line)
		}
		path == want && depth == 2 && line ~ /^[[:space:]]*client_max_body_size[[:space:]]/ {
			value = line
			sub(/^[[:space:]]*client_max_body_size[[:space:]]+/, "", value)
			sub(/[[:space:]]*;.*$/, "", value)
			print value
			exit
		}
		{
			depth += opens - closes
			if (depth < 2) path = ""
		}
	' "$1"
}

# Exit 0 when the proxy file gives `/catalog/` the `/api/` ceiling and leaves
# `location /` at the default.
proxy_matches_api() {
	local api catalog root
	api="$(location_body_size "$1" /api/)"
	catalog="$(location_body_size "$1" /catalog/)"
	root="$(location_body_size "$1" /)"
	[[ -n "$api" && "$catalog" == "$api" && -z "$root" ]]
}

# The `BODY_SIZE_LIMIT` in the `environment` of the `frontend` service.
frontend_body_size_limit() {
	awk '
		/^  [^ #][^:]*:/ { in_frontend = ($0 ~ /^  frontend:[[:space:]]*$/) }
		in_frontend && /^      BODY_SIZE_LIMIT:/ {
			sub(/^      BODY_SIZE_LIMIT:[[:space:]]*/, "")
			gsub(/"/, "")
			print
			exit
		}
	' "$1"
}

describe() {
	printf "/api/='%s' /catalog/='%s' /='%s'" \
		"$(location_body_size "$1" /api/)" \
		"$(location_body_size "$1" /catalog/)" \
		"$(location_body_size "$1" /)"
}

# --- The real tree -----------------------------------------------------------

for conf in frontend-proxy/nginx.conf frontend-proxy/dev-nginx.conf; do
	if proxy_matches_api "$REPO_ROOT/$conf"; then
		pass "$conf: /catalog/ carries the /api/ ceiling and / keeps the default"
	else
		fail "$conf: want /catalog/ == /api/ and nothing on /, got $(describe "$REPO_ROOT/$conf")"
	fi
done

api="$(location_body_size "$REPO_ROOT/frontend-proxy/nginx.conf" /api/)"
limit="$(frontend_body_size_limit "$REPO_ROOT/docker-compose.unified.yml")"
if [[ -n "$api" && "$limit" == "$api" ]]; then
	pass "docker-compose.unified.yml: frontend BODY_SIZE_LIMIT is the proxy's $api"
else
	fail "docker-compose.unified.yml: frontend BODY_SIZE_LIMIT must be '$api', got '$limit'"
fi

# --- Fixtures ------------------------------------------------------------------

FIXTURES="$(mktemp -d)"
trap 'rm -rf "$FIXTURES"' EXIT

proxy_fixture() {
	printf 'server {\n    location /api/ {\n        client_max_body_size 55M;\n    }\n%s\n}\n' "$1" >"$FIXTURES/nginx.conf"
}

# Must-pass first: a checker that refuses everything would make every must-fail
# row below green for the wrong reason.
proxy_fixture "$(printf '    location /catalog/ {\n        proxy_pass http://frontend:3000;\n        client_max_body_size 55M;  # upload route\n    }\n    location / {\n        proxy_pass http://frontend:3000;\n    }')"
if proxy_matches_api "$FIXTURES/nginx.conf"; then
	pass "checker accepts a proxy whose /catalog/ carries the /api/ ceiling"
else
	fail "checker refused a correct proxy: $(describe "$FIXTURES/nginx.conf")"
fi

printf 'services:\n  frontend:\n    environment:\n      BODY_SIZE_LIMIT: "55M"\n' >"$FIXTURES/compose.yml"
if [[ "$(frontend_body_size_limit "$FIXTURES/compose.yml")" == 55M ]]; then
	pass "checker reads BODY_SIZE_LIMIT from the frontend service"
else
	fail "checker could not read BODY_SIZE_LIMIT from a correct frontend"
fi

# Must-fail.

while IFS='|' read -r label body; do
	proxy_fixture "$(printf '%b' "$body")"
	if proxy_matches_api "$FIXTURES/nginx.conf"; then
		fail "checker accepted a proxy with $label"
	else
		pass "checker refuses a proxy with $label"
	fi
done <<'EOF'
no /catalog/ block|    location / {\n        proxy_pass http://frontend:3000;\n    }
a /catalog/ block without its own ceiling|    location /catalog/ {\n        proxy_pass http://frontend:3000;\n    }
a /catalog/ ceiling below /api/|    location /catalog/ {\n        client_max_body_size 1M;\n    }
the ceiling raised on / instead|    location / {\n        client_max_body_size 55M;\n    }
the ceiling only in a comment|    location /catalog/ {\n        # client_max_body_size 55M;\n    }
EOF

for wrong in '' '      BODY_SIZE_LIMIT: 512K'; do
	printf 'services:\n  frontend:\n    environment:\n%s\n  frontend-proxy:\n    environment:\n      BODY_SIZE_LIMIT: 55M\n' "$wrong" >"$FIXTURES/compose.yml"
	got="$(frontend_body_size_limit "$FIXTURES/compose.yml")"
	if [[ "$got" == 55M ]]; then
		fail "checker read 55M from a frontend wired as '${wrong:-<absent, only in frontend-proxy>}'"
	else
		pass "checker refuses a frontend wired as '${wrong:-<absent, only in frontend-proxy>}'"
	fi
done

if [[ "$failures" -ne 0 ]]; then
	printf '\n%d check(s) failed\n' "$failures"
	exit 1
fi
printf '\nall checks passed\n'
