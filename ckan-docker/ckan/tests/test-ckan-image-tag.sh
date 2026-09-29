#!/usr/bin/env bash
#
# Guards the one thing the CKAN version literal cannot guard by itself: every
# place that names a CKAN image must name the SAME release tag.
#
# Run from anywhere:  bash ckan-docker/ckan/tests/test-ckan-image-tag.sh
#
# Why the invariant exists (R2-002): the stack's CKAN release tag is written as a
# `FROM` in four Dockerfiles plus the `umss-tests` container in
# `.github/workflows/checks.yml`, and nothing derives one from another.
# `Dockerfile`/`Dockerfile.umss` extend `ckan/ckan-base` while their `.dev`
# siblings extend `ckan/ckan-dev` (a different upstream image), so an `ARG` in
# `FROM` would NOT remove the duplication -- it would leave one literal default
# per image -- and the workflow's `container:` is resolved by the runner, so it
# cannot share a Dockerfile `ARG` at all. Measured on the 2.11 -> 2.12 bump:
# every site was edited by hand. The cost of missing one is silent and confusing
# rather than loud: a prod image on 2.12 next to a dev image or test container on
# 2.11, discovered as a behaviour difference. So what is missing is not a
# variable, it is that disagreement FAILS.
#
# The comparison runs through `check_ckan_tag <root>`, and every must-fail case
# below runs against a fixture tree in `mktemp -d`, so no tracked file is mutated.
# The repository case always reads THIS repository: the root is derived from
# `BASH_SOURCE` and is deliberately not overridable from the environment, so the
# positive case cannot silently end up checking something else.
#
# The declared sites, and the ones deliberately left out:
#
#   ckan-docker/ckan/Dockerfile        `FROM ckan/ckan-base:<tag>`
#   ckan-docker/ckan/Dockerfile.dev    `FROM ckan/ckan-dev:<tag>`
#   ckan-docker/Dockerfile.umss        `FROM ckan/ckan-base:<tag>` (+ a comment
#                                      carrying the same literal, see below)
#   ckan-docker/Dockerfile.dev.umss    `FROM ckan/ckan-dev:<tag>`
#   .github/workflows/checks.yml       `image: ckan/ckan-dev:<tag>`
#
#   - `ckan-docker/nginx/Dockerfile` and `ckan-docker/postgresql/Dockerfile` are
#     the only other `ckan-docker/**/Dockerfile*`; both use their own upstream
#     (`nginx:stable-alpine`, `postgres:16-alpine`) and name no CKAN image.
#   - The `solr`, `postgres` and `redis` service images in `checks.yml`
#     (`ckan/ckan-solr:<tag>-solr9`, `ckan/ckan-postgres-dev:<tag>`, `redis:6`)
#     are NOT CKAN. Their tags embed the CKAN version on purpose, which is
#     exactly why the extraction anchors on `ckan/ckan-base:`/`ckan/ckan-dev:`
#     instead of matching `ckan/ckan*`: demanding that they share CKAN's tag would
#     fail on a tag shape this repository does not own (`-solr9`) and on images
#     whose upstream release lags CKAN's. The case "service images are not CKAN"
#     below proves they are excluded rather than assumed excluded.
#   - The `ckan/ckan-base-datapusher:<v>` images in `docker-compose*.yml` are the
#     datapusher, versioned separately through `DATAPUSHER_VERSION`.
#   - `ckan-docker/src/ckanext-umss/.github/workflows/test.yml` names
#     `ckan/ckan-dev:2.11`. It is the vendored extension's own upstream CI, for
#     the extension's repository, not the image this stack builds or runs; this
#     repository's mirror of that job is `checks.yml`, which IS a site. Bumping
#     the vendored file is an upstream sync, not a tag drift in this stack, so the
#     guard must not fail because upstream lags.
#   - `README.md` carries `FROM ckan/ckan-base:2.9.7-dev` and `<new version>`
#     examples: they illustrate HOW to change the base image. Out of scope by
#     decision, not by accident.
#
# The guard reads raw text, comments included. That matters here: the measured
# tree has a second CKAN literal in the `# Extends ckan/ckan-base:2.12 with:`
# comment of `Dockerfile.umss`, so a bump that edits the `FROM` and forgets the
# comment is caught instead of passing. What is NOT read as a reference is a
# `ckan/ckan-*` that forms part of a longer path (a URL, a fork registry):
# `ckan_image_refs` below carries the boundary and the measured counterexamples.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The subject of the check: this repository, always. Not overridable from the
# environment, because the positive case below is the one assertion that has to
# be about the real tree; each must-fail case passes its own fixture root instead.
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"

# Declared, not discovered. A discovery glob would pass on a tree where a site was
# renamed or its `FROM` deleted, because the set being compared would simply
# shrink; every site below must name a CKAN image or the check fails.
CKAN_SITES=(
	ckan-docker/ckan/Dockerfile
	ckan-docker/ckan/Dockerfile.dev
	ckan-docker/Dockerfile.umss
	ckan-docker/Dockerfile.dev.umss
	.github/workflows/checks.yml
)

# Every CKAN image reference in a file, one per line. Both boundaries were
# measured, not assumed:
#
#   * `(:|@)` right after the repository name separates CKAN from its
#     lookalikes: `ckan/ckan-base-datapusher:`, `ckan/ckan-solr:` and
#     `ckan/ckan-postgres-dev:` never match.
#   * The `(?<!...)` lookbehind rejects a reference that forms part of a longer
#     path. Measured counterexamples an unanchored pattern takes as CKAN:
#     `xckan/ckan-dev:1.0` (a different Docker Hub namespace) and a comment
#     holding `https://github.com/ckan/ckan-base:2.12` (a URL, not an image this
#     stack builds). The same rule rejects a fork on a private registry
#     (`registry.example.com/ckan/ckan-base:2.12`), and that is deliberate: it
#     fails closed through the "names CKAN, but not as a literal tag" branch
#     below, which is where a fork has to make a conscious decision, instead of
#     being compared as if it were the upstream image.
#
# `grep -P` carries the lookbehind; it is GNU grep with PCRE, present on the CI
# runner and in this repository's own environment (measured: GNU grep 3.12).
ckan_image_refs() { # file
	grep -oP '(?<![A-Za-z0-9._/-])ckan/ckan-(base|dev)(:[A-Za-z0-9_.-]+|@[^[:space:]]+)' "$1"
}

# The tag a reference carries. A digest pin (`ckan/ckan-base@sha256:...`) cannot be
# compared against a tag, so it is reported as the reference itself and rejected.
tag_of_ref() { # ref
	case "$1" in
	*@*) printf '%s' "$1" ;;
	*) printf '%s' "${1##*:}" ;;
	esac
}

# Every declared site and the tag it holds. Printed when the check fails, so a bump
# sees the whole picture rather than only the files that disagree with the first.
print_inventory() { # root
	local root="$1" site file ref
	for site in "${CKAN_SITES[@]}"; do
		file="$root/$site"
		if [ ! -f "$file" ]; then
			printf '  %s: MISSING\n' "$site"
			continue
		fi
		while IFS= read -r ref; do
			[ -n "$ref" ] || continue
			printf '  %s: %s\n' "$site" "$(tag_of_ref "$ref")"
		done < <(ckan_image_refs "$file")
	done
}

# Prints one line per offending file, naming the file and the tag it holds, and
# returns non-zero on any disagreement or on any site that stopped naming CKAN.
# Returns 0 only when every site names CKAN and all of them carry one tag.
check_ckan_tag() { # [root]
	local root="${1:-$REPO_ROOT}"
	local site file ref tag expected="" expected_from="" seen=0 offenders=0
	local digest="" tags_same=1 taglist=""

	local -a tags=()
	for site in "${CKAN_SITES[@]}"; do
		file="$root/$site"
		if [ ! -f "$file" ]; then
			printf '  %s: MISSING, this site is expected to name CKAN\n' "$site"
			offenders=$((offenders + 1))
			continue
		fi
		# Read this file's references before judging them: telling "one file
		# carrying two tags" (a bump that missed a comment inside it) apart from
		# "this file disagrees with another one" needs the whole file.
		# Process substitution, not a pipe: `seen`, `tags` and `expected` live in
		# this function's scope and a pipe would hide every update in a subshell.
		tags=()
		digest=""
		while IFS= read -r ref; do
			[ -n "$ref" ] || continue
			seen=$((seen + 1))
			case "$ref" in
			*@*) digest="$ref" ;;
			*) tags+=("$(tag_of_ref "$ref")") ;;
			esac
		done < <(ckan_image_refs "$file")
		if [ "${#tags[@]}" -eq 0 ] && [ -z "$digest" ]; then
			# Fail closed on both shapes: a site that no longer mentions CKAN at
			# all, and a site that mentions it through something the check cannot
			# read as a literal tag (an `ARG`, a variable).
			# Case-insensitive on purpose: an uppercase `CKAN/CKAN-BASE:2.12` still
			# names CKAN, and calling it "no longer names" would send a maintainer
			# after the wrong problem.
			if grep -qiE 'ckan/ckan-(base|dev)([[:space:]]|$|[:@])' "$file"; then
				printf '  %s: names CKAN, but not as a literal tag the check can read\n' "$site"
			else
				printf '  %s: no longer names ckan/ckan-base or ckan/ckan-dev\n' "$site"
			fi
			offenders=$((offenders + 1))
			continue
		fi
		if [ -n "$digest" ]; then
			# A digest cannot be compared against the tags the other sites carry,
			# and skipping it would quietly narrow what the check covers.
			printf '  %s: %s, a digest pin cannot be compared against a tag\n' "$site" "$digest"
			offenders=$((offenders + 1))
			continue
		fi
		tags_same=1
		taglist=""
		for tag in "${tags[@]}"; do
			taglist="${taglist:+$taglist, }$tag"
			[ "$tag" = "${tags[0]}" ] || tags_same=0
		done
		if [ "$tags_same" -eq 0 ]; then
			printf '  %s: carries more than one tag (%s)\n' "$site" "$taglist"
			offenders=$((offenders + 1))
			continue
		fi
		tag="${tags[0]}"
		if [ -z "$expected" ]; then
			expected="$tag"
			expected_from="$site"
		elif [ "$tag" != "$expected" ]; then
			printf '  %s: carries %s, but %s carries %s\n' \
				"$site" "$tag" "$expected_from" "$expected"
			offenders=$((offenders + 1))
		fi
	done

	if [ "$seen" -eq 0 ]; then
		printf '  no CKAN image reference found under %s: an empty extraction is not a pass\n' "$root"
		print_inventory "$root"
		return 1
	fi
	if [ "$offenders" -gt 0 ]; then
		printf '  CKAN %s (from %s) is not shared by %s site(s); every site measured:\n' \
			"$expected" "$expected_from" "$offenders"
		print_inventory "$root"
		return 1
	fi
	printf '  ok: %s CKAN image reference(s), all %s\n' "$seen" "$expected"
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
	if [ "$2" = "$3" ]; then
		ok "$1"
	else
		notok "$1 (expected [$2], got [$3])"
	fi
}
assert_contains() { # label haystack needle
	case "$2" in
	*"$3"*) ok "$1" ;;
	*) notok "$1 (missing [$3] in [$2])" ;;
	esac
}
assert_not_contains() { # label haystack needle
	case "$2" in
	*"$3"*) notok "$1 (found [$3] in [$2])" ;;
	*) ok "$1" ;;
	esac
}

# Runs the check and echoes its own report, indented. A drift must be readable
# straight from the CI log -- which file, which tag -- not just as a failed
# assertion, so the report is always shown and asserted against.
CHECK_OUT=
CHECK_STATUS=
run_check() { # root
	CHECK_OUT="$(check_ckan_tag "$1" 2>&1)"
	CHECK_STATUS=$?
	while IFS= read -r line; do printf '     %s\n' "$line"; done <<<"$CHECK_OUT"
}

# A fixture tree shaped like the five real sites, every CKAN reference carrying
# `$2`. The service images take their own tags through `SOLR_TAG`/`POSTGRES_TAG`/
# `REDIS_TAG` so a case can prove they are never pulled into the comparison.
make_tree() { # dir ckan_tag
	local dir="$1" tag="$2"
	mkdir -p "$dir/ckan-docker/ckan" "$dir/.github/workflows"
	printf 'FROM ckan/ckan-base:%s\n' "$tag" > "$dir/ckan-docker/ckan/Dockerfile"
	printf 'FROM ckan/ckan-dev:%s\n' "$tag" > "$dir/ckan-docker/ckan/Dockerfile.dev"
	printf 'FROM ckan/ckan-base:%s\n' "$tag" > "$dir/ckan-docker/Dockerfile.umss"
	printf 'FROM ckan/ckan-dev:%s\n' "$tag" > "$dir/ckan-docker/Dockerfile.dev.umss"
	cat > "$dir/.github/workflows/checks.yml" <<EOF
container:
  image: ckan/ckan-dev:$tag
services:
  solr:
    image: ckan/ckan-solr:${SOLR_TAG:-$tag-solr9}
  postgres:
    image: ckan/ckan-postgres-dev:${POSTGRES_TAG:-$tag}
  redis:
    # Must track REDIS_VERSION in the compose files.
    image: redis:${REDIS_TAG:-6}
EOF
}

echo "the repository's CKAN references all carry one tag"
run_check "$REPO_ROOT"
assert_eq "the check accepts the current tree" "0" "$CHECK_STATUS"
for site in "${CKAN_SITES[@]}"; do
	refs="$(ckan_image_refs "$REPO_ROOT/$site")"
	assert_contains "$site is read as a site that names CKAN" "$refs" "ckan/ckan-"
done

echo "a planted disagreement fails and names both the file and its tag"
make_tree "$WORK/drift" "0.0-test"
printf 'FROM ckan/ckan-base:0.0-other\n' > "$WORK/drift/ckan-docker/Dockerfile.umss"
run_check "$WORK/drift"
assert_eq "the check rejects the disagreement" "1" "$CHECK_STATUS"
assert_contains "the offending file and its tag are named" "$CHECK_OUT" \
	"ckan-docker/Dockerfile.umss: carries 0.0-other"
assert_contains "the tag it disagrees with is named" "$CHECK_OUT" \
	"ckan-docker/ckan/Dockerfile carries 0.0-test"
assert_contains "every site is listed with its tag" "$CHECK_OUT" \
	"ckan-docker/ckan/Dockerfile.dev: 0.0-test"

echo "a second tag inside one file fails, as a stale comment would"
make_tree "$WORK/infile" "0.0-test"
# Exactly the shape of the real `Dockerfile.umss`: a comment repeating the
# literal next to the `FROM`. A bump that edits one and not the other must fail.
printf '# Extends ckan/ckan-base:0.0-old with:\nFROM ckan/ckan-base:0.0-test\n' \
	> "$WORK/infile/ckan-docker/Dockerfile.umss"
run_check "$WORK/infile"
assert_eq "the check rejects one file holding two tags" "1" "$CHECK_STATUS"
assert_contains "the file is named with both tags" "$CHECK_OUT" \
	"ckan-docker/Dockerfile.umss: carries more than one tag (0.0-old, 0.0-test)"

echo "a site that stopped naming CKAN fails instead of shrinking the set"
make_tree "$WORK/noref" "0.0-test"
cat > "$WORK/noref/.github/workflows/checks.yml" <<'YAML'
container:
  image: ubuntu:24.04
YAML
run_check "$WORK/noref"
assert_eq "the check rejects a site that lost its CKAN image" "1" "$CHECK_STATUS"
assert_contains "the emptied site is named" "$CHECK_OUT" \
	".github/workflows/checks.yml: no longer names ckan/ckan-base or ckan/ckan-dev"

echo "a renamed site fails instead of passing on a shrunken set"
make_tree "$WORK/renamed" "0.0-test"
mv "$WORK/renamed/ckan-docker/Dockerfile.dev.umss" \
	"$WORK/renamed/ckan-docker/Dockerfile.dev.umss.bak"
run_check "$WORK/renamed"
assert_eq "the check rejects the missing site" "1" "$CHECK_STATUS"
assert_contains "the missing site is named" "$CHECK_OUT" \
	"ckan-docker/Dockerfile.dev.umss: MISSING"

echo "an empty extraction is not a pass"
run_check "$WORK/no-such-tree"
assert_eq "the check rejects a tree that names no CKAN image" "1" "$CHECK_STATUS"
assert_contains "the empty extraction is reported as such" "$CHECK_OUT" \
	"an empty extraction is not a pass"

echo "a digest pin cannot agree with a tag and fails"
make_tree "$WORK/digest" "0.0-test"
digest="sha256:0000000000000000000000000000000000000000000000000000000000000000"
printf 'FROM ckan/ckan-base@%s\n' "$digest" > "$WORK/digest/ckan-docker/ckan/Dockerfile"
run_check "$WORK/digest"
assert_eq "the check rejects a digest-pinned CKAN image" "1" "$CHECK_STATUS"
assert_contains "the pin is named" "$CHECK_OUT" \
	"ckan-docker/ckan/Dockerfile: ckan/ckan-base@sha256:"

echo "a lookalike namespace and a URL in a comment are not read as CKAN refs"
make_tree "$WORK/lookalike" "0.0-test"
cat > "$WORK/lookalike/ckan-docker/ckan/Dockerfile" <<'EOF'
FROM xckan/ckan-dev:0.0-other
# cf. https://github.com/ckan/ckan-base:0.0-other
EOF
run_check "$WORK/lookalike"
assert_eq "the check is not fooled by a lookalike namespace" "1" "$CHECK_STATUS"
assert_contains "the file is named as naming CKAN unreadably" "$CHECK_OUT" \
	"ckan-docker/ckan/Dockerfile: names CKAN, but not as a literal tag the check can read"


echo "service images keep their own tags and are never read as CKAN"
SOLR_TAG="9.9-solr9" POSTGRES_TAG="1.0" REDIS_TAG="3" make_tree "$WORK/services" "0.0-test"
# An assignment prefix on a function call stays set in this shell, so clear the
# knobs before any later case builds another tree.
unset SOLR_TAG POSTGRES_TAG REDIS_TAG
run_check "$WORK/services"
assert_eq "the check accepts services that do not share CKAN's tag" "0" "$CHECK_STATUS"
assert_contains "the fixture is read as one reference per site" "$CHECK_OUT" \
	"ok: ${#CKAN_SITES[@]} CKAN image reference(s), all 0.0-test"
assert_contains "the workflow's own CKAN container is read" \
	"$(ckan_image_refs "$WORK/services/.github/workflows/checks.yml")" "ckan/ckan-dev:0.0-test"
assert_not_contains "the Solr service image is not read as CKAN" \
	"$(ckan_image_refs "$WORK/services/.github/workflows/checks.yml")" "solr"
assert_not_contains "the Postgres service image is not read as CKAN" \
	"$(ckan_image_refs "$WORK/services/.github/workflows/checks.yml")" "postgres"

echo
if [ "$failures" -eq 0 ]; then
	echo "all tests passed"
	exit 0
fi
echo "$failures test(s) failed"
exit 1
