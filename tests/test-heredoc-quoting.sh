#!/usr/bin/env bash
#
# Guards against a heredoc that bash expands behind the reader's back.
#
# Run from anywhere:  bash tests/test-heredoc-quoting.sh
#
# With an unquoted delimiter (`<<PY`) bash runs command substitution and
# parameter expansion over the heredoc body before the receiving program sees
# it. Measured on 2026-10-16: `bin/test-umss` fed Python a comment reading
#
#     # `timeout` no es decorativo ...
#
# and bash ran `timeout` with no operand. Every suite log started with
# `Try 'timeout --help' for more information.`, and the word vanished from the
# comment. Harmless that time; a `$` or a `$(...)` written for the embedded
# program would have been expanded the same way, silently.
#
# What is enforced: a heredoc with an UNQUOTED delimiter must not contain
# command substitution -- a backtick or `$(` -- in its body. Quoting the
# delimiter (`<<'PY'` or `<<"PY"`) hands the body over verbatim and is always
# accepted. Parameter expansion (`$tag`, `${X:-y}`) in an unquoted heredoc stays
# allowed: two fixture writers in `ckan-docker/ckan/tests/` use it on purpose,
# in plain sight. A command that has to run inside a heredoc would be a
# visible, reviewed exception here, not a default.
#
# The subject set is the same globs the CI runs or ships: the executables
# directly in `ckan-docker/bin/`, `ckan-docker/*/docker-entrypoint.d/*.sh`,
# `ckan-docker/*/tests/*.sh` and `tests/*.sh`. Vendored upstream code under
# `ckan-docker/src/` is not ours and is out of scope.
#
# What this guard CANNOT see, stated rather than implied:
#
#   - A heredoc operator assembled at run time, or one inside a string that is
#     later `eval`ed in pieces. The check reads the text: a `<<WORD` that
#     appears in the text is checked, wherever it sits (the `test-umss` one is
#     itself inside a single-quoted `bash -lc` payload).
#   - Here-strings (`<<<`) are a different operator and are skipped, and so are
#     comment lines: prose that names the operator opens no heredoc.
#   - A stray `$` written for the embedded program in an unquoted heredoc that
#     bash would expand is not flagged: it is indistinguishable from intended
#     parameter expansion. Embedded programs should use a quoted delimiter.
#
# No `set -e`: the harness decides what a non-zero status means.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

failures=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }

# Prints `line: text` for every heredoc with an unquoted delimiter whose body
# contains command substitution. The body runs from the operator line to the
# delimiter line (leading tabs stripped for `<<-`); comment lines open nothing.
unsafe_heredocs() {
	awk '
		function report() { printf "%d: %s\n", start, opener }
		in_body {
			line = $0
			if (strip) sub(/^\t+/, "", line)
			if (line == delim) { if (flagged) report(); in_body = 0; next }
			if ($0 ~ /`/ || index($0, "$(") > 0) flagged = 1
			next
		}
		/^[[:space:]]*#/ { next }
		{
			rest = $0
			while (match(rest, /<<-?[[:space:]]*[A-Za-z_][A-Za-z0-9_]*/)) {
				op = substr(rest, RSTART, RLENGTH)
				before = substr(rest, 1, RSTART - 1)
				rest = substr(rest, RSTART + RLENGTH)
				if (before ~ /<$/) continue
				strip = (op ~ /^<<-/)
				delim = op
				sub(/^<<-?[[:space:]]*/, "", delim)
				in_body = 1; flagged = 0; start = NR; opener = $0
			}
		}
	' "$1"
}

subjects() {
	local f
	for f in "$REPO_ROOT"/ckan-docker/bin/*; do
		[[ -f "$f" && -x "$f" ]] && printf '%s\n' "$f"
	done
	for f in "$REPO_ROOT"/ckan-docker/*/docker-entrypoint.d/*.sh \
		"$REPO_ROOT"/ckan-docker/*/tests/*.sh \
		"$REPO_ROOT"/tests/*.sh; do
		[[ -f "$f" ]] && printf '%s\n' "$f"
	done
}

# --- The real tree -----------------------------------------------------------

count=0
while IFS= read -r subject; do
	count=$((count + 1))
	rel="${subject#"$REPO_ROOT"/}"
	found="$(unsafe_heredocs "$subject")"
	if [[ -z "$found" ]]; then
		pass "$rel"
	else
		fail "$rel: unquoted heredoc with command substitution at $found"
	fi
done < <(subjects)

if [[ "$count" -lt 10 ]]; then
	fail "subject set looks wrong: only $count file(s) found"
fi

# --- Fixtures ------------------------------------------------------------------

FIXTURES="$(mktemp -d)"
trap 'rm -rf "$FIXTURES"' EXIT

check_fixture() {
	local expect="$1" label="$2" body="$3"
	printf '%s\n' "$body" >"$FIXTURES/subject.sh"
	if [[ -z "$(unsafe_heredocs "$FIXTURES/subject.sh")" ]]; then
		got=accepts
	else
		got=refuses
	fi
	if [[ "$got" == "$expect" ]]; then
		pass "checker $expect $label"
	else
		fail "checker should $expect $label, but $got"
	fi
}

# The operator is assembled from pieces so this file's own text carries no
# heredoc for the check above to read.
op='<''<'

# Must-pass first: a checker that refuses everything would make the must-fail
# rows green for the wrong reason.
check_fixture accepts "a quoted delimiter over a backtick" "python - ${op}'PY'"$'\n# `x`\nPY'
check_fixture accepts "a double-quoted delimiter over \$(" "python - ${op}\"PY\""$'\nprint("$(x)")\nPY'
check_fixture accepts "parameter expansion in an unquoted body" "cat ${op}EOF"$'\nimage: redis:$tag ${X:-6}\nEOF'
check_fixture accepts "a here-string" "grep -q x ${op}< \"\$value\""
check_fixture accepts "the operator named in a comment" "# python - ${op}PY"$'\n# `x`'
check_fixture accepts "a backtick after the body ends" "cat ${op}EOF"$'\nx\nEOF\necho `date`'

check_fixture refuses "a backtick in an unquoted body" "python - ${op}PY"$'\n# `timeout` no es decorativo\nPY'
check_fixture refuses "\$( in an unquoted body" "cat ${op} EOF"$'\n$(whoami)\nEOF'
check_fixture refuses "a backtick in an unquoted, tab-stripped body" "cat ${op}-EOF"$'\n\t`x`\n\tEOF'

if [[ "$failures" -ne 0 ]]; then
	printf '\n%d check(s) failed\n' "$failures"
	exit 1
fi
printf '\nall checks passed\n'
