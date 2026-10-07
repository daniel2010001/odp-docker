"""
Authorization for the five publication actions: `design.md` D4.

Every predicate here is the **stock** CKAN capacity, reused: the extension
defines no permission of its own. `has_user_permission_for_group_or_org`
(`ckan/authz.py:302`) already answers the sysadmin short-circuit and the cascade
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
    "publication_publish",
    "publication_request_list",
    "REQUEST_DENIED_MSG",
    "CANCEL_DENIED_MSG",
    "DECIDE_DENIED_MSG",
    "DECIDE_FOUR_EYES_MSG",
    "DECIDE_REQUESTER_CAPACITY_MSG",
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
DECIDE_REQUESTER_CAPACITY_MSG = (
    "The requester no longer has permission to update this dataset, so the "
    "request cannot be decided"
)
PUBLISH_DENIED_MSG = (
    "Only a sysadmin may publish a dataset directly"
)
ALREADY_PUBLIC_MSG = "That dataset is already public"


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
    dataset with no resolvable owner both answer `False`. The escape hatch when the
    requester is gone or degraded is the sysadmin's `publication_publish`, which
    annuls the pending row and publishes in the same act — never an open
    decision.
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
    decide their own request, and sysadmins have no exception — the sanctioned
    path for a sysadmin who requested is `publication_publish`.

    A2.6 also re-checks the requester's **current** capacity against the
    dataset's **current** owning organization, mirrored from
    `publication_request_create`'s predicate. That check has no sysadmin
    exception either, so it runs before the sysadmin branch; and a requester
    whose id has **no user row at all** fails closed rather than opening
    the decision (a soft-deleted user still resolves: `model.User.get` does not
    filter on `state`). The refusal is an authorization failure, so the action
    never runs and the row stays `pending` — it is **not** annulled: the two
    annulment triggers are the deleted dataset and the other publish path.

    `auth_sysadmins_check` is load-bearing, not decoration. Without it CKAN
    short-circuits every sysadmin to success *before* this function runs
    (`ckan/authz.py:224-228`), leaving the rule written, green and hollow for
    exactly the caller it does not exempt. With it, the function runs for a
    sysadmin too, so it must answer for that caller explicitly.
    """
    row = _request_row(data_dict.get("request_id"))
    if row is None:
        return {"success": True}

    caller = caller_id(context)
    is_requester = bool(row.requested_by) and row.requested_by == caller

    # A2.6, requester half: the decision re-checks the requester's **current**
    # capacity, and it has no sysadmin exception — it is placed before the
    # sysadmin branch below so a sysadmin approver is subject to it too. The
    # escape hatch for a degraded requester is `publication_publish`.
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


@toolkit.auth_sysadmins_check
def publication_publish(context, data_dict):
    """The governance amendment: a sysadmin publishing on their own authority.

    The organization-admin direct path through this action is closed, and the
    stock `package_patch {private: false}` route is closed too: the wall in
    `ckanext.umss.auth` refuses it for the org admin (and for every other
    caller below a sysadmin) with `PUBLISH_VIA_FLOW_MSG`. This action is a
    sysadmin's.

    The dataset is resolved first, because contract rule 7 requires an
    unresolvable `dataset_id` to answer `NotFound`, not `403`: the auth answers
    `success` for an unknown id and the action raises `NotFound`
    (R1-ORPHAN-ROW). Only the capacity predicate below narrows the caller to a
    sysadmin.

    An already public dataset is refused here, the way
    `publication_request_create` refuses one: the flip would be a no-op and the
    caller would only be piling up identical `approved` rows (A2's review,
    R3-003).

    The **capacity** predicate runs before the **state** predicate, so a
    non-sysadmin is denied as a non-sysadmin whatever the dataset's visibility.
    That preserves master's answer for every non-sysadmin exactly — the same
    caller, the same `PUBLISH_DENIED_MSG` — and lets the state guard narrow only
    the sysadmin, the one caller the amendment lets through.

    `auth_sysadmins_check` is load-bearing for that state guard, exactly as it
    is for `publication_request_decide`. Without it CKAN short-circuits every
    sysadmin to success *before* this function runs (`ckan/authz.py:224-228`);
    since the amendment makes this action sysadmin-only, the already-public
    refusal would then be unreachable for every caller who can reach the action
    at all. With it, the function runs for a sysadmin too and must answer for
    that caller explicitly — which it does by returning `success` once the
    dataset is private.
    """
    dataset = model.Package.get(data_dict.get("dataset_id") or "")
    if dataset is None:
        return {"success": True}
    if not ckan_authz.is_sysadmin(context.get("user")):
        return {"success": False, "msg": PUBLISH_DENIED_MSG}
    if not dataset.private:
        return {"success": False, "msg": ALREADY_PUBLIC_MSG}
    return {"success": True}


def publication_request_list(context, data_dict):
    """D4: anyone who administers or edits in the org.

    Always allowed, because the action is what narrows the answer to the
    requests the caller may see. It is deliberately **not** flagged
    `auth_allow_anonymous_access`: D4 grants the queue to admins and editors,
    and an anonymous caller is neither.
    """
    return {"success": True}
