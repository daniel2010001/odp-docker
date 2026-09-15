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
      # `--json` forwards extra fields to `api_token_create`; `-q` prints only the token.
      ckan config-tool $CKAN_INI "ckan.datapusher.api_token=$(ckan -c $CKAN_INI user token add ckan_admin datapusher --json '{\"expires_in\": 1, \"unit\": 86400}' -q | tail -n 1 | tr -d '\t')"
   fi
else
   echo "Not configuring DataPusher"
fi
