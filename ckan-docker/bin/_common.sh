#!/usr/bin/env bash
#
# Single source of truth for the compose file the `ckan-docker/bin/` scripts use.
#
# This file is SOURCED, never executed. It sets exactly two variables and has no
# other side effect.
#
#   ROOT          absolute path of the `ckan-docker` directory
#   COMPOSE_FILE  absolute path of the unified dev compose file it wraps
#
# `cd -- ... && pwd` makes ROOT absolute, so the result does not depend on the
# caller's cwd. Every expansion is quoted: an unquoted `$(dirname ...)`
# word-splits on a path containing a space, `dirname` then prints one line per
# word, ROOT becomes multi-line, and `docker compose -f` receives garbage.

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT}/../docker-compose.dev.unified.yml"
