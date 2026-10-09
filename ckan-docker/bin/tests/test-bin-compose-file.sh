#!/usr/bin/env bash
#
# Tests for the compose file path the `ckan-docker/bin/` scripts pass to
# `docker compose`.
#
# Run from anywhere:  bash ckan-docker/bin/tests/test-bin-compose-file.sh
#
# What is enforced, per subject:
#
#   1. The subject set is the directory listing: every regular executable file
#      directly inside `ckan-docker/bin/` (subdirectories are never descended
#      into). Discovery never depends on how a call is spelled. A subject that
#      mentions `docker` but records no invocation is a FAILURE, not a silent
#      skip -- so a call written as `sudo docker compose`, `env X=1 docker
#      compose`, or indented inside an `if ...; then` is still run and still
#      checked. A regular executable that never mentions `docker` is reported as
#      `skipped (no docker reference)`: a visible decision, not a dropped one.
#   2. Only the compose command's own option region is examined: the argv
#      elements after `compose` and before the first non-option (the subcommand,
#      e.g. `exec`, `run`, `restart`, `up`). `-f` and `--file` are the same
#      option; exactly one is required, with a value. A stray `-f` after the
#      subcommand is not counted at all.
#   3. That value must resolve to the one repository compose file, and the file
#      must exist.
#   4. The subject's text must not contain the compose file's literal name: the
#      path has to come from `COMPOSE_FILE` (`bin/_common.sh`), the single source
#      of truth. This is the structural axis, independent of the argv axis.
#
# Declared limits -- what this test does NOT enforce, so that no green row here is
# ever read as more than it is:
#
#   * The mechanism, only its effect. A subject that assembles the compose file
#     name at run time -- concatenating the literal in pieces, or sourcing a
#     second, non-executable helper that carries the literal -- still passes.
#     What is enforced is exactly this: no subject carries the literal name, and
#     every subject hands the same, existing, resolved file to `docker compose`.
#   * Exactly one `docker` invocation per subject. A subject that invokes docker
#     twice fails loudly instead of having both invocations checked.
#   * A subject that mentions the word `docker` anywhere -- a comment counts --
#     must invoke it exactly once. The failure prints the referencing lines, so a
#     comment-only mention is diagnosable from the output alone.
#   * The option-region parser knows the value-taking options it lists:
#     `-f/--file`, `-p/--project-name`, `--profile`, `--env-file`,
#     `--project-directory`, `--ansi`, `--progress`. An unknown option that takes
#     a separate value would end the region early and be reported as a missing
#     `-f`.
#
# Every subject that references docker is run twice: from a foreign cwd on an
# ordinary path, and again reached through a symlink under a directory whose
# name contains a space. The space case is the one that fails when the path is
# derived with an unquoted `$(dirname ${BASH_SOURCE[0]})`.
#
# Measured, and stated correctly here: the broken path does NOT arrive as two
# argv elements. The unquoted `$(dirname ${BASH_SOURCE[0]})` word-splits into
# two operands and `dirname` prints two lines, but both lines stay inside ONE
# command substitution; because the use site quotes the value, the result is a
# SINGLE argv element that contains an embedded newline. argc is unchanged:
# `compose -f $'/tmp/x\nniel/odp/...' exec ...`. The option region still holds
# exactly one `-f`; its value simply is not a file.
#
# Nothing here touches Docker or the network: a stub `docker` on `PATH` records
# its argv and returns. Everything written lives in a temp dir; the repository is
# only read.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$(cd "${HERE}/.." && pwd)"     # ckan-docker/bin
REPO="$(cd "${BIN}/../.." && pwd)"  # repository root
COMPOSE="${REPO}/docker-compose.dev.unified.yml"
COMPOSE_LITERAL="docker-compose.dev.unified.yml"

failures=0
ok() { printf '  ok   %s\n' "$1"; }
notok() {
	printf '  FAIL %s\n' "$1"
	failures=$((failures + 1))
}

# Everything the test writes lives in a temp dir; the repository is read only.
# The spaced directory is created on purpose: it is the case that fails when the
# path is not quoted.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STUB_BIN="$WORK/stub-bin"
mkdir -p "$STUB_BIN" "$WORK/plain-cwd" "$WORK/da niel"
ARGV_LOG="$WORK/docker-argv"
CALLS_LOG="$WORK/docker-calls"

# A stub `docker` that records its argv and returns. NUL separation keeps an
# argument that contains a newline (exactly the broken case) as one element; a
# separate line per invocation counts the calls.
cat > "$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\0' "$@" >> "${DOCKER_ARGV_LOG:?}"
printf '%s\n' "$#" >> "${DOCKER_CALLS_LOG:?}"
STUB
chmod +x "$STUB_BIN/docker"

# A symlink whose path contains a space, pointing at the repository root. The
# scripts are then invoked through it, so `BASH_SOURCE[0]` carries the space.
SPACED_LINK="$WORK/da niel/odp"
ln -s "$REPO" "$SPACED_LINK"

if [ ! -f "$COMPOSE" ]; then
	echo "test-bin-compose-file: repository compose file not found: $COMPOSE" >&2
	exit 1
fi

# The subject set is the directory listing, not a grep of invocation lines.
# `_common.sh` is sourced and must stay non-executable so it never enters the
# set: if it were executable, its own comment would make it look like a caller
# that never invokes anything. The precondition below asserts that instead of
# trusting it.
SUBJECTS=()
for f in "$BIN"/*; do
	[ -f "$f" ] || continue
	[ -x "$f" ] || continue
	SUBJECTS+=("$(basename "$f")")
done

echo "subjects (regular executable files directly under ${BIN}):"
if [ "${#SUBJECTS[@]}" -eq 0 ]; then
	echo "  (none)"
	notok "no regular executable files directly under ${BIN}"
else
	printf '  %s\n' "${SUBJECTS[@]}"
fi

if [ ! -e "${BIN}/_common.sh" ]; then
	notok "_common.sh is missing: the single source of truth must exist"
elif [ -x "${BIN}/_common.sh" ]; then
	notok "_common.sh is executable; it is sourced and must not enter the subject set"
else
	ok "_common.sh is present and non-executable (sourced, not a subject)"
fi

# Run one subject from `$1` (a cwd), reached through `$2` (a script path), and
# report with `$3`. Fills the globals ARGV and CALLS. The script may exit
# non-zero for its own reasons (the stub answers nothing); that says nothing
# about the argv, which is what we assert.
ARGV=()
CALLS=0
run_script() {
	local cwd="$1" script="$2" label="$3"
	: > "$ARGV_LOG"
	: > "$CALLS_LOG"
	(
		cd "$cwd"
		DOCKER_ARGV_LOG="$ARGV_LOG" DOCKER_CALLS_LOG="$CALLS_LOG" \
			PATH="$STUB_BIN:$PATH" bash "$script" >/dev/null 2>&1
	) || true
	CALLS="$(wc -l < "$CALLS_LOG")"
	ARGV=()
	mapfile -d '' -t ARGV < "$ARGV_LOG" || true

	if [ "$CALLS" -eq 0 ]; then
		notok "${label}: references docker but recorded no invocation"
		# Show where the word appears: a mention that only lives in a comment is
		# then diagnosable from this output alone, without opening the file.
		while IFS= read -r ref; do
			printf '        references: %s\n' "$ref"
		done < <(grep -nw -- docker "$script" || true)
		return
	fi
	if [ "$CALLS" -ne 1 ]; then
		notok "${label}: expected exactly one docker invocation, got ${CALLS}"
		return
	fi
	assert_compose_argv "$label"
}

dump_argv() {
	printf '        argc: %s\n' "${#ARGV[@]}"
	local a
	for a in ${ARGV[@]+"${ARGV[@]}"}; do
		printf '        argv: %q\n' "$a"
	done
}

# Assert the compose command's own option region carries exactly one `-f` or
# `--file`, with a value that resolves to the one repository compose file.
assert_compose_argv() {
	local label="$1"
	local n="${#ARGV[@]}" i compose_idx=-1
	for i in "${!ARGV[@]}"; do
		if [ "${ARGV[$i]}" = "compose" ]; then
			compose_idx=$i
			break
		fi
	done
	if [ "$compose_idx" -lt 0 ]; then
		notok "${label}: docker invoked without the compose subcommand"
		dump_argv
		return
	fi

	local count=0 value="" missing_value=0 el
	i=$((compose_idx + 1))
	while [ "$i" -lt "$n" ]; do
		el="${ARGV[$i]}"
		case "$el" in
		-f | --file)
			count=$((count + 1))
			if [ "$((i + 1))" -lt "$n" ]; then
				value="${ARGV[$((i + 1))]}"
				i=$((i + 2))
			else
				missing_value=1
				i=$((i + 1))
			fi
			;;
		-p | --project-name | --profile | --env-file | --project-directory | \
			--ansi | --progress)
			# Value-taking option: skip its value too, or that value would be
			# read as the first non-option and end the region early.
			if [ "$((i + 1))" -lt "$n" ]; then
				i=$((i + 2))
			else
				i=$((i + 1))
			fi
			;;
		--)
			break
			;;
		-*)
			i=$((i + 1))
			;;
		*)
			break
			;;
		esac
	done

	if [ "$count" -eq 0 ]; then
		notok "${label}: no -f/--file in the compose option region"
		dump_argv
		return
	fi
	if [ "$count" -ne 1 ]; then
		notok "${label}: expected exactly one -f/--file, got ${count}"
		dump_argv
		return
	fi
	if [ "$missing_value" -eq 1 ]; then
		notok "${label}: -f/--file has no value"
		dump_argv
		return
	fi
	if [ -z "$value" ]; then
		notok "${label}: -f/--file has an empty value"
		dump_argv
		return
	fi
	if [ ! -f "$value" ]; then
		notok "${label}: -f/--file value is not an existing file"
		printf '        value: %q\n' "$value"
		dump_argv
		return
	fi

	local resolved expected
	resolved="$(realpath -- "$value")"
	expected="$(realpath -- "$COMPOSE")"
	if [ "$resolved" != "$expected" ]; then
		notok "${label}: -f/--file resolves to the wrong file"
		printf '        expected: %s\n' "$expected"
		printf '        actual:   %s\n' "$resolved"
		dump_argv
		return
	fi

	ok "$label"
}

echo
echo "no subject carries the compose file literal"
for name in "${SUBJECTS[@]}"; do
	file="${BIN}/${name}"
	lines="$(grep -nF -- "$COMPOSE_LITERAL" "$file" || true)"
	if [ -n "$lines" ]; then
		notok "${name}: contains the compose file literal"
		while IFS= read -r line; do
			printf '        %s\n' "$line"
		done <<< "$lines"
	else
		ok "${name}"
	fi
done

echo
echo "a foreign cwd, an ordinary path"
for name in "${SUBJECTS[@]}"; do
	file="${BIN}/${name}"
	if ! grep -qw docker "$file"; then
		ok "${name}: skipped (no docker reference)"
		continue
	fi
	run_script "$WORK/plain-cwd" "$file" "$name"
done

echo
echo "a foreign cwd reached through a path containing a space"
for name in "${SUBJECTS[@]}"; do
	file="${BIN}/${name}"
	if ! grep -qw docker "$file"; then
		continue # already reported as skipped above
	fi
	run_script "$WORK/plain-cwd" "${SPACED_LINK}/ckan-docker/bin/${name}" \
		"${name} through a spaced path"
done

echo
if [ "$failures" -eq 0 ]; then
	echo "all tests passed"
	exit 0
fi
echo "$failures test(s) failed"
exit 1
