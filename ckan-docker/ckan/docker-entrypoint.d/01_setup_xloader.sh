#!/bin/bash

if [[ $CKAN__PLUGINS == *"xloader"* ]]; then
   # The rest of the xloader settings (site_url, jobs_db.uri, parse_dates_dayfirst) come
   # from the `x-xloader-env` anchor in the compose files, through the envvars plugin.
   # Set API token if necessary
   if [ -z "$CKANEXT__XLOADER__API_TOKEN" ] ; then
      echo "Set up ckanext.xloader.api_token in the CKAN config file"
      # `expire_api_token` makes `expires_in` and `unit` mandatory on
      # `api_token_create`, and the bare CLI call does not pass them: it fails with
      # `ValidationError: {'expires_in': ['Missing value'], 'unit': ['Missing value']}`
      # and prints nothing on stdout, so a bare `$( ... )` captures an EMPTY string.
      # The CLI accepts `key=value` arguments directly, and avoiding the `--json` form
      # removes a whole class of nested-quoting bug (literal backslashes made CKAN fail
      # with `JSONDecodeError` and left the token empty on an earlier DataPusher version).
      # The expiry is deliberately long. `expire_api_token` makes `expires_in` and `unit`
      # mandatory, and a one-day token would expire silently on a dev stack left running
      # for more than a day -- which is normal here (the previous container ran 39 hours).
      # A service credential that nothing renews should not be the shortest-lived thing
      # in the stack.
      xloader_token="$(ckan -c $CKAN_INI user token add "${CKAN_SYSADMIN_NAME:-ckan_admin}" xloader expires_in=365 unit=86400 -q | tail -n 1 | tr -d '\t')"
      if [[ ! $xloader_token =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
         # Writing whatever came back is how a bad token reaches ckan.ini. Refusing to
         # write leaves the ini as it was: CKAN still starts, and `xloader_submit`
         # answers a ValidationError because `ckanext.xloader.api_token` stays `NOT_SET`
         # (`ckanext/xloader/utils.py:163`) -- uploads work, nothing is loaded into the
         # DataStore -- until the mint is fixed. That is deliberate: this container is
         # `restart: unless-stopped`, so a mint failure must not become a crash loop.
         echo "xloader: refusing to write ckanext.xloader.api_token: the CLI returned ${#xloader_token} bytes and none of them form a token" >&2
         echo "xloader: $CKAN_INI keeps whatever was there, so CKAN starts and xloader_submit answers a ValidationError (api_token NOT_SET)" >&2
         echo "xloader: reproduce with: ckan -c $CKAN_INI user token add ${CKAN_SYSADMIN_NAME:-ckan_admin} xloader expires_in=365 unit=86400 -q" >&2
         # `return`, never `exit`: the entrypoint SOURCES this file
         # (`/srv/app/start_ckan_development.sh`), so an `exit` here would kill
         # PID 1 instead of letting CKAN start.
         return 1
      fi
      ckan config-tool $CKAN_INI "ckanext.xloader.api_token=$xloader_token"
   fi
else
   echo "Not configuring xloader"
fi
