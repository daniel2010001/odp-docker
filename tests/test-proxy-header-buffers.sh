#!/usr/bin/env bash
#
# Guards the production frontend-proxy's buffer for upstream response headers.
#
# Run from anywhere:  bash tests/test-proxy-header-buffers.sh
#
# A production build of the portal answers dataset and resource pages with a
# `Link:` preload header of ~7 KB (91 entries, measured on the hs-node demo,
# 2026-10-18). nginx's default `proxy_buffer_size` is one memory page (4 or 8 KB),
# so frontend-proxy refused those responses with `upstream sent too big header`
# and the browser got 502, while `/` and `/search` (smaller headers) worked.
#
# What is enforced, on `frontend-proxy/nginx.conf`:
#
#   1. `proxy_buffer_size` is set at the `server` level (so `/`, `/catalog/`
#      and `/api/` all get it, not one location) and is at least 32k: more than
#      four times the measured header, so new routes do not bring the 502 back.
#   2. `proxy_busy_buffers_size` is set, at least `proxy_buffer_size`: nginx
#      refuses to start otherwise ("must be equal to or greater than ...").
#
# Must-fail fixtures prove the checker refuses each wrong form.
#
# What this guard CANNOT see, stated rather than implied: nginx's own parse.
# That was measured instead with `nginx -t` on nginx:alpine and live on the demo.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }

# Value in KB of a directive at the server level (depth 1), or nothing.
server_level_kb() { # file, directive
	awk -v d="$2" '
		{ line = $0; sub(/#.*/, "", line) }
		depth == 1 && line ~ "^[[:space:]]*" d "[[:space:]]" {
			v = line; sub("^[[:space:]]*" d "[[:space:]]+", "", v); sub(/;.*/, "", v)
			if (v ~ /^[0-9]+[kK]$/) { print v + 0 }
			else if (v ~ /^[0-9]+[mM]$/) { print (v + 0) * 1024 }
			exit
		}
		{ n = gsub(/\{/, "{", line); m = gsub(/\}/, "}", line); depth += n - m }
	' "$1"
}

# Exit 0 when the file's buffers are right.
buffers_ok() {
	local size busy
	size="$(server_level_kb "$1" proxy_buffer_size)"
	busy="$(server_level_kb "$1" proxy_busy_buffers_size)"
	[[ -n "$size" && "$size" -ge 32 && -n "$busy" && "$busy" -ge "$size" ]]
}

conf="$REPO_ROOT/frontend-proxy/nginx.conf"
if buffers_ok "$conf"; then
	pass "nginx.conf: server-level proxy_buffer_size $(server_level_kb "$conf" proxy_buffer_size)k, busy $(server_level_kb "$conf" proxy_busy_buffers_size)k"
else
	fail "nginx.conf: needs server-level proxy_buffer_size >= 32k and proxy_busy_buffers_size >= it (got '$(server_level_kb "$conf" proxy_buffer_size)' / '$(server_level_kb "$conf" proxy_busy_buffers_size)')"
fi

# --- Must-fail fixtures ------------------------------------------------------

F="$(mktemp -d)"
trap 'rm -rf "$F"' EXIT

fixture() { printf 'server {\n    listen 80;\n%s\n    location / {\n%s\n        proxy_pass http://frontend:3000;\n    }\n}\n' "$1" "$2" >"$F/nginx.conf"; }

check_refuses() { # label
	if buffers_ok "$F/nginx.conf"; then fail "checker accepted: $1"; else pass "checker refuses: $1"; fi
}

fixture '' ''
check_refuses "no buffer directives (nginx default)"
fixture '    proxy_buffer_size 8k;
    proxy_busy_buffers_size 16k;' ''
check_refuses "an 8k buffer"
fixture '' '        proxy_buffer_size 32k;
        proxy_busy_buffers_size 64k;'
check_refuses "the buffers only inside one location"
fixture '    proxy_buffer_size 32k;' ''
check_refuses "no proxy_busy_buffers_size"
fixture '    proxy_buffer_size 32k;
    proxy_busy_buffers_size 16k;' ''
check_refuses "busy smaller than the buffer"
fixture '    # proxy_buffer_size 32k;
    # proxy_busy_buffers_size 64k;' ''
check_refuses "commented-out directives"

fixture '    proxy_buffer_size 32k;
    proxy_busy_buffers_size 64k;' ''
if buffers_ok "$F/nginx.conf"; then pass "checker accepts a correct server block"; else fail "checker refused a correct server block"; fi

if [[ "$failures" -ne 0 ]]; then
	printf '\n%d check(s) failed\n' "$failures"
	exit 1
fi
printf '\nall checks passed\n'
