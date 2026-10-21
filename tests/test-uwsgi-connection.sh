#!/usr/bin/env bash
#
# Guards CKAN's uWSGI against closing a connection it looked willing to reuse.
#
# Run from anywhere:  bash tests/test-uwsgi-connection.sh
#
# uWSGI's http router answers HTTP/1.1 with neither `Connection: close` nor real
# keep-alive, then closes the socket. The portal's Node server (undici) keeps that
# socket in its pool and, when the next request leaves before the FIN arrives, fails
# with UND_ERR_SOCKET: the portal answers 502 "No se pudo contactar el catalogo" and
# the dataset/resource page shows "no se pueden cargar los graficos". Measured on the
# hs-node demo, 2026-10-21: 5 of 59 browser calls, and ~1 in 400 in a Node loop.
#
# The fix is `--add-header Connection:close` in EXTRA_UWSGI_OPTS (start_ckan.sh appends
# it, word-split, to its uwsgi command line): every response declares the close the
# server does anyway, so no client reuses the socket. Real keep-alive
# (`--http-keepalive`) was not chosen: it reopens the same race through uWSGI's idle
# close against undici's 4 s pool, and it was not measured.
#
# What is enforced:
#
#   1. Static: the resolved `ckan` service of the prod compose, and of the hs-node demo
#      (unified + `docker-compose.hs.yml`), carries `--add-header Connection:close` in
#      EXTRA_UWSGI_OPTS (no space inside the header value: it is word-split).
#   2. Behavioural (skipped without docker or the image): a throwaway uWSGI with the
#      same options, with a JSON body and a streamed body without Content-Length;
#      curl fetches two URLs over one connection. Both responses must carry
#      `Connection: close` and curl must never retry on a dead connection
#      ("Connection died").
#
# What this guard CANNOT see: the real CKAN app, or Node's pool. The race itself is
# timing-dependent; the probe checks the protocol behaviour that removes it.
#
# Every compose run uses a scratch copy with fake env values: the real `.env` files
# are never read. `config` needs the Compose CLI but no daemon.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
IMAGE="${CKAN_PROBE_IMAGE:-odp-demo-ckan:latest}"

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }
finish() {
	if [[ "$failures" -ne 0 ]]; then
		printf '\n%d check(s) failed\n' "$failures"
		exit 1
	fi
	printf '\nall checks passed\n'
	exit 0
}

if ! docker compose version >/dev/null 2>&1; then
	fail "the docker compose CLI is required (no daemon needed)"
	finish
fi
if ! command -v jq >/dev/null 2>&1; then
	fail "jq is required"
	finish
fi

S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/ckan-docker"
cp "$REPO_ROOT/docker-compose.unified.yml" "$REPO_ROOT/docker-compose.hs.yml" "$S/"
cp "$REPO_ROOT/ckan-docker/docker-compose.yml" "$S/ckan-docker/"
cp "$REPO_ROOT/ckan-docker/.env.example" "$S/ckan-docker/.env"
printf 'SESSION_SECRET=fake\n' >"$S/.env"
awk -F= '/^[A-Z_][A-Z0-9_]*=/ && $2 == "" { print $1 "=fake-" $1; next } { print }' \
	"$REPO_ROOT/demo.env.example" >"$S/.env.demo"

compose() { (cd "$S" && DOCKER_HOST=unix:///nonexistent docker compose "$@"); }

# The options the ckan service passes to uWSGI, on one line.
extra_opts() { jq -r '.services.ckan.environment.EXTRA_UWSGI_OPTS // ""' "$1"; }

check() { # label, json
	local opts
	opts="$(extra_opts "$2")"
	if [[ " $opts " == *" --add-header Connection:close "* ]]; then
		pass "$1: ckan EXTRA_UWSGI_OPTS has --add-header Connection:close"
	else
		fail "$1: ckan EXTRA_UWSGI_OPTS lacks --add-header Connection:close (got '$opts')"
	fi
}

if compose -f ckan-docker/docker-compose.yml config --format json >"$S/prod.json" 2>"$S/prod.err"; then
	check "prod compose" "$S/prod.json"
else
	fail "prod compose: config exits non-zero: $(head -c 400 "$S/prod.err")"
fi

if compose -f docker-compose.unified.yml -f docker-compose.hs.yml --env-file .env.demo \
	config --format json >"$S/demo.json" 2>"$S/demo.err"; then
	check "hs-node demo" "$S/demo.json"
else
	fail "hs-node demo: config exits non-zero: $(head -c 400 "$S/demo.err")"
fi

# --- Behavioural probe -------------------------------------------------------

# Options as the prod compose resolved them: the probe runs what the stack runs.
opts="$(extra_opts "$S/prod.json")"

probe() { # label, uwsgi options, path
	# Mounted dirs must be world-readable: the image's user is not ours.
	local out headers
	out="$(timeout 90 docker run --rm --network none --entrypoint bash -v "$S/probe:/p:ro" \
		"$IMAGE" /p/run.sh "$2" "$3" 2>&1 | grep -aiE '^< (HTTP|connection)|died' | head -20)"
	headers="$(grep -aic '^< connection: *close' <<<"$out")"
	if grep -qai 'died' <<<"$out"; then
		fail "$1: curl hit a dead reused connection: $(tr '\r\n' ';;' <<<"$out")"
	elif [[ "$headers" -ne 2 ]]; then
		fail "$1: expected Connection: close on both responses, saw $headers: $(tr '\r\n' ';;' <<<"$out")"
	else
		pass "$1: both responses carry Connection: close, no dead reuse"
	fi
}

if ! command -v curl >/dev/null 2>&1; then
	printf 'skip behavioural probe: curl is not installed\n'
elif ! timeout 20 docker image inspect "$IMAGE" >/dev/null 2>&1; then
	printf 'skip behavioural probe: docker or the image %s is unavailable\n' "$IMAGE"
else
	mkdir -p "$S/probe"
	cat >"$S/probe/app.py" <<'PY'
def application(env, start_response):
    if env['PATH_INFO'] == '/stream':
        start_response('200 OK', [('Content-Type', 'application/octet-stream')])
        return (b'x' * 1000 for _ in range(5))
    body = b'{"ok":true}'
    start_response('200 OK', [('Content-Type', 'application/json'), ('Content-Length', str(len(body)))])
    return [body]
PY
	cat >"$S/probe/run.sh" <<'SH'
#!/bin/bash
# $1: extra uwsgi options (word-split on purpose), $2: path fetched twice.
# --die-on-term: the master reloads on SIGTERM otherwise.
uwsgi --die-on-term --http 127.0.0.1:5000 --master -p 2 --wsgi-file /p/app.py -L $1 >/tmp/u.log 2>&1 &
sleep 2
curl -sv -o /dev/null "http://127.0.0.1:5000$2" "http://127.0.0.1:5000$2" 2>&1
kill -INT %1
wait
SH
	chmod 755 "$S/probe" "$S/probe/run.sh"
	chmod 644 "$S/probe/app.py"
	probe "probe, JSON body with Content-Length" "$opts" /json
	probe "probe, streamed body without Content-Length" "$opts" /stream
	# Must-fail control: without the options the probe sees the closed connection.
	if timeout 90 docker run --rm --network none --entrypoint bash -v "$S/probe:/p:ro" \
		"$IMAGE" /p/run.sh "" /stream 2>&1 | grep -aqE 'Closing connection 0' \
		&& ! timeout 90 docker run --rm --network none --entrypoint bash -v "$S/probe:/p:ro" \
			"$IMAGE" /p/run.sh "" /stream 2>&1 | grep -aqi '^< connection: *close'; then
		pass "probe control: bare uWSGI closes the connection without declaring it"
	else
		fail "probe control: bare uWSGI no longer closes; the probe proves nothing"
	fi
fi

finish
