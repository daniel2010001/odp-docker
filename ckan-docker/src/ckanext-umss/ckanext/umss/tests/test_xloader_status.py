"""`xloader_status` must not hand out the xloader service token.

xloader 2.6.0 answers `xloader_status` with `task_info`, the row of its jobs
table, and that row keeps `api_key`: the token `01_setup_xloader.sh` mints for
the sysadmin. `db.delete_api_key` exists (`ckanext/xloader/db.py:388`) but
nothing calls it, and the action's auth is `resource_update`
(`ckanext/xloader/auth.py:16`), so any editor of a dataset could read a
sysadmin token through the API. Measured on the dev stack on 2026-10-18: every
finished job kept its key.

The CKAN test image has no xloader installed, so these tests drive the chained
function with a stand-in for the original action, and the registration through
`plugins.plugin_loaded`.
"""
import copy

import ckan.plugins as plugins

from ckanext.umss import plugin as umss_plugin
from ckanext.umss.logic.action import xloader as xloader_actions

SERVICE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJ4bG9hZGVyIn0.c2lnbmF0dXJl"

UMSS_OWN_ACTIONS = {
    "publication_request_create",
    "publication_request_cancel",
    "publication_request_decide",
    "publication_request_list",
    "umss_chart_data",
}


def _status_with_token():
    return {
        "status": "complete",
        "job_id": "job-1",
        "job_url": None,
        "last_updated": "2026-10-18T03:00:00",
        "task_info": {
            "job_id": "job-1",
            "status": "complete",
            "api_key": SERVICE_TOKEN,
            "metadata": {"resource_id": "res-1"},
            "logs": [{"message": "Express Load completed"}],
        },
        "error": None,
    }


def test_the_status_drops_the_api_key_and_keeps_everything_else():
    calls = []

    def original(context, data_dict):
        calls.append((context, data_dict))
        return _status_with_token()

    answer = xloader_actions.xloader_status(original, {"user": "u"}, {"resource_id": "res-1"})

    expected = _status_with_token()
    del expected["task_info"]["api_key"]
    assert answer == expected
    assert SERVICE_TOKEN not in repr(answer)
    assert calls == [({"user": "u"}, {"resource_id": "res-1"})]


def test_a_status_without_task_info_is_returned_unchanged():
    before_any_job = _status_with_token()
    before_any_job["task_info"] = None

    answer = xloader_actions.xloader_status(
        lambda context, data_dict: copy.deepcopy(before_any_job), {}, {"resource_id": "res-1"}
    )

    assert answer == before_any_job


def test_the_chained_status_stays_a_get_action():
    # `xloader_status` is read with GET; a chained action keeps the attributes
    # of the function that is registered (`ckan/logic/__init__.py`, the loop
    # over `func.__dict__`), so the chain must carry `side_effect_free` itself.
    assert getattr(xloader_actions.xloader_status, "side_effect_free", False) is True
    assert getattr(xloader_actions.xloader_status, "chained_action", False) is True


def test_the_chain_is_registered_only_when_xloader_is_loaded(monkeypatch):
    # Chaining an action nobody defines makes CKAN raise `NotFound` at load, so
    # an install without xloader (the CKAN test image) must not get the chain.
    plugin = umss_plugin.UmssPlugin()

    monkeypatch.setattr(plugins, "plugin_loaded", lambda name: name == "xloader")
    with_xloader = plugin.get_actions()
    assert with_xloader["xloader_status"] is xloader_actions.xloader_status
    assert UMSS_OWN_ACTIONS <= set(with_xloader)

    monkeypatch.setattr(plugins, "plugin_loaded", lambda name: False)
    without_xloader = plugin.get_actions()
    assert "xloader_status" not in without_xloader
    assert set(without_xloader) == UMSS_OWN_ACTIONS
