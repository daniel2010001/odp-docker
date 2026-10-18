#!/usr/bin/env bash
#
# `.env.example` must never ship `CKANEXT__XLOADER__API_TOKEN=` with an empty value.
#
# Measured 2026-10-17 on an isolated stack: ckanext-envvars turns the empty variable into
# `ckanext.xloader.api_token = ""`, xloader's config declaration rejects it
# (`CkanConfigurationException: ckanext.xloader.api_token: Missing value`), and every
# `ckan` CLI call fails with it -- `db init`, the umss migration, and the token mint in
# `01_setup_xloader.sh`, which then refuses to write. CKAN never becomes healthy. The
# variable must be absent (or commented out) for the boot-time mint to run.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_EXAMPLE="${HERE}/../../.env.example"

if grep -Eq '^[[:space:]]*CKANEXT__XLOADER__API_TOKEN=[[:space:]]*$' "$ENV_EXAMPLE"; then
	echo "  FAIL .env.example sets an empty CKANEXT__XLOADER__API_TOKEN; CKAN will not boot"
	exit 1
fi
echo "  ok   .env.example does not set an empty CKANEXT__XLOADER__API_TOKEN"
