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

# Every live `image: redis:<tag>` in a file, one tag per line. Anchored at the start of the
# line, leading indentation allowed, so a **commented** `# image: redis:3` is not read as a
# service definition: the extraction is looking for a mapping key, and a comment is not one.
# That anchoring is load-bearing -- measured, a stale comment about redis 3 used to trip the
# floor on a tree whose live tag was fine. The tag is whatever follows the colon, so
# `${REDIS_VERSION}` comes out as itself.
redis_tags() { # file
	grep -oE '^[[:space:]]*image:[[:space:]]*redis:[^[:space:]]+' "$1" \
		| sed -E 's/^[[:space:]]*image:[[:space:]]*redis://'
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
	local root="$1" site file tag offenders=0 seen=0 site_seen
	for site in "${COMPOSE_FILES[@]}"; do
		file="$root/$site"
		if [ ! -f "$file" ]; then
			printf '  %s: MISSING, this file is expected to define the redis service\n' "$site"
			offenders=$((offenders + 1))
			continue
		fi
		site_seen=0
		while IFS= read -r tag; do
			[ -n "$tag" ] || continue
			site_seen=$((site_seen + 1))
			seen=$((seen + 1))
			if [ "$tag" != '${REDIS_VERSION}' ]; then
				printf '  %s: image is `redis:%s`, a literal; it must be `redis:${REDIS_VERSION}` so\n' \
					"$site" "$tag"
				printf '    the version keeps one home. A literal here bypasses the only place the\n'
				printf '    value is set, which is how the wrong one gets in.\n'
				offenders=$((offenders + 1))
			fi
		done < <(redis_tags "$file")
		if [ "$site_seen" -eq 0 ]; then
			# Per file, not in total. With one count across both files, the file that lost its
			# redis service is covered for by its sibling -- and this rule would be certifying an
			# indirection it never read in that file, which is the drift it exists to catch.
			printf '  %s: no redis image in this file, so the single home cannot be verified here\n' \
				"$site"
			offenders=$((offenders + 1))
		fi
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
# A fixture root that is not a directory turns every path below into an absolute one.
# Measured, with a failing `mktemp` the fixtures targeted `/old`, `/at-6`, ...: the suite then
# failed in cascade blaming its own assertions, and the cause appeared only on stderr. Fail
# closed here, while the cause is still nameable, instead of measuring something else.
if [ -z "$WORK" ] || [ ! -d "$WORK" ]; then
	printf '  FAIL mktemp -d gave no usable directory (got [%s]): the fixtures would land on\n' "$WORK"
	printf '       absolute paths and the suite would not be measuring what it prints\n'
	exit 1
fi
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

# The positive case has to be about the real tree, so this takes its root instead of reading a
# fixture. It deliberately does NOT pin the tag the file happens to hold today: measured, the
# pinned literal `6` made a legitimate bump to `redis:7` fail with `missing [6]`, which trains a
# maintainer to distrust a correct change. The bump and below-the-floor cases below are what
# keep this from collapsing into "any tag passes".
CI_TAG_STATE=
CI_TAG_READ=
read_ci_tag() { # root
	local tag major
	tag="$(redis_tags "$1/$CI_FILE")"
	CI_TAG_READ="redis:$tag"
	if [ -z "$tag" ]; then
		CI_TAG_STATE=1
		CI_TAG_READ="$CI_TAG_READ (no site read at all)"
		return
	fi
	major="$(major_of "$tag")"
	if [ -z "$major" ]; then
		CI_TAG_STATE=1
		CI_TAG_READ="$CI_TAG_READ (not a numeric major this check can read)"
	elif [ "$major" -lt "$REDIS_FLOOR" ]; then
		CI_TAG_STATE=1
		CI_TAG_READ="$CI_TAG_READ (below the floor of major $REDIS_FLOOR)"
	else
		CI_TAG_STATE=0
		CI_TAG_READ="$CI_TAG_READ (major $major >= $REDIS_FLOOR)"
	fi
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
read_ci_tag "$REPO_ROOT"
assert_eq "the CI tag is read as a version: $CI_TAG_READ" "0" "$CI_TAG_STATE"
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

echo "the CI tag is read as a major, not as the literal it holds today"
make_tree "$WORK/bumped" "7" '${REDIS_VERSION}'
read_ci_tag "$WORK/bumped"
assert_eq "a legitimate bump to a newer major stays green: $CI_TAG_READ" "0" "$CI_TAG_STATE"
make_tree "$WORK/below" "3" '${REDIS_VERSION}'
read_ci_tag "$WORK/below"
assert_eq "a tag below the floor is still rejected: $CI_TAG_READ" "1" "$CI_TAG_STATE"

echo "a commented redis line is not a site"
make_tree "$WORK/commented" "6" '${REDIS_VERSION}'
cat > "$WORK/commented/$CI_FILE" <<'EOF'
container:
  image: ckan/ckan-dev:2.12
services:
  redis:
    # image: redis:3, kept for reference: a comment cannot run
    image: redis:6
EOF
run_check check_redis_floor "$WORK/commented"
assert_eq "a commented tag does not trip the floor" "0" "$CHECK_STATUS"
assert_contains "only the live site is counted" "$CHECK_OUT" "1 redis reference(s)"

echo "a compose literal fails, because it bypasses the variable"
make_tree "$WORK/literal" "6" "6"
run_check check_redis_indirection "$WORK/literal"
assert_eq "the indirection rule rejects a literal" "1" "$CHECK_STATUS"
assert_contains "the literal is named" "$CHECK_OUT" 'image is `redis:6`, a literal'
assert_contains "the fix is named" "$CHECK_OUT" 'redis:${REDIS_VERSION}'

echo "a compose file that lost its redis service fails on its own"
make_tree "$WORK/lost" "6" '${REDIS_VERSION}'
# The sibling still points at the variable: with one count across both files, this one is
# covered for by its neighbour and the drift stays invisible, which is the whole point.
printf 'services:\n  web:\n    image: nginx:alpine\n' > "$WORK/lost/${COMPOSE_FILES[1]}"
run_check check_redis_indirection "$WORK/lost"
assert_eq "the indirection rule rejects a compose without redis" "1" "$CHECK_STATUS"
assert_contains "the file that lost the service is named" "$CHECK_OUT" \
	"${COMPOSE_FILES[1]}: no redis image"

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
