#!/usr/bin/env bash
#
# Tests for `docker-entrypoint.d/03_upgrade_umss_schema.sh`.
#
# Run from anywhere:  bash ckan-docker/ckan/tests/test-upgrade-umss-schema.sh
#
# These tests SOURCE the script, because the entrypoint does exactly that
# (`. "$f"` over `/docker-entrypoint.d/*` in both `/srv/app/start_ckan_development.sh`
# and `/srv/app/start_ckan.sh`). Every property pinned here follows from that, or
# from a measurement taken on the running stack:
#
#   1. It must not pass `--skip-core`. Measured 2026-10-07: with `-p <plugin>`,
#      that flag turns the command into a silent no-op that still prints SUCCESS,
#      so a verifier trusting the exit status would call an un-migrated database
#      healthy.
#   2. It must verify the TABLE and not the exit status. The check is
#      `select to_regclass('public.publication_requests')` — plural, which is
#      load-bearing: the claim that "the dev database has no store table" came from
#      looking up the singular and two sessions took that empty result as mutual
#      confirmation.
#   3. A missing table must return non-zero **without killing the shell that
#      sourced it**: only an explicit `exit` inside a sourced script can take PID 1
#      down, and the container is `restart: unless-stopped`, so a failed migration
#      must leave a trail instead of becoming a crash loop.
#   4. "I could not look" must not be reported as "it is not there": the checker has
#      three states, and the two failure modes say different things in the log.
#
# This file must NOT live in `docker-entrypoint.d/`: the Dockerfiles COPY that whole
# directory into the image and the entrypoint sources every `*.sh` in it.
#
# No `set -e` on purpose: the harness decides what a non-zero status means, and the
# sourced script is written for a shell that does not abort on errors.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../docker-entrypoint.d/03_upgrade_umss_schema.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
mkdir -p "$BIN"

# A `ckan` stand-in that records its arguments and reports the exit status the case
# wants. `db upgrade -p umss` must appear (without `--skip-core`) and the case where
# it fails must be distinguishable.
cat > "$BIN/ckan" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${CKAN_LOG:-/dev/null}"
exit "${CKAN_EXIT:-0}"
STUB

# A `python3` stand-in that stands for the table check: it swallows the script that
# comes in on stdin and reports the state the case wants (0 present, 1 missing,
# 2 unknown). Swallowing stdin matters — the caller writes the checker through a
# heredoc, and a child that exits without reading can disturb the writer.
cat > "$BIN/python3" <<'STUB'
#!/usr/bin/env bash
cat > /dev/null
exit "${PY_EXIT:-0}"
STUB
chmod +x "$BIN/ckan" "$BIN/python3"

pass=0
fail=0
check() {
	local label="$1" expected="$2" actual="$3"
	if [ "$expected" = "$actual" ]; then
		pass=$((pass + 1))
		printf 'ok   %s\n' "$label"
	else
		fail=$((fail + 1))
		printf 'FAIL %s\n     expected: %s\n     actual:   %s\n' "$label" "$expected" "$actual"
	fi
}

# Sources the script in a shell of its own, with the stubs on PATH, and echoes
# everything the caller needs: the status the sourced script returned and a marker
# proving the sourcing shell survived it.
source_it() {
	CKAN_LOG="$1" CKAN_EXIT="${2:-0}" PY_EXIT="${3:-0}" \
		PATH="$BIN:$PATH" bash -c '. "$1"; printf "\nsurvived:%s" "$?"' _ "$SCRIPT" 2>&1
}

# ── 1. The command is the one A6 requires, and never the silent no-op ──────────
LOG="$WORK/ckan-args-ok.log"
out="$(source_it "$LOG" 0 0)"
check "table present: the script returns 0" "survived:0" "$(printf '%s' "$out" | tail -n 1)"
check "it runs 'db upgrade -p umss'" "yes" "$(grep -q 'db upgrade -p umss' "$LOG" && echo yes || echo no)"
check "it does NOT pass --skip-core" "no" "$(grep -q 'skip-core' "$LOG" && echo yes || echo no)"
check "it reports the table before and after" "2" "$(grep -c 'publication_requests' <<<"$out")"

# ── 2. A missing table after a successful upgrade fails loudly, and survives ───
LOG="$WORK/ckan-args-missing.log"
out="$(source_it "$LOG" 0 1)"
check "table missing: the sourced script returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "table missing: it names the table" "yes" "$(grep -q 'STILL MISSING' <<<"$out" && echo yes || echo no)"
check "table missing: it warns about the SUCCESS lie" "yes" "$(grep -q -- '--skip-core' <<<"$out" && echo yes || echo no)"

# ── 3. An upgrade that fails is reported, and nothing is claimed about the table ─
LOG="$WORK/ckan-args-failed.log"
out="$(source_it "$LOG" 1 0)"
check "upgrade fails: the sourced script returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "upgrade fails: it says the schema was not verified" "yes" "$(grep -q 'NOT verified' <<<"$out" && echo yes || echo no)"

# ── 4. "Cannot check" is not "is not there" ────────────────────────────────────
LOG="$WORK/ckan-args-unknown.log"
out="$(source_it "$LOG" 0 2)"
check "cannot check: the sourced script returns 1" "survived:1" "$(printf '%s' "$out" | tail -n 1)"
check "cannot check: it does NOT claim the table is missing" "yes" "$(grep -q 'STILL MISSING' <<<"$out" && echo no || echo yes)"
check "cannot check: it says so in those words" "yes" "$(grep -q 'could NOT BE CHECKED' <<<"$out" && echo yes || echo no)"

# ── 5. The script is only valid when sourced, and is executable in the image ───
check "the script is executable" "yes" "$([ -x "$SCRIPT" ] && echo yes || echo no)"
check "it never calls a bare 'exit'" "no" "$(grep -qE '^[[:space:]]*exit([[:space:]]|$)' "$SCRIPT" && echo yes || echo no)"

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
