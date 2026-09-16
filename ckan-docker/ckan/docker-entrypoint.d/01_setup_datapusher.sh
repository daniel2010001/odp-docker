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
      ckan config-tool $CKAN_INI "ckan.datapusher.api_token=$(ckan -c $CKAN_INI user token add ckan_admin datapusher expires_in=365 unit=86400 -q | tail -n 1 | tr -d '\t')"
   fi
else
   echo "Not configuring DataPusher"
fi
