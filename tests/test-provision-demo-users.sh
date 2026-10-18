#!/usr/bin/env bash
#
# Guards `bin/provision-demo-users`, which creates the hs-node demo's users and
# their organization roles through the CKAN action API.
#
# Run from anywhere:  bash tests/test-provision-demo-users.sh
#
# Never talks to a real CKAN: a stub `curl` first on PATH is a fake CKAN that
# keeps its state in files of a scratch directory and logs each call's action
# and full argv. What is enforced:
#
#   1. **Preflight before any write.** Without CKAN_URL or CKAN_API_TOKEN: exit
#      2 and a usage message. A missing organization (the portal seed creates
#      them) makes the exit 1 with the exact message, and not a single write
#      action (user_create, organization_member_create) is logged.
#   2. **Create missing users** with a fresh password, appended as
#      `<name> <password>` to the credentials file (mode 600). An existing user
#      is not recreated and gets no line: its password is not ours to change.
#   3. **Ensure the role** of every user, new or existing, in the right org.
#   4. **stdout** is one exact line per user, in order, and nothing else.
#   5. **Secrets never leak.** Neither the token nor a password appears in
#      stdout, stderr or the argv of any curl call (they go via stdin/@file).
#   6. **CKAN errors.** `success: false` or a non-2xx exits 1, names the action
#      and CKAN's message; lines of users created before stay in the file.
#   7. **Rerun is a no-op.** Every user says `exists`, no user_create is made,
#      and the credentials file is byte-identical.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
PROV="$REPO_ROOT/bin/provision-demo-users"

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

if [[ ! -x "$PROV" ]]; then
	fail "bin/provision-demo-users does not exist or is not executable"
	finish
fi

S="$(mktemp -d)"
trap 'rm -rf "$S"' EXIT

TOKEN="tok-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"

# The fake CKAN. State: $STUB_DIR/users/<name>, $STUB_DIR/members (org user role).
mkdir "$S/bin"
cat >"$S/bin/curl" <<'STUB'
#!/usr/bin/env bash
url="" body="" auth=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
	a="${args[i]}"
	case "$a" in
	http*) url="$a" ;;
	--data-binary) [[ "${args[i + 1]}" == "@-" ]] && body="$(cat)" ;;
	-H) [[ "${args[i + 1]}" == @* ]] && auth="$(cat "${args[i + 1]#@}")" ;;
	esac
done
action="${url##*/}"
printf '%s %s\n' "$action" "$*" >>"$STUB_DIR/calls"
mkdir -p "$STUB_DIR/users"
reply() { printf '%s\n%s' "$1" "$2"; exit 0; }
err() { reply "{\"success\":false,\"error\":{\"__type\":\"$3\",\"message\":\"$2\"}}" "$1"; }
[[ "$auth" == "Authorization: $STUB_TOKEN" ]] || err 403 "Access denied" "Authorization Error"
get() { jq -r ".$1 // empty" <<<"$body"; }
case "$action" in
user_show)
	n="$(get id)"
	[[ -e "$STUB_DIR/users/$n" ]] || err 404 "Not found: User was not found." "Not Found Error"
	reply "{\"success\":true,\"result\":{\"name\":\"$n\"}}" 200 ;;
user_create)
	n="$(get name)"
	[[ "$n" == "$STUB_FAIL_USER_CREATE" ]] && err 500 "boom creating $n" "Internal Error"
	[[ -e "$STUB_DIR/users/$n" ]] && err 409 "That login name is not available." "Validation Error"
	[[ -n "$(get password)" ]] || err 409 "password: Missing value" "Validation Error"
	: >"$STUB_DIR/users/$n"
	reply "{\"success\":true,\"result\":{\"name\":\"$n\"}}" 200 ;;
organization_show)
	n="$(get id)"
	[[ " $STUB_ORGS " == *" $n "* ]] || err 404 "Not found: Group was not found." "Not Found Error"
	reply "{\"success\":true,\"result\":{\"name\":\"$n\"}}" 200 ;;
organization_member_create)
	o="$(get id)" u="$(get username)" r="$(get role)"
	[[ " $STUB_ORGS " == *" $o "* ]] || err 404 "Not found: Group was not found." "Not Found Error"
	[[ -e "$STUB_DIR/users/$u" ]] || err 404 "Not found: User was not found." "Not Found Error"
	{ grep -v "^$o $u " "$STUB_DIR/members" 2>/dev/null; echo "$o $u $r"; } >"$STUB_DIR/members.new"
	mv "$STUB_DIR/members.new" "$STUB_DIR/members"
	reply "{\"success\":true,\"result\":{\"capacity\":\"$r\"}}" 200 ;;
*) err 400 "unknown action $action" "Bad Request" ;;
esac
STUB
chmod +x "$S/bin/curl"

# jq and openssl run the real binaries, but record their argv: a password passed
# as a jq --arg is just as visible in ps as one passed to curl.
# STUB_OPENSSL_FAIL=1 makes openssl fail.
for tool in jq openssl; do
	real="$(command -v "$tool")"
	cat >"$S/bin/$tool" <<WRAP
#!/usr/bin/env bash
printf '%s %s\n' "$tool" "\$*" >>"\$STUB_DIR/argv"
[[ "$tool" == openssl && -n "\$STUB_OPENSSL_FAIL" ]] && exit 1
exec "$real" "\$@"
WRAP
	chmod +x "$S/bin/$tool"
done

# new_world <dir>: a fresh fake CKAN with both orgs.
new_world() {
	rm -rf "$1"
	mkdir -p "$1"
	: >"$1/calls"
	: >"$1/argv"
}
run() { # run <world> <creds> [env...]: stdout/stderr/status into <world>/{out,err,status}
	local w="$1" creds="$2"
	shift 2
	env PATH="$S/bin:$PATH" STUB_DIR="$w" STUB_TOKEN="$TOKEN" STUB_ORGS="fcyt fcs" STUB_FAIL_USER_CREATE="" \
		CKAN_URL="http://ckan.test" CKAN_API_TOKEN="$TOKEN" "$@" \
		"$PROV" "$creds" >"$w/out" 2>"$w/err"
	echo $? >"$w/status"
}
writes() { grep -cE '^(user_create|organization_member_create) ' "$1/calls"; }

# 1. Preflight.
W="$S/w1"
new_world "$W"
run "$W" "$S/c1" CKAN_URL=
if [[ "$(cat "$W/status")" == 2 && "$(cat "$W/err")" == *"Usage:"* && ! -s "$W/out" && "$(writes "$W")" == 0 ]]; then
	pass "missing CKAN_URL: exit 2, usage on stderr"
else fail "missing CKAN_URL: status $(cat "$W/status"), stderr '$(cat "$W/err")'"; fi
new_world "$W"
run "$W" "$S/c1" CKAN_API_TOKEN=
if [[ "$(cat "$W/status")" == 2 && "$(cat "$W/err")" == *"Usage:"* && ! -s "$W/out" && "$(writes "$W")" == 0 ]]; then
	pass "missing CKAN_API_TOKEN: exit 2, usage on stderr"
else fail "missing CKAN_API_TOKEN: status $(cat "$W/status"), stderr '$(cat "$W/err")'"; fi

for missing in fcs fcyt; do
	new_world "$W"
	rm -f "$S/c1"
	[[ "$missing" == fcs ]] && present=fcyt || present=fcs
	run "$W" "$S/c1" STUB_ORGS="$present"
	want="provision-demo-users: organization $missing does not exist; run the portal seed first"
	if [[ "$(cat "$W/status")" == 1 && "$(cat "$W/err")" == "$want" && ! -s "$W/out" ]]; then
		pass "missing org $missing: exit 1 and the exact message"
	else fail "missing org $missing: status $(cat "$W/status"), stderr '$(cat "$W/err")'"; fi
	if [[ "$(writes "$W")" == 0 && ! -e "$S/c1" && ! -e "$W/members" && -z "$(ls "$W/users" 2>/dev/null)" ]]; then
		pass "missing org $missing: no write call, no user, no credentials file"
	else fail "missing org $missing: a write happened ($(writes "$W") write calls)"; fi
done

# 2-5. First run on an empty CKAN.
W="$S/w2"
C="$S/c2"
new_world "$W"
run "$W" "$C"
if [[ "$(cat "$W/status")" == 0 ]]; then pass "first run exits 0"; else fail "first run exit $(cat "$W/status"): $(head -c 300 "$W/err")"; fi
want_out='created admin-fcyt (admin of fcyt)
created editor-fcyt (editor of fcyt)
created miembro-fcyt (member of fcyt)
created editor-fcs (editor of fcs)'
[[ "$(cat "$W/out")" == "$want_out" ]] && pass "stdout: one exact line per user, in order" || fail "stdout was: $(cat "$W/out")"
[[ ! -s "$W/err" ]] && pass "stderr empty on success" || fail "stderr: $(cat "$W/err")"
[[ "$(stat -c '%a' "$C" 2>/dev/null)" == 600 ]] && pass "credentials file is mode 600" || fail "credentials mode '$(stat -c '%a' "$C" 2>/dev/null)'"
if [[ "$(cut -d' ' -f1 "$C" | paste -sd,)" == "admin-fcyt,editor-fcyt,miembro-fcyt,editor-fcs" && "$(wc -l <"$C")" == 4 ]] &&
	! grep -vqE '^[a-z-]+ [0-9a-f]{32}$' "$C"; then
	pass "credentials: '<name> <32 hex>' per created user, in order"
else fail "credentials file is malformed"; fi
[[ "$(cut -d' ' -f2 "$C" | sort -u | wc -l)" == 4 ]] && pass "the four passwords differ" || fail "passwords repeat"
want_members='fcyt admin-fcyt admin
fcyt editor-fcyt editor
fcyt miembro-fcyt member
fcs editor-fcs editor'
[[ "$(sort "$W/members")" == "$(sort <<<"$want_members")" ]] && pass "every role ensured in the right org" || fail "members: $(cat "$W/members")"
if [[ "$(grep -c '^user_create ' "$W/calls")" == 4 && "$(grep -c '^organization_member_create ' "$W/calls")" == 4 ]]; then
	pass "four user_create and four organization_member_create"
else fail "unexpected call counts"; fi
leak=""
while read -r _ pw; do
	grep -qF "$pw" "$W/out" "$W/err" "$W/calls" && leak+="password "
	grep -qF "$pw" "$W/argv" && leak+="password-in-jq/openssl-argv "
done <"$C"
grep -qF "$TOKEN" "$W/out" "$W/err" "$W/calls" "$W/argv" && leak+="token "
[[ -z "$leak" ]] && pass "no token or password in stdout, stderr or any curl, jq or openssl argv" || fail "leaked: $leak"
[[ -s "$W/argv" ]] && pass "the jq/openssl argv recorder saw calls" || fail "the jq/openssl argv recorder saw nothing"

# openssl failing: no user is created with an empty password.
W_SSL="$S/w-ssl"
new_world "$W_SSL"
run "$W_SSL" "$S/c-ssl" STUB_OPENSSL_FAIL=1
if [[ "$(cat "$W_SSL/status")" == 1 && "$(cat "$W_SSL/err")" == "provision-demo-users: openssl failed" &&
	"$(grep -c '^user_create ' "$W_SSL/calls")" == 0 && ! -e "$S/c-ssl" ]]; then
	pass "openssl failing: exit 1, 'openssl failed', no user_create, no credentials file"
else
	fail "openssl failing: status $(cat "$W_SSL/status"), stderr '$(cat "$W_SSL/err")', user_create $(grep -c '^user_create ' "$W_SSL/calls")"
fi
grep -q "Authorization" "$W/calls" && fail "an Authorization header is in argv"
grep -q -- '--data-binary @-' "$W/calls" && pass "bodies go through stdin" || fail "bodies are not sent via --data-binary @-"

# 7. Rerun is a no-op.
cp "$C" "$S/c2.before"
: >"$W/calls"
run "$W" "$C"
want_out='exists admin-fcyt; admin of fcyt ensured
exists editor-fcyt; editor of fcyt ensured
exists miembro-fcyt; member of fcyt ensured
exists editor-fcs; editor of fcs ensured'
if [[ "$(cat "$W/status")" == 0 && "$(cat "$W/out")" == "$want_out" && ! -s "$W/err" ]]; then
	pass "rerun: exit 0 and four 'exists' lines"
else fail "rerun: status $(cat "$W/status"), stdout '$(cat "$W/out")', stderr '$(cat "$W/err")'"; fi
[[ "$(grep -c '^user_create ' "$W/calls")" == 0 ]] && pass "rerun: no user_create" || fail "rerun called user_create"
[[ "$(grep -c '^organization_member_create ' "$W/calls")" == 4 ]] && pass "rerun: roles ensured again" || fail "rerun role calls"
cmp -s "$C" "$S/c2.before" && [[ "$(stat -c '%a' "$C")" == 600 ]] && pass "rerun: credentials file byte-identical, mode 600" || fail "rerun changed the credentials file"

# A user that exists beforehand: no line, no password change, role still ensured.
W="$S/w3"
C="$S/c3"
new_world "$W"
mkdir "$W/users"
: >"$W/users/editor-fcyt"
run "$W" "$C"
want_out='created admin-fcyt (admin of fcyt)
exists editor-fcyt; editor of fcyt ensured
created miembro-fcyt (member of fcyt)
created editor-fcs (editor of fcs)'
[[ "$(cat "$W/out")" == "$want_out" ]] && pass "pre-existing user: 'exists' line among 'created'" || fail "stdout was: $(cat "$W/out")"
if [[ "$(wc -l <"$C")" == 3 ]] && ! grep -q '^editor-fcyt ' "$C" && ! grep -q 'user_create .*editor-fcyt' "$W/calls" &&
	[[ "$(grep -c '^user_create ' "$W/calls")" == 3 ]] && grep -q '^fcyt editor-fcyt editor$' "$W/members"; then
	pass "pre-existing user: not recreated, no credentials line, role ensured"
else fail "pre-existing user mishandled"; fi

# 6. CKAN errors: the second user_create fails.
W="$S/w4"
C="$S/c4"
new_world "$W"
run "$W" "$C" STUB_FAIL_USER_CREATE=editor-fcyt
want_err='provision-demo-users: user_create failed: boom creating editor-fcyt'
if [[ "$(cat "$W/status")" == 1 && "$(cat "$W/err")" == "$want_err" && "$(cat "$W/out")" == "created admin-fcyt (admin of fcyt)" ]]; then
	pass "CKAN error: exit 1, stderr names the action and CKAN's message, earlier line kept on stdout"
else fail "CKAN error: status $(cat "$W/status"), stderr '$(cat "$W/err")', stdout '$(cat "$W/out")'"; fi
if [[ "$(cut -d' ' -f1 "$C")" == "admin-fcyt" && "$(stat -c '%a' "$C")" == 600 ]]; then
	pass "CKAN error: the earlier created user's line stays in the file"
else fail "credentials after the error: '$(cat "$C" 2>/dev/null | cut -d' ' -f1)'"; fi
pw="$(cut -d' ' -f2 "$C")"
grep -qF "$pw" "$W/err" "$W/out" && fail "password leaked on error" || pass "CKAN error: no secret in output"
# A rejected token (403) is an error too, without echoing the token.
new_world "$W"
run "$W" "$S/c5" CKAN_API_TOKEN=wrong-token-value
if [[ "$(cat "$W/status")" == 1 && "$(cat "$W/err")" == "provision-demo-users: organization_show failed: Access denied" ]] &&
	! grep -q wrong-token-value "$W/err" "$W/out"; then
	pass "403: exit 1, action and message, token not echoed"
else fail "403: status $(cat "$W/status"), stderr '$(cat "$W/err")'"; fi

# Default credentials file is <repo>/.demo-users and is git-ignored.
git -C "$REPO_ROOT" check-ignore -q .demo-users && pass ".demo-users is git-ignored" || fail ".demo-users is not git-ignored"

finish
