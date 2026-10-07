"""
Authorization for the five publication actions: `design.md` D4.

Every predicate here is the **stock** CKAN capacity, reused: the extension
defines no permission of its own. `has_user_permission_for_group_or_org`
(`ckan/authz.py:302`) already answers the sysadmin short-circuit and the cascade
down the organization hierarchy, which is why a parent org's admin can decide a
child org's request.

These are plain auth functions for the extension's own action names. The two
*chained* functions that guard core's actions live in `ckanext.umss.auth`, and
are a different mechanism on purpose.

A missing `request_id` or an unresolvable one answers `success: True` and lets
the action raise `NotFound`: a `403` would claim the caller lacks a capacity,
when what happened is that the thing does not exist. `ckanext/umss/auth.py`
follows the same rule for unresolvable package ids.
"""
from __future__ import annotations

import ckan.authz as ckan_authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.umss import model as umss_model


__all__ = [
    "publication_request_create",
    "publication_request_cancel",
    "publication_request_decide",
    "publication_publish",
    "publication_request_list",
    "REQUEST_DENIED_MSG",
    "CANCEL_DENIED_MSG",
    "DECIDE_DENIED_MSG",
    "DECIDE_FOUR_EYES_MSG",
    "PUBLISH_DENIED_MSG",
]


# The stock permissions D4's predicates are written in.
UPDATE_PERMISSION = "update_dataset"
ADMIN_PERMISSION = "admin"

REQUEST_DENIED_MSG = (
    "Only a user who can update this dataset may ask for it to be published"
)
CANCEL_DENIED_MSG = (
    "Only the requester or an organization administrator may cancel this request"
)
DECIDE_DENIED_MSG = (
    "Only an organization administrator may decide a publication request"
)
DECIDE_FOUR_EYES_MSG = (
    "Four eyes: the approver cannot be the requester of the request they decide"
)
PUBLISH_DENIED_MSG = (
    "Only a sysadmin may publish a dataset directly"
)
ALREADY_PUBLIC_MSG = "That dataset is already public"


def _user_id(context):
    user = context.get("auth_user_obj") or model.User.get(context.get("user"))
    return user.id if user else None


def _org_id_of_dataset(dataset_id):
    dataset = model.Package.get(dataset_id) if dataset_id else None
    return dataset.owner_org if dataset else None


def _org_id_of_request(request_id):
    row = _request_row(request_id)
    return _org_id_of_dataset(row.dataset_id) if row else None


def _request_row(request_id):
    if not request_id:
        return None
    return model.Session.get(umss_model.PublicationRequest, request_id)


def _allowed(context, org_id, permission):
    if not org_id:
        return False
    return ckan_authz.has_user_permission_for_group_or_org(
        org_id, context.get("user"), permission
    )


def publication_request_create(context, data_dict):
    """D4: a caller who can `update_dataset` in the owning org, on a private
    dataset."""
    dataset_id = data_dict.get("dataset_id")
    dataset = model.Package.get(dataset_id) if dataset_id else None
    if dataset is None:
        return {"success": True}
    if not dataset.private:
        return {"success": False, "msg": ALREADY_PUBLIC_MSG}
    if not _allowed(context, dataset.owner_org, UPDATE_PERMISSION):
        return {"success": False, "msg": REQUEST_DENIED_MSG}
    return {"success": True}


def publication_request_cancel(context, data_dict):
    """D4: the requester, or an org `admin`."""
    row = _request_row(data_dict.get("request_id"))
    if row is None:
        return {"success": True}
    if row.requested_by and row.requested_by == _user_id(context):
        return {"success": True}
    if _allowed(context, _org_id_of_dataset(row.dataset_id), ADMIN_PERMISSION):
        return {"success": True}
    return {"success": False, "msg": CANCEL_DENIED_MSG}


@toolkit.auth_sysadmins_check
def publication_request_decide(context, data_dict):
    """D4, plus the governance amendment's four eyes.

    An org `admin` decides for someone else. Four eyes: the requester cannot
    decide their own request, and sysadmins have no exception — the sanctioned
    path for a sysadmin who requested is `publication_publish`.

    `auth_sysadmins_check` is load-bearing, not decoration. Without it CKAN
    short-circuits every sysadmin to success *before* this function runs
    (`ckan/authz.py:224-228`), leaving the rule written, green and hollow for
    exactly the caller it does not exempt. With it, the function runs for a
    sysadmin too, so it must answer for that caller explicitly.
    """
    row = _request_row(data_dict.get("request_id"))
    if row is None:
        return {"success": True}

    caller = _user_id(context)
    is_requester = bool(row.requested_by) and row.requested_by == caller

    if ckan_authz.is_sysadmin(context.get("user")):
        if is_requester:
            return {"success": False, "msg": DECIDE_FOUR_EYES_MSG}
        return {"success": True}

    if not _allowed(context, _org_id_of_dataset(row.dataset_id), ADMIN_PERMISSION):
        return {"success": False, "msg": DECIDE_DENIED_MSG}
    if is_requester:
        return {"success": False, "msg": DECIDE_FOUR_EYES_MSG}
    return {"success": True}


def publication_publish(context, data_dict):
    """The governance amendment: a sysadmin publishing on their own authority.

    The organization-admin direct path through this action is closed. The org
    admin keeps the stock `package_patch {private: false}` route, which the wall
    in `ckanext.umss.auth` owns; this action is a sysadmin's.

    The dataset is resolved first, because contract rule 7 requires an
    unresolvable `dataset_id` to answer `NotFound`, not `403`: the auth answers
    `success` for an unknown id and the action raises `NotFound`
    (R1-ORPHAN-ROW). Only the capacity predicate below narrows the caller to a
    sysadmin.
    """
    dataset = model.Package.get(data_dict.get("dataset_id") or "")
    if dataset is None:
        return {"success": True}
    if not ckan_authz.is_sysadmin(context.get("user")):
        return {"success": False, "msg": PUBLISH_DENIED_MSG}
    return {"success": True}


def publication_request_list(context, data_dict):
    """D4: anyone who administers or edits in the org.

    Always allowed, because the action is what narrows the answer to the
    requests the caller may see. It is deliberately **not** flagged
    `auth_allow_anonymous_access`: D4 grants the queue to admins and editors,
    and an anonymous caller is neither.
    """
    return {"success": True}
