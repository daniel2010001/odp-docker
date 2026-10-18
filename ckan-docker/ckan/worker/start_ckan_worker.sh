#!/bin/bash
#
# PID 1 of the `ckan-worker` / `ckan-worker-dev` services: a plain `ckan jobs worker`
# that consumes the RQ queue `default`, where xloader's jobs go. It is EXECUTED, not
# sourced, so `set -eu` and `exit` are right here (unlike `docker-entrypoint.d/`).
# It lives in `/srv/worker`, outside every dev volume (`/srv/app`, `/usr/local/bin`,
# site-packages), so the image's copy is always the one that runs.
set -eu

# The job carries the API token the WEB container minted (`xloader_submit` puts it in
# the payload) and the worker decodes it, so both need the same JWT secret from the
# environment. Without it the worker would boot and then fail every job at decode time.
if [ -z "${CKAN___API_TOKEN__JWT__DECODE__SECRET:-}" ]; then
   echo "ckan-worker: CKAN___API_TOKEN__JWT__DECODE__SECRET is empty; refusing to start" >&2
   echo "ckan-worker: the job carries the web container's token and must be decoded with the same secret (set it in .env)" >&2
   exit 1
fi

# Same plugins as the web container, or the worker cannot import the xloader job.
ckan config-tool "$CKAN_INI" "ckan.plugins=$CKAN__PLUGINS"

# The image's ini ships `SECRET_KEY = ` empty and the web start scripts fill it; CKAN
# refuses to build the app without one, and this container does not run those scripts.
if grep -Eq '^SECRET_KEY *= *$' "$CKAN_INI"; then
   ckan config-tool "$CKAN_INI" "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi

exec ckan -c "$CKAN_INI" jobs worker
