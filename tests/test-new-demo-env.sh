#!/usr/bin/env bash
#
# Guards `bin/new-demo-env`, the generator of the hs-node demo's `.env.demo`.
#
# Run from anywhere:  bash tests/test-new-demo-env.sh
#
# The demo's secrets follow the production checklist in
# odd/tasks/rotate-postgres-credentials.md (§ "Before production"), so what is
# enforced is that checklist, on files written to a scratch directory:
#
#   1. **Complete.** Every key of `demo.env.example` is written; every secret
#      is filled (only the SMTP password stays empty: the demo sends no mail),
#      and the file is mode 600.
#   2. **Fresh.** No value is a template default from
#      `ckan-docker/.env.example`, no two passwords are equal, and two runs
#      never share a secret.
#   3. **Consistent.** The URLs embed exactly the names and passwords the db
#      service creates its roles with; a mismatch only shows up as a CKAN that
#      cannot connect on the first boot. The DataStore read URL uses the
#      read-only role, never the writer.
#   4. **Silent.** Neither stdout nor stderr carries a generated value.
#   5. **No overwrite.** An existing file is refused and left byte-identical:
#      on a live odp-demo volume the roles keep their old passwords.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
GEN="$REPO_ROOT/bin/new-demo-env"
EXAMPLE="$REPO_ROOT/demo.env.example"

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

if [[ ! -x "$GEN" ]]; then
	fail "bin/new-demo-env does not exist or is not executable"
	finish
fi

S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT

val() { grep -E "^$2=" "$1" | head -n1 | cut -d= -f2-; }

"$GEN" "$S/a.env" >"$S/out" 2>"$S/err"
status=$?
if [[ "$status" -eq 0 ]]; then pass "exit 0 on a new path"; else fail "exit $status on a new path: $(head -c 300 "$S/err")"; finish; fi
if [[ "$(cat "$S/out")" == "wrote $S/a.env (mode 600); no value printed" && ! -s "$S/err" ]]; then
	pass "stdout names only the path; stderr is empty"
else
	fail "unexpected output: stdout '$(cat "$S/out")', stderr '$(cat "$S/err")'"
fi

# 1. Complete.
mode="$(stat -c '%a' "$S/a.env")"
[[ "$mode" == 600 ]] && pass "mode 600" || fail "mode is $mode, not 600"
mapfile -t KEYS < <(grep -oE '^[A-Z_][A-Z0-9_]*=' "$EXAMPLE" | tr -d '=')
for key in "${KEYS[@]}"; do
	if [[ "$(grep -cE "^$key=" "$S/a.env")" -ne 1 ]]; then
		fail "$key is not written exactly once"
	elif [[ "$key" != CKAN_SMTP_PASSWORD && -z "$(val "$S/a.env" "$key")" ]]; then
		fail "$key is empty"
	else
		pass "$key written"
	fi
done
[[ -z "$(val "$S/a.env" CKAN_SMTP_PASSWORD)" ]] && pass "CKAN_SMTP_PASSWORD stays empty (no mail)" || fail "CKAN_SMTP_PASSWORD was filled"
for key in DEMO_HOST POSTGRES_USER POSTGRES_DB CKAN_DB_USER CKAN_DB DATASTORE_READONLY_USER DATASTORE_DB; do
	[[ "$(val "$S/a.env" "$key")" == "$(val "$EXAMPLE" "$key")" ]] && pass "$key kept from the example" ||
		fail "$key changed: '$(val "$S/a.env" "$key")'"
done

# 2. Fresh.
SECRETS=(SESSION_SECRET POSTGRES_PASSWORD CKAN_DB_PASSWORD DATASTORE_READONLY_PASSWORD
	CKAN___BEAKER__SESSION__SECRET CKAN___API_TOKEN__JWT__ENCODE__SECRET CKAN_SYSADMIN_PASSWORD)
for key in "${SECRETS[@]}" CKAN_SQLALCHEMY_URL CKAN_DATASTORE_WRITE_URL CKAN_DATASTORE_READ_URL; do
	template="$(val "$REPO_ROOT/ckan-docker/.env.example" "$key")"
	if [[ -n "$template" && "$(val "$S/a.env" "$key")" == "$template" ]]; then
		fail "$key is the template default"
	else
		pass "$key is not the template default"
	fi
done
pw_unique="$(for k in POSTGRES_PASSWORD CKAN_DB_PASSWORD DATASTORE_READONLY_PASSWORD CKAN_SYSADMIN_PASSWORD; do val "$S/a.env" "$k"; done | sort -u | wc -l)"
[[ "$pw_unique" -eq 4 ]] && pass "the four passwords differ" || fail "only $pw_unique distinct passwords out of 4"
"$GEN" "$S/b.env" >/dev/null 2>&1
shared=""
for key in "${SECRETS[@]}"; do
	[[ "$(val "$S/a.env" "$key")" == "$(val "$S/b.env" "$key")" ]] && shared+="$key "
done
[[ -z "$shared" ]] && pass "two runs share no secret" || fail "two runs share: $shared"

# 3. Consistent.
a() { val "$S/a.env" "$1"; }
U="$(a CKAN_DB_USER)" P="$(a CKAN_DB_PASSWORD)" RU="$(a DATASTORE_READONLY_USER)" RP="$(a DATASTORE_READONLY_PASSWORD)"
for pair in \
	"CKAN_SQLALCHEMY_URL=postgresql://$U:$P@db/$(a CKAN_DB)" \
	"CKAN_DATASTORE_WRITE_URL=postgresql://$U:$P@db/$(a DATASTORE_DB)" \
	"CKAN_DATASTORE_READ_URL=postgresql://$RU:$RP@db/$(a DATASTORE_DB)" \
	"TEST_CKAN_SQLALCHEMY_URL=postgresql://$U:$P@db/ckan_test" \
	"TEST_CKAN_DATASTORE_WRITE_URL=postgresql://$U:$P@db/datastore_test" \
	"TEST_CKAN_DATASTORE_READ_URL=postgresql://$RU:$RP@db/datastore_test"; do
	key="${pair%%=*}"
	[[ "$(a "$key")" == "${pair#*=}" ]] && pass "$key matches the db roles" || fail "$key does not match the names and passwords the db creates"
done
[[ "$RU" != "$U" ]] && pass "the DataStore read role is not the writer" || fail "the read role is the writer"
jwt="$(a CKAN___API_TOKEN__JWT__ENCODE__SECRET)"
[[ "$jwt" == string:?* && "$jwt" == "$(a CKAN___API_TOKEN__JWT__DECODE__SECRET)" ]] &&
	pass "JWT encode == decode, with the string: prefix" || fail "JWT secrets must be one 'string:<secret>'"
bad_chars="$(for k in POSTGRES_PASSWORD CKAN_DB_PASSWORD DATASTORE_READONLY_PASSWORD; do a "$k"; done | grep -c '[^0-9a-f]')"
[[ "$bad_chars" -eq 0 ]] && pass "passwords are URL-safe (hex)" || fail "a password has characters that break a URL"

# 4. Silent.
leaked=""
for key in "${SECRETS[@]}"; do
	v="$(a "$key")"
	grep -qF -- "$v" "$S/out" "$S/err" && leaked+="$key "
done
[[ -z "$leaked" ]] && pass "no value reaches stdout or stderr" || fail "printed: $leaked"

# 5. No overwrite.
cp "$S/a.env" "$S/a.before"
"$GEN" "$S/a.env" >"$S/out" 2>"$S/err"
status=$?
if [[ "$status" -ne 0 ]] && cmp -s "$S/a.env" "$S/a.before" && [[ ! -s "$S/out" ]] &&
	[[ "$(cat "$S/err")" == "new-demo-env: $S/a.env already exists; not overwriting it (the db roles of an existing odp-demo volume keep the old passwords)" ]]; then
	pass "an existing file is refused (exit $status) and left unchanged"
else
	fail "overwrite: exit $status, stdout '$(cat "$S/out")', stderr '$(cat "$S/err")', unchanged: $(cmp -s "$S/a.env" "$S/a.before" && echo yes || echo no)"
fi
leftovers="$(find "$S" -name 'a.env.*' | wc -l)"
[[ "$leftovers" -eq 0 ]] && pass "no temporary file left behind" || fail "$leftovers temporary file(s) left"

finish
