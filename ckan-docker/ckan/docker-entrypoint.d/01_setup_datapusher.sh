#!/bin/bash

if [[ $CKAN__PLUGINS == *"datapusher"* ]]; then
   # Datapusher settings have been configured in the .env file
   # Set API token if necessary
   if [ -z "$CKAN__DATAPUSHER__API_TOKEN" ] ; then
      echo "Set up ckan.datapusher.api_token in the CKAN config file"
      # `expire_api_token` makes `expires_in` and `unit` mandatory on
      # `api_token_create`, and the bare CLI call does not pass them: it fails with
      # `ValidationError: {'expires_in': ['Missing value'], 'unit': ['Missing value']}`
      # and prints nothing on stdout, so the old `$( ... )` captured an EMPTY string.
      # An empty `ckan.datapusher.api_token` makes the DataPusher plugin refuse to
      # configure (`plugin.py:52`), so the container crash-looped on every restart.
      # `--json` also works, but the CLI accepts `key=value` arguments directly, and avoiding
      # nested quoting removes a whole class of escaping bug: an earlier version of this line
      # shipped `--json '{\"expires_in\": 1, ...}'`, whose literal backslashes made CKAN fail
      # with `JSONDecodeError` and left the token empty, which is the crash loop this fixes.
      # The expiry is deliberately long. `expire_api_token` makes `expires_in` and `unit`
      # mandatory, and a one-day token would expire silently on a dev stack left running
      # for more than a day -- which is normal here (the previous container ran 39 hours).
      # A service credential that nothing renews should not be the shortest-lived thing
      # in the stack.
      datapusher_token="$(ckan -c $CKAN_INI user token add ckan_admin datapusher expires_in=365 unit=86400 -q | tail -n 1 | tr -d '\t')"
      if [[ ! $datapusher_token =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
         # Writing whatever came back is how a bad token reaches ckan.ini. An empty
         # one makes the plugin raise (`ckanext/datapusher/plugin.py:52`) and CKAN
         # crash-loops, which is what the base image heads off by writing the
         # placeholder `ckan.datapusher.api_token=xxx` before this file runs
         # (`/srv/app/start_ckan_development.sh:6`, "updated with corrected value
         # later"). Refusing to write therefore leaves that placeholder in place:
         # CKAN still starts and the datapusher stays broken -- 403 on its
         # callbacks, resources stuck in `pending` -- until the mint is fixed. That
         # is deliberate: this container is `restart: unless-stopped`, so a mint
         # failure must not become a crash loop.
         echo "datapusher: refusing to write ckan.datapusher.api_token: the CLI returned ${#datapusher_token} bytes and none of them form a token" >&2
         echo "datapusher: $CKAN_INI keeps whatever was there -- the base entrypoint's placeholder -- so CKAN starts and the datapusher stays broken" >&2
         echo "datapusher: reproduce with: ckan -c $CKAN_INI user token add ckan_admin datapusher expires_in=365 unit=86400 -q" >&2
         # `return`, never `exit`: the entrypoint SOURCES this file
         # (`/srv/app/start_ckan_development.sh`), so an `exit` here would kill
         # PID 1 instead of letting CKAN start with the token already in place.
         return 1
      fi
      ckan config-tool $CKAN_INI "ckan.datapusher.api_token=$datapusher_token"
   fi
else
   echo "Not configuring DataPusher"
fi
