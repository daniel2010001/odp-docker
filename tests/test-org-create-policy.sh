#!/usr/bin/env bash
#
# Guards the rule that only a sysadmin creates or deletes organizations.
#
# Run from anywhere:  bash tests/test-org-create-policy.sh
#
# CKAN 2.12 defaults `ckan.auth.user_create_organizations` to true: any logged-in user
# can call `organization_create` and becomes the admin of the new organization. The PRD
# reserves that to the superadministrator. `ckan.auth.user_delete_organizations` is
# also true by default, and `organization_delete` then only asks for the `delete`
# permission, which the org `admin` role holds: an organization's admin could delete
# it. The PRD reserves deletion to the superadministrator too (RF-35). Both settings
# travel as CKAN__AUTH__USER_{CREATE,DELETE}_ORGANIZATIONS=false in the `environment:`
# of every CKAN service, so they are versioned and a local `ckan-docker/.env` cannot
# turn them back on (Compose: environment > env_file). Collections (groups) stay open
# to every registered user (RF-21): no `user_*_groups` setting is touched.
#
# What is enforced, on the resolved config of the prod compose, the hs-node demo
# (unified + `docker-compose.hs.yml`) and the dev compose:
#
#   1. Every CKAN service (ckan, ckan-worker / ckan-dev, ckan-worker-dev) has
#      CKAN__AUTH__USER_CREATE_ORGANIZATIONS=false and
#      CKAN__AUTH__USER_DELETE_ORGANIZATIONS=false, even with a `.env` that says true.
#   2. No CKAN service sets CKAN__AUTH__USER_CREATE_GROUPS or
#      CKAN__AUTH__USER_DELETE_GROUPS.
#
# What this guard CANNOT see: the running CKAN. The live proof is a non-sysadmin token
# answering 403 on `organization_create`, and an org admin's token answering 403 on
# `organization_delete` while the sysadmin's succeeds (see README, "Crear y borrar
# organizaciones").
#
# Every compose run uses a scratch copy with fake env values: the real `.env` files
# are never read. `config` needs the Compose CLI but no daemon.
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

S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/ckan-docker"
cp "$REPO_ROOT/docker-compose.unified.yml" "$REPO_ROOT/docker-compose.hs.yml" \
	"$REPO_ROOT/docker-compose.dev.unified.yml" "$S/"
cp "$REPO_ROOT/ckan-docker/docker-compose.yml" "$REPO_ROOT/ckan-docker/docker-compose.dev.yml" \
	"$S/ckan-docker/"
# A local `.env` that tries to reopen both: the compose must win.
{
	cat "$REPO_ROOT/ckan-docker/.env.example"
	printf '\nCKAN__AUTH__USER_CREATE_ORGANIZATIONS=true\nCKAN__AUTH__USER_DELETE_ORGANIZATIONS=true\n'
} >"$S/ckan-docker/.env"
printf 'SESSION_SECRET=fake\n' >"$S/.env"
awk -F= '/^[A-Z_][A-Z0-9_]*=/ && $2 == "" { print $1 "=fake-" $1; next } { print }' \
	"$REPO_ROOT/demo.env.example" >"$S/.env.demo"

compose() { (cd "$S" && DOCKER_HOST=unix:///nonexistent docker compose "$@"); }

check() { # label, json, services...
	local label="$1" json="$2" svc op key value
	shift 2
	for svc in "$@"; do
		for op in CREATE DELETE; do
			key="CKAN__AUTH__USER_${op}_ORGANIZATIONS"
			value="$(jq -r --arg s "$svc" --arg k "$key" '.services[$s].environment[$k] // "<unset>"' "$json")"
			if [[ "$value" == "false" ]]; then
				pass "$label: $svc has $key=false"
			else
				fail "$label: $svc $key is '$value', want 'false'"
			fi
			key="CKAN__AUTH__USER_${op}_GROUPS"
			value="$(jq -r --arg s "$svc" --arg k "$key" '.services[$s].environment[$k] // "<unset>"' "$json")"
			if [[ "$value" == "<unset>" ]]; then
				pass "$label: $svc leaves $key unset"
			else
				fail "$label: $svc sets $key='$value' (collections stay open, RF-21)"
			fi
		done
	done
}

resolve() { # label, out, compose args...
	local label="$1" out="$2"
	shift 2
	if compose "$@" config --format json >"$out" 2>"$out.err"; then
		return 0
	fi
	fail "$label: config exits non-zero: $(head -c 400 "$out.err")"
	return 1
}

resolve "prod compose" "$S/prod.json" -f docker-compose.unified.yml &&
	check "prod compose" "$S/prod.json" ckan ckan-worker
resolve "hs-node demo" "$S/demo.json" -f docker-compose.unified.yml -f docker-compose.hs.yml \
	--env-file .env.demo &&
	check "hs-node demo" "$S/demo.json" ckan ckan-worker
resolve "dev compose" "$S/dev.json" -f docker-compose.dev.unified.yml &&
	check "dev compose" "$S/dev.json" ckan-dev ckan-worker-dev

finish
