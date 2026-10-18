#!/usr/bin/env bash
#
# Guards the hs-node demo override: `docker-compose.hs.yml`.
#
# Run from anywhere:  bash tests/test-hs-demo-compose.sh
#
# The v0 demo runs the PRODUCTION stack on hs-node, in place of odp-dev, behind
# the homelab's caddy-docker-proxy:
#
#   docker compose -f docker-compose.unified.yml -f docker-compose.hs.yml \
#     --env-file .env.demo up -d --build
#
# What is enforced, on Compose's own resolved config (`config --format json`),
# never on the text:
#
#   1. **Its own project.** `odp-demo`, so its volumes never touch odp-dev's or a
#      future prod stack's.
#   2. **Caddy is the only entrance.** No service publishes a host port (the
#      homelab forbids it); the CKAN TLS `nginx` is not started; frontend-proxy
#      and ckan join the external `hs-net` with the homelab's labels, under
#      names that are not odp-dev's (Caddy would load-balance between stacks).
#   3. **The origins match.** CKAN's `ckan.site_url` is its own public name,
#      with the portal's scheme, and adapter-node's `ORIGIN` is the portal's:
#      behind Caddy the proxy forwards `X-Forwarded-Proto: http`, and without
#      `ORIGIN` SvelteKit refuses every form POST, the login included.
#   4. **No dev secret leaks in.** `ckan-docker/.env` is shared with odp-dev.
#      Every secret it declares must come from `.env.demo` instead, in every
#      service that receives it. A key missing from `.env.demo` would silently
#      pass the dev value through, so `demo.env.example` must list them all.
#   5. **It fails closed.** Without `--env-file .env.demo`, Compose refuses.
#   6. **The prod compose alone is unchanged.** Without the override it still
#      publishes 8080 and starts the CKAN nginx.
#
# Every run uses a scratch copy with fake values: the real `.env` files are
# never read. `config` needs the Compose CLI but no daemon (DOCKER_HOST points
# nowhere on purpose), so this runs in the host-test job.
#
# What this guard CANNOT see, stated rather than implied: that Caddy routes the
# names, that AdGuard or `/etc/hosts` resolves them, and that the stack comes up.
# Those are the bring-up checks in the README («Demo en hs-node»).
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

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
for f in docker-compose.hs.yml demo.env.example; do
	if [[ ! -f "$REPO_ROOT/$f" ]]; then
		fail "$f does not exist"
	fi
done
[[ "$failures" -eq 0 ]] || finish

DEMO_HOST=demo.odp.hs.lan
SECRET_PATTERN='PASSWORD|SECRET|SQLALCHEMY_URL|DATASTORE_(WRITE|READ)_URL'

# The secret keys of the shared CKAN env, read from its committed example.
mapfile -t SECRET_KEYS < <(grep -oE '^[A-Z_][A-Z0-9_]*=' "$REPO_ROOT/ckan-docker/.env.example" |
	tr -d '=' | grep -E "$SECRET_PATTERN" | sort -u)

S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/ckan-docker"
cp "$REPO_ROOT/docker-compose.unified.yml" "$REPO_ROOT/docker-compose.hs.yml" "$S/"
cp "$REPO_ROOT/ckan-docker/docker-compose.yml" "$S/ckan-docker/"

# odp-dev's env: the example, with every secret marked as a leak and every DB name
# made different from the demo's.
awk -v pat="$SECRET_PATTERN" -F= '
	/^[A-Z_][A-Z0-9_]*=/ && $1 ~ pat { print $1 "=DEVLEAK-" $1; next }
	/^(POSTGRES_USER|POSTGRES_DB|CKAN_DB_USER|CKAN_DB|DATASTORE_READONLY_USER|DATASTORE_DB)=/ { print $1 "=devname_" tolower($1); next }
	{ print }
' "$REPO_ROOT/ckan-docker/.env.example" >"$S/ckan-docker/.env"
# The root `.env` odp-dev and the plain prod stack read.
printf 'SESSION_SECRET=DEVLEAK-SESSION_SECRET\n' >"$S/.env"

# The demo env: the example, every empty key filled with demo-<key>. $1 drops one key.
write_demo_env() {
	awk -v drop="${1:-}" -F= '
		/^[A-Z_][A-Z0-9_]*=/ {
			if ($1 == drop) next
			if ($2 == "") { print $1 "=demo-" $1; next }
		}
		{ print }
	' "$REPO_ROOT/demo.env.example" >"$S/.env.demo"
}

compose() { (cd "$S" && DOCKER_HOST=unix:///nonexistent docker compose "$@"); }
demo_config() { compose -f docker-compose.unified.yml -f docker-compose.hs.yml --env-file .env.demo config --format json; }

# --- 4. The example lists every secret, empty --------------------------------

declared_value() { grep -E "^$1=" "$REPO_ROOT/demo.env.example" | head -n1 | cut -d= -f2-; }
for key in "${SECRET_KEYS[@]}" SESSION_SECRET; do
	count="$(grep -cE "^$key=" "$REPO_ROOT/demo.env.example")"
	if [[ "$count" -eq 1 && -z "$(declared_value "$key")" ]]; then
		pass "demo.env.example: declares $key once, empty"
	else
		fail "demo.env.example: must declare '$key=' exactly once with an empty value (found $count)"
	fi
done
if [[ "$(declared_value DEMO_HOST)" == "$DEMO_HOST" ]]; then
	pass "demo.env.example: DEMO_HOST=$DEMO_HOST"
else
	fail "demo.env.example: DEMO_HOST must be '$DEMO_HOST', got '$(declared_value DEMO_HOST)'"
fi
# The DB names the URLs are built from: the demo's own, never ckan-docker/.env's.
for key in POSTGRES_USER POSTGRES_DB CKAN_DB_USER CKAN_DB DATASTORE_READONLY_USER DATASTORE_DB; do
	if [[ -n "$(declared_value "$key")" ]]; then
		pass "demo.env.example: declares the name $key"
	else
		fail "demo.env.example: must declare a value for $key"
	fi
done

# --- The resolved demo config ------------------------------------------------

write_demo_env
if ! demo_config >"$S/demo.json" 2>"$S/demo.err"; then
	fail "config with the override exits non-zero: $(head -c 400 "$S/demo.err")"
	finish
fi
pass "config with the override and --env-file .env.demo exits 0"

q() { jq -r "$1" "$S/demo.json"; }
expect() { # label, jq filter, expected
	local got
	got="$(q "$2")"
	if [[ "$got" == "$3" ]]; then pass "$1"; else fail "$1: expected '$3', got '$got'"; fi
}

# 1.
expect "project is odp-demo" '.name' 'odp-demo'

# 2.
expect "services: the stack without the CKAN TLS nginx" \
	'.services | keys | join(",")' 'ckan,ckan-worker,db,frontend,frontend-proxy,redis,solr'
expect "no service publishes a host port" '[.services[] | (.ports // []) | length] | add' '0'
expect "hs-net is the homelab's external network" '.networks["hs-net"].external' 'true'
expect_labels() { # label, service, expected caddy name, upstream port
	local got
	got="$(jq -c --arg s "$2" '.services[$s].labels // {}' "$S/demo.json")"
	if jq -e --arg s "$2" --arg n "$3" --arg u "{{upstreams $4}}" \
		'.services[$s].labels == {"caddy": $n, "caddy.tls": "internal", "caddy.reverse_proxy": $u}' "$S/demo.json" >/dev/null; then
		pass "$1"
	else
		fail "$1: expected caddy=$3, caddy.tls=internal, caddy.reverse_proxy={{upstreams $4}}, got $got"
	fi
}
expect_labels "frontend-proxy: Caddy labels" frontend-proxy "$DEMO_HOST" 80
expect "frontend-proxy: keeps its networks and joins hs-net" \
	'.services["frontend-proxy"].networks | keys | join(",")' 'ckannet,hs-net,webnet'
expect_labels "ckan: Caddy labels" ckan "api.$DEMO_HOST" 5000
expect "ckan: keeps its networks and joins hs-net" \
	'.services.ckan.networks | keys | join(",")' 'ckannet,dbnet,hs-net,redisnet,solrnet'
expect "ckan-worker: no Caddy labels" '.services["ckan-worker"].labels // {} | length' '0'

# 3.
expect "ckan: CKAN_SITE_URL is CKAN's own public origin" '.services.ckan.environment.CKAN_SITE_URL' "https://api.$DEMO_HOST"
expect "ckan-worker: same CKAN_SITE_URL" '.services["ckan-worker"].environment.CKAN_SITE_URL' "https://api.$DEMO_HOST"
expect "frontend: ORIGIN is the portal's public origin" '.services.frontend.environment.ORIGIN' "https://$DEMO_HOST"

# 4.
leaks="$(grep -oE 'DEVLEAK-[A-Z0-9_]+' "$S/demo.json" | sort -u | tr '\n' ' ')"
if [[ -z "$leaks" ]]; then pass "no dev secret reaches any service"; else fail "dev secrets leak in: $leaks"; fi
for svc in ckan ckan-worker; do
	for key in "${SECRET_KEYS[@]}"; do
		expect "$svc: $key comes from .env.demo" ".services[\"$svc\"].environment.$key" "demo-$key"
	done
	expect "$svc: the xloader jobs DB is the demo CKAN DB" \
		".services[\"$svc\"].environment.CKANEXT__XLOADER__JOBS_DB__URI" 'demo-CKAN_SQLALCHEMY_URL'
done
for key in POSTGRES_PASSWORD CKAN_DB_PASSWORD DATASTORE_READONLY_PASSWORD; do
	expect "db: $key comes from .env.demo" ".services.db.environment.$key" "demo-$key"
done
for key in CKAN_DB_USER CKAN_DB DATASTORE_READONLY_USER DATASTORE_DB; do
	for svc in db ckan; do
		expect "$svc: $key is the demo's" ".services[\"$svc\"].environment.$key" "$(declared_value "$key")"
	done
done
expect "frontend: SESSION_SECRET comes from .env.demo" '.services.frontend.environment.SESSION_SECRET' 'demo-SESSION_SECRET'

# Must-fail fixture: a demo env missing one secret is caught as a leak.
write_demo_env CKAN_DB_PASSWORD
if demo_config 2>/dev/null | grep -q 'DEVLEAK-CKAN_DB_PASSWORD'; then
	pass "checker sees the leak when .env.demo lacks CKAN_DB_PASSWORD"
else
	fail "checker missed the leak when .env.demo lacks CKAN_DB_PASSWORD"
fi

# --- 5. It fails closed ------------------------------------------------------

write_demo_env DEMO_HOST
if demo_config >/dev/null 2>"$S/err"; then
	fail "config without DEMO_HOST must refuse"
elif grep -q 'DEMO_HOST' "$S/err" && grep -q -- '--env-file .env.demo' "$S/err"; then
	pass "config without DEMO_HOST refuses, naming DEMO_HOST and --env-file .env.demo"
else
	fail "config without DEMO_HOST refuses, but says: $(head -c 300 "$S/err")"
fi

write_demo_env
if compose -f docker-compose.unified.yml -f docker-compose.hs.yml config >/dev/null 2>"$S/err"; then
	fail "config without --env-file .env.demo (root .env only) must refuse"
else
	pass "config without --env-file .env.demo refuses"
fi

# --- 6. The prod compose alone is unchanged ----------------------------------

if compose -f docker-compose.unified.yml config --format json >"$S/prod.json" 2>"$S/err"; then
	pq() { jq -r "$1" "$S/prod.json"; }
	[[ "$(pq '.name')" != 'odp-demo' ]] && pass "prod alone: project is not odp-demo" || fail "prod alone: project became odp-demo"
	[[ "$(pq '.services["frontend-proxy"].ports[0] | "\(.published):\(.target)"')" == '8080:80' ]] &&
		pass "prod alone: frontend-proxy still publishes 8080:80" || fail "prod alone: frontend-proxy lost 8080:80"
	[[ "$(pq '.services | has("nginx")')" == 'true' ]] && pass "prod alone: the CKAN nginx is still started" || fail "prod alone: nginx disappeared"
	[[ "$(pq '.networks | has("hs-net")')" == 'false' ]] && pass "prod alone: no hs-net" || fail "prod alone: hs-net leaked into the prod compose"
else
	fail "prod alone: config exits non-zero: $(head -c 300 "$S/err")"
fi

finish
