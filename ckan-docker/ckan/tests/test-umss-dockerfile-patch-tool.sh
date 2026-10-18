#!/usr/bin/env bash
#
# `Dockerfile.umss` runs a `patch -p1` loop over `ckan/patches/`, and its base image
# `ckan/ckan-base:2.12` ships no `patch` (measured 2026-10-17: `which patch` exits 1;
# `ckan/ckan-dev:2.12` has it, which is why only the production build failed, with
# `/bin/sh: 1: patch: not found`). The Dockerfile must install `patch` before the loop.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${HERE}/../../Dockerfile.umss"

loop_line="$(grep -n 'patch -p1' "$DOCKERFILE" | head -n 1 | cut -d: -f1)"
install_line="$(grep -n -E 'apt-get install[^#]*[[:space:]]patch([[:space:]]|$)' "$DOCKERFILE" | head -n 1 | cut -d: -f1)"

if [ -z "$loop_line" ]; then
	echo "  ok   Dockerfile.umss has no patch loop, so it needs no patch tool"
	exit 0
fi
if [ -z "$install_line" ] || [ "$install_line" -gt "$loop_line" ]; then
	echo "  FAIL Dockerfile.umss runs 'patch -p1' at line $loop_line without installing patch before it"
	exit 1
fi
echo "  ok   Dockerfile.umss installs patch (line $install_line) before the patch loop (line $loop_line)"
