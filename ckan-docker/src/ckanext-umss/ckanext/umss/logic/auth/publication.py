"""
Authorization for the four publication actions: `design.md` D4.

Every predicate here is the **stock** CKAN capacity, reused: the extension
defines no permission of its own. `has_user_permission_for_group_or_org`
(`ckan/authz.py`, `def has_user_permission_for_group_or_org(`) already answers
the sysadmin short-circuit and the cascade
down the organization hierarchy, which is why a parent org's admin can decide a
child org's request.

These are plain auth functions for the extension's own action names. The three
*chained* functions that guard core's actions live in `ckanext.umss.auth`, and
are a different mechanism on purpose.

A missing `request_id`, an unresolvable one, or an unresolvable `dataset_id`
answers `success: True` and lets the action raise `NotFound`: a `403` would claim
the caller lacks a capacity, when what happened is that the thing does not
exist. `ckanext/umss/auth.py` follows the same rule for unresolvable package ids.
"""
from __future__ import annotations

import ckan.authz as ckan_authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.umss import model as umss_model
from ckanext.umss.logic import caller_id


__all__ = [
    "publication_request_create",
    "publication_request_cancel",
    "publication_request_decide",
    "publication_request_list",
    "REQUEST_DENIED_MSG",
    "CANCEL_DENIED_MSG",
    "DECIDE_DENIED_MSG",
    "DECIDE_FOUR_EYES_MSG",
    "DECIDE_REQUESTER_CAPACITY_MSG",
    "FOUR_EYES_LABEL",
    "REQUESTER_CAPACITY_LABEL",
    "NOT_AN_APPROVER_LABEL",
    "ALREADY_PUBLIC_LABEL",
    "CANNOT_REQUEST_LABEL",
    "CANNOT_CANCEL_LABEL",
]


# The stock permissions D4's predicates are written in.
UPDATE_PERMISSION = "update_dataset"
ADMIN_PERMISSION = "admin"

# The refusal **labels** are the frozen interface a consumer matches on:
# every message below reads `<label>: <sentence>`, the label is the part that
# must not change, and the sentence after it is free prose. Each label names
# exactly one refusal fact, and all of them are distinct.
FOUR_EYES_LABEL = "Four eyes"
REQUESTER_CAPACITY_LABEL = "Requester capacity"
NOT_AN_APPROVER_LABEL = "Not an approver"
ALREADY_PUBLIC_LABEL = "Already public"
CANNOT_REQUEST_LABEL = "Cannot request"
CANNOT_CANCEL_LABEL = "Cannot cancel"

REQUEST_DENIED_MSG = "%s: %s" % (
    CANNOT_REQUEST_LABEL,
    "only a user who can update this dataset may ask for it to be published",
)
CANCEL_DENIED_MSG = "%s: %s" % (
    CANNOT_CANCEL_LABEL,
    "only the requester or an organization administrator may cancel this request",
)
DECIDE_DENIED_MSG = "%s: %s" % (
    NOT_AN_APPROVER_LABEL,
    "only an organization administrator may decide a publication request",
)
DECIDE_FOUR_EYES_MSG = "%s: %s" % (
    FOUR_EYES_LABEL,
    "the approver cannot be the requester of the request they decide",
)
DECIDE_REQUESTER_CAPACITY_MSG = "%s: %s" % (
    REQUESTER_CAPACITY_LABEL,
    "the requester can no longer update this dataset, so the request cannot "
    "be decided",
)
ALREADY_PUBLIC_MSG = "%s: %s" % (
    ALREADY_PUBLIC_LABEL,
    "that dataset is already public",
)


def _org_id_of_dataset(dataset_id):
    dataset = model.Package.get(dataset_id) if dataset_id else None
    return dataset.owner_org if dataset else None


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


def _requester_holds_capacity(row):
    """Whether the requester **currently** holds what `create` demands for the
    request's dataset.

    `publication_request_create` requires the `update_dataset` permission on
    the dataset's owning organization; this mirrors that predicate, evaluated
    against the dataset's **current** owner and the **requester's** identity
    rather than the caller's. `row.requested_by` is a user id, and
    `has_user_permission_for_group_or_org` resolves a username, so the id is
    resolved to the user first.

    Fails closed: a requester whose id has **no user row at all** — `model.User.get`,
    which does not filter on `state`, so a soft-deleted user still resolves — and a
    dataset with no resolvable owner both answer `False`. There is no escape
    hatch: no caller, the sysadmin included, publishes directly any more, so a
    degraded requester leaves the request undecidable — it stays `pending`.
    """
    if not row.requested_by:
        return False
    requester = model.User.get(row.requested_by)
    owner_org = _org_id_of_dataset(row.dataset_id)
    if requester is None or not owner_org:
        return False
    return ckan_authz.has_user_permission_for_group_or_org(
        owner_org, requester.name, UPDATE_PERMISSION
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
    if row.requested_by and row.requested_by == caller_id(context):
        return {"success": True}
    if _allowed(context, _org_id_of_dataset(row.dataset_id), ADMIN_PERMISSION):
        return {"success": True}
    return {"success": False, "msg": CANCEL_DENIED_MSG}


@toolkit.auth_sysadmins_check
def publication_request_decide(context, data_dict):
    """D4, plus the governance amendment's four eyes and state re-check.

    An org `admin` decides for someone else. Four eyes: the requester cannot
    decide their own request, and sysadmins have no exception either.

    A2.6 also re-checks the requester's **current** capacity against the
    dataset's **current** owning organization, mirrored from
    `publication_request_create`'s predicate. That check has no sysadmin
    exception either, so it runs before the sysadmin branch; and a requester
    whose id has **no user row at all** fails closed rather than opening
    the decision (a soft-deleted user still resolves: `model.User.get` does not
    filter on `state`). The refusal is an authorization failure, so the action
    never runs and the row stays `pending` — it is **not** annulled: the two
    annulment triggers are historical now that direct publish is retired.

    `auth_sysadmins_check` is load-bearing, not decoration. Without it CKAN
    short-circuits every sysadmin to success *before* this function runs
    (`ckan/authz.py`,
    `if not getattr(auth_function, 'auth_sysadmins_check', False):`), leaving
    the rule written, green and hollow for exactly the caller it does not
    exempt. With it, the function runs for a sysadmin too, so it must answer for
    that caller explicitly.
    """
    row = _request_row(data_dict.get("request_id"))
    if row is None:
        return {"success": True}

    caller = caller_id(context)
    is_requester = bool(row.requested_by) and row.requested_by == caller

    # A2.6, requester half: the decision re-checks the requester's **current**
    # capacity, and it has no sysadmin exception — it is placed before the
    # sysadmin branch below so a sysadmin approver is subject to it too.
    if not _requester_holds_capacity(row):
        return {"success": False, "msg": DECIDE_REQUESTER_CAPACITY_MSG}

    if ckan_authz.is_sysadmin(context.get("user")):
        if is_requester:
            return {"success": False, "msg": DECIDE_FOUR_EYES_MSG}
        return {"success": True}

    if not _allowed(context, _org_id_of_dataset(row.dataset_id), ADMIN_PERMISSION):
        return {"success": False, "msg": DECIDE_DENIED_MSG}
    if is_requester:
        return {"success": False, "msg": DECIDE_FOUR_EYES_MSG}
    return {"success": True}


def publication_request_list(context, data_dict):
    """D4: anyone who administers or edits in the org.

    Always allowed, because the action is what narrows the answer to the
    requests the caller may see. It is deliberately **not** flagged
    `auth_allow_anonymous_access`: D4 grants the queue to admins and editors,
    and an anonymous caller is neither.
    """
    return {"success": True}
