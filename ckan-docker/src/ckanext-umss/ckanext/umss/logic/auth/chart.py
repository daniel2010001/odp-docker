"""
Authorization for `umss_chart_data`.

The action has two call forms and therefore two capacities, and neither one is
invented here:

* the **visitor** form (`view_id`) is authorized exactly as
  `resource_view_show` is — core's own function, reached through
  `authz.is_authorized`, which resolves the view to its resource and asks
  `resource_show`;
* the **editor** form (`resource_id`, `config`) is authorized as
  `resource_update` is, which delegates to `package_update` and therefore runs
  this extension's publication wall (`ckanext.umss.auth`) on the way. That the
  wall lets an ordinary organization editor through is a test, not an
  assumption: without it no chart could ever be configured.

CKAN raises `ValueError('Authorization function not found: ...')` for an action
with no auth function (`ckan/authz.py:235-254`), so this module is what makes
the action callable at all.

Two things about the shape of this function that are deliberate:

* **`auth_allow_anonymous_access`** is declared. CKAN whitelists only the
  functions defined in `ckan/logic/auth/get.py` for anonymous callers
  (`ckan/authz.py`, `_build`); a plugin's function is refused before it runs
  without this flag. The visitor form has to work for an anonymous caller on a
  public dataset, and the editor form still refuses them — the flag opens the
  door to this function, not to the action's two capacities.
* **`is_authorized`, not `check_access`.** `check_access` is what the action
  calls for this action, and it preloads `context['auth_user_obj']` before
  running; a nested `check_access` here would raise instead of answering, and
  would also reset the audit stack. `is_authorized` is the same decision
  without the raising.
"""
from __future__ import annotations

import ckan.authz as ckan_authz
import ckan.plugins.toolkit as toolkit


__all__ = ["umss_chart_data"]


@toolkit.auth_allow_anonymous_access
def umss_chart_data(context, data_dict):
    """`umss_chart_data`, authorized by the form the caller used.

    A request with neither form, or with both, answers `success`: the action
    refuses that request itself, keyed `view_id`/`resource_id`, and a `403`
    there would report a missing capacity instead of a malformed request.
    """
    data_dict = data_dict or {}
    if data_dict.get("view_id"):
        return ckan_authz.is_authorized(
            "resource_view_show", context, {"id": data_dict["view_id"]}
        )
    if data_dict.get("resource_id"):
        return ckan_authz.is_authorized(
            "resource_update", context, {"id": data_dict["resource_id"]}
        )
    return {"success": True}
