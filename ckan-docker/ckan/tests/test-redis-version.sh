#!/usr/bin/env bash
#
# Guards the one coupling that has already bitten: CKAN 2.12's redis client sends HELLO
# (RESP3), and a redis below major 6 answers `unknown command 'HELLO'`. CKAN then logs
# "Could not connect to Redis" and runs the whole suite **without a cache, silently** --
# the failure is not an error, it is a quieter run that looks green.
#
# Run from anywhere:  bash ckan-docker/ckan/tests/test-redis-version.sh
#
# That requirement lives today as a comment in `.github/workflows/checks.yml`:
#
#     Must track `REDIS_VERSION` in the compose files. CKAN 2.12's redis client sends HELLO
#     (RESP3), which redis 3 answers with `unknown command 'HELLO'`: CKAN then logs
#     "Could not connect to Redis" and runs the whole suite without a cache, silently.
#
# A comment cannot fail, so this test makes its two enforceable halves fail:
#
#   1. **The floor.** The redis image `checks.yml` runs the suite against must be a
#      numeric major >= 6. It is a floor, not an equality: `redis:6`, `redis:6-alpine`
#      and `redis:7` all pass.
#   2. **The single home.** The compose files must keep the image as
#      `redis:${REDIS_VERSION}` rather than a literal, which is the route by which a wrong
#      version would bypass the one place the value lives.
#
# What this guard CANNOT see, stated rather than implied:
#
#   - **`REDIS_VERSION`'s value** lives in `ckan-docker/.env` / `.env.example`, and both
#     paths are blocked for reading and writing by the harness. This test therefore checks
#     the indirection and the CI literal, never that value: if the variable itself were set
#     to 3, only a human or a runtime probe would catch it.
#   - **`ckan-docker/src/ckanext-umss/.github/workflows/test.yml` says `redis:3`** and is
#     deliberately out of scope: it is the vendored upstream CI of the extension, for the
#     extension's own repository, and it never runs here (GitHub only runs root workflows).
#     The same file is excluded by `test-ckan-image-tag.sh` for the same reason.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The subject is this repository, always. Not overridable from the environment: the positive
# case is the one assertion that has to be about the real tree, and its sibling guard taught
# that an override thread makes it untrustworthy. Must-fail cases pass their own fixture root.
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"

# The major a RESP3 client needs. `HELLO` exists from 6.0, so the comparison is `>=`, not `=`.
REDIS_FLOOR=6

CI_FILE=".github/workflows/checks.yml"
COMPOSE_FILES=(
	ckan-docker/docker-compose.yml
	ckan-docker/docker-compose.dev.yml
)

# Every `image: redis:<tag>` in a file, one tag per line. Anchored on `image:` so a prose
# mention of redis in a comment is not read as the service definition -- and the tag is
# whatever follows the colon, so `${REDIS_VERSION}` comes out as itself.
redis_tags() { # file
	grep -oE 'image:[[:space:]]*redis:[^[:space:]]+' "$1" | sed -E 's/^image:[[:space:]]*redis://'
}

# The leading numeric major of a tag, or nothing when there is none. `6-alpine` -> 6, `7` -> 7,
# `${REDIS_VERSION}` -> nothing (which the floor check reports as unreadable).
major_of() { # tag
	printf '%s' "$1" | grep -oE '^[0-9]+' || true
}

# One line per file, with what was read. Printed on failure so a bump sees the whole picture.
print_inventory() { # root
	local root="$1" site file tag
	file="$root/$CI_FILE"
	if [ -f "$file" ]; then
		while IFS= read -r tag; do
			[ -n "$tag" ] || continue
			printf '  %s: %s\n' "$CI_FILE" "$tag"
		done < <(redis_tags "$file")
	fi
	for site in "${COMPOSE_FILES[@]}"; do
		file="$root/$site"
		if [ ! -f "$file" ]; then
			printf '  %s: MISSING\n' "$site"
			continue
		fi
		while IFS= read -r tag; do
			[ -n "$tag" ] || continue
			printf '  %s: %s\n' "$site" "$tag"
		done < <(redis_tags "$file")
	done
}

# The floor. Fails on a redis reference that is missing, unreadable or below major 6.
check_redis_floor() { # root
	local root="$1" file tag major offenders=0 seen=0
	file="$root/$CI_FILE"
	if [ ! -f "$file" ]; then
		printf '  %s: MISSING, this file is what runs the suite against redis\n' "$CI_FILE"
		return 1
	fi
	while IFS= read -r tag; do
		[ -n "$tag" ] || continue
		seen=$((seen + 1))
		major="$(major_of "$tag")"
		if [ -z "$major" ]; then
			# A variable is not a version this check can read, and its value is out of reach
			# anyway. Failing closed here is deliberate: an unreadable floor is not a proven one.
			printf '  %s: redis:%s, not a numeric major this check can read\n' "$CI_FILE" "$tag"
			offenders=$((offenders + 1))
		elif [ "$major" -lt "$REDIS_FLOOR" ]; then
			printf '  %s: redis:%s is below the RESP3 floor (major %s): CKAN 2.12 sends HELLO and\n' \
				"$CI_FILE" "$tag" "$REDIS_FLOOR"
			printf '    redis %s answers `unknown command '"'"'HELLO'"'"'`, so CKAN logs "Could not connect to\n' "$major"
			printf '    Redis" and runs the whole suite without a cache, silently.\n'
			offenders=$((offenders + 1))
		fi
	done < <(redis_tags "$file")
	if [ "$seen" -eq 0 ]; then
		printf '  %s: no redis image found: an empty extraction is not a pass\n' "$CI_FILE"
		print_inventory "$root"
		return 1
	fi
	if [ "$offenders" -gt 0 ]; then
		printf '  %s redis site(s) below the floor; every site measured:\n' "$offenders"
		print_inventory "$root"
		return 1
	fi
	printf '  ok: %s redis reference(s) in %s, all >= major %s\n' "$seen" "$CI_FILE" "$REDIS_FLOOR"
	return 0
}

# The single home. The compose files must point at `${REDIS_VERSION}`, not at a literal.
check_redis_indirection() { # root
	local root="$1" site file tag offenders=0 seen=0
	for site in "${COMPOSE_FILES[@]}"; do
		file="$root/$site"
		if [ ! -f "$file" ]; then
			printf '  %s: MISSING, this file is expected to define the redis service\n' "$site"
			offenders=$((offenders + 1))
			continue
		fi
		while IFS= read -r tag; do
			[ -n "$tag" ] || continue
			seen=$((seen + 1))
			if [ "$tag" != '${REDIS_VERSION}' ]; then
				printf '  %s: image is `redis:%s`, a literal; it must be `redis:${REDIS_VERSION}` so\n' \
					"$site" "$tag"
				printf '    the version keeps one home. A literal here bypasses the only place the\n'
				printf '    value is set, which is how the wrong one gets in.\n'
				offenders=$((offenders + 1))
			fi
		done < <(redis_tags "$file")
	done
	if [ "$seen" -eq 0 ]; then
		printf '  no redis image found in the compose files: an empty extraction is not a pass\n'
		print_inventory "$root"
		return 1
	fi
	if [ "$offenders" -gt 0 ]; then
		printf '  %s compose site(s) not using the variable; every site measured:\n' "$offenders"
		print_inventory "$root"
		return 1
	fi
	printf '  ok: %s compose site(s) point at \${REDIS_VERSION}\n' "$seen"
	return 0
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

failures=0
ok() { printf '  ok   %s\n' "$1"; }
notok() {
	printf '  FAIL %s\n' "$1"
	failures=$((failures + 1))
}
assert_eq() { # label expected actual
	if [ "$2" = "$3" ]; then ok "$1"; else notok "$1 (expected [$2], got [$3])"; fi
}
assert_contains() { # label haystack needle
	case "$2" in
	*"$3"*) ok "$1" ;;
	*) notok "$1 (missing [$3] in [$2])" ;;
	esac
}

CHECK_OUT=
CHECK_STATUS=
run_check() { # function root
	CHECK_OUT="$("$1" "$2" 2>&1)"
	CHECK_STATUS=$?
	while IFS= read -r line; do printf '     %s\n' "$line"; done <<<"$CHECK_OUT"
}

# A fixture tree shaped like the real sites. `$2` is the redis tag in the CI file, `$3` the
# one in the compose files, so a case can plant either violation without touching the repo.
make_tree() { # dir ci_tag compose_tag
	local dir="$1" ci_tag="$2" compose_tag="$3"
	mkdir -p "$dir/ckan-docker" "$dir/.github/workflows"
	cat > "$dir/$CI_FILE" <<EOF
container:
  image: ckan/ckan-dev:2.12
services:
  redis:
    image: redis:$ci_tag
EOF
	for site in "${COMPOSE_FILES[@]}"; do
		printf 'services:\n  redis:\n    image: redis:%s\n' "$compose_tag" > "$dir/$site"
	done
}

echo "the repository's redis sites hold the floor and the single home"
run_check check_redis_floor "$REPO_ROOT"
assert_eq "the CI redis is at or above the floor" "0" "$CHECK_STATUS"
run_check check_redis_indirection "$REPO_ROOT"
assert_eq "the compose files use \${REDIS_VERSION}" "0" "$CHECK_STATUS"
assert_contains "the CI tag is read as a version" "$(redis_tags "$REPO_ROOT/$CI_FILE")" "6"
assert_contains "the compose tag is read as the variable" \
	"$(redis_tags "$REPO_ROOT/${COMPOSE_FILES[0]}")" '${REDIS_VERSION}'

echo "a planted redis below the floor fails and names the tag"
make_tree "$WORK/old" "3" '${REDIS_VERSION}'
run_check check_redis_floor "$WORK/old"
assert_eq "the floor rejects redis:3" "1" "$CHECK_STATUS"
assert_contains "the offending tag is named" "$CHECK_OUT" "redis:3 is below the RESP3 floor"
assert_contains "the symptom is named" "$CHECK_OUT" "without a cache, silently"

echo "the floor is a floor, not an equality"
for tag in 6 6-alpine 7 7.2-alpine; do
	make_tree "$WORK/at-$tag" "$tag" '${REDIS_VERSION}'
	run_check check_redis_floor "$WORK/at-$tag"
	assert_eq "redis:$tag passes" "0" "$CHECK_STATUS"
done

echo "a compose literal fails, because it bypasses the variable"
make_tree "$WORK/literal" "6" "6"
run_check check_redis_indirection "$WORK/literal"
assert_eq "the indirection rule rejects a literal" "1" "$CHECK_STATUS"
assert_contains "the literal is named" "$CHECK_OUT" 'image is `redis:6`, a literal'
assert_contains "the fix is named" "$CHECK_OUT" 'redis:${REDIS_VERSION}'

echo "a CI tag that is a variable cannot be read, and fails closed"
make_tree "$WORK/variable" '${REDIS_VERSION}' '${REDIS_VERSION}'
run_check check_redis_floor "$WORK/variable"
assert_eq "the floor rejects an unreadable tag" "1" "$CHECK_STATUS"
assert_contains "the unreadable tag is reported as such" "$CHECK_OUT" \
	"not a numeric major this check can read"

echo "an empty extraction is not a pass"
make_tree "$WORK/empty" "6" '${REDIS_VERSION}'
printf 'services:\n  web:\n    image: nginx:alpine\n' > "$WORK/empty/$CI_FILE"
run_check check_redis_floor "$WORK/empty"
assert_eq "the floor rejects a file with no redis" "1" "$CHECK_STATUS"
assert_contains "the empty extraction is reported as such" "$CHECK_OUT" \
	"an empty extraction is not a pass"

echo "a missing file fails instead of shrinking the set"
run_check check_redis_indirection "$WORK/no-such-tree"
assert_eq "the indirection rule rejects a missing tree" "1" "$CHECK_STATUS"
assert_contains "the missing file is named" "$CHECK_OUT" "MISSING, this file is expected"

echo
if [ "$failures" -eq 0 ]; then
	echo "all tests passed"
	exit 0
fi
echo "$failures test(s) failed"
exit 1
