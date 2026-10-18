"""`xloader_status` without the xloader service token.

xloader 2.6.0 answers `xloader_status` with `task_info`, the job's row from
its jobs table, and that row keeps `api_key`: the token
`01_setup_xloader.sh` mints for the sysadmin, valid for a year.
`db.delete_api_key` exists (`ckanext/xloader/db.py:388`) but nothing calls
it, and the action only needs `resource_update` (`ckanext/xloader/auth.py:16`).
So, unchained, any editor of a dataset can read a sysadmin token through
`/api/3/action/xloader_status`. Measured on the dev stack on 2026-10-18: every
finished job kept its key.

The chain removes the key from the answer and changes nothing else: the
status, the job id and the log lines the portal and CKAN's own status page read
stay as they were. The key is still stored in the jobs table, which only a
database user can read.
"""
import ckan.plugins.toolkit as toolkit


@toolkit.chained_action
@toolkit.side_effect_free
def xloader_status(original_action, context, data_dict):
    answer = original_action(context, data_dict)
    task_info = answer.get("task_info")
    if isinstance(task_info, dict):
        task_info.pop("api_key", None)
    return answer
