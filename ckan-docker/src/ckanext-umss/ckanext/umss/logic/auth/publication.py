"""
Authorization for the four publication actions: `design.md` D4.

Every predicate here is the **stock** CKAN capacity, reused: the extension
defines no permission of its own. `has_user_permission_for_group_or_org`
(`ckan/authz.py`, `def has_user_permission_for_group_or_org(`) already answers
the sysadmin short-circuit and the cascade
down the organization hierarchy, which is why a parent org's admin can decide a
child org's request.

`publication_request_decide` carries one rule the other three do not: four eyes
is **conditional**. The requester cannot decide their own request **unless no
other person of the organization could decide it**, and that absence is measured
over the organization's **memberships** — never over effective permissions,
because those cascade down the hierarchy here. The platform's sysadmins are not
that other person either: CKAN makes the creator of an organization an `admin`
member of it (`ckan/logic/action/create.py`,
`# creator of group/org becomes an admin`), so counting them would leave every
organization's admin unable to approve their own request, which is the case the
exception exists for.

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
    "decide_cache",
    "decide_reason",
    "REQUEST_DENIED_MSG",
    "CANCEL_DENIED_MSG",
    "DECIDE_DENIED_MSG",
    "DECIDE_FOUR_EYES_MSG",
    "DECIDE_REQUESTER_CAPACITY_MSG",
    "DECIDE_ORG_ADMIN_MSG",
    "FOUR_EYES_LABEL",
    "REQUESTER_CAPACITY_LABEL",
    "NOT_AN_APPROVER_LABEL",
    "ORG_ADMIN_DECIDES_LABEL",
    "ALREADY_PUBLIC_LABEL",
    "CANNOT_REQUEST_LABEL",
    "CANNOT_CANCEL_LABEL",
]


# The stock permissions D4's predicates are written in.
UPDATE_PERMISSION = "update_dataset"
ADMIN_PERMISSION = "admin"

# The member table's own vocabulary. It is polymorphic — `table_name` says what
# `table_id` points at, and for a user membership `table_id` holds the user **id**
# (`member_create` builds `table_id=obj.id`) — so a query over memberships has to
# name the kind it wants. `MEMBER_STATE_ACTIVE` is the whole distinction that
# matters here: a `pending` or `deleted` membership is not a person who can
# approve anything.
MEMBER_TABLE_NAME = "user"
MEMBER_STATE_ACTIVE = "active"

# The refusal **labels** are the frozen interface a consumer matches on:
# every message below reads `<label>: <sentence>`, the label is the part that
# must not change, and the sentence after it is free prose. Each label names
# exactly one refusal fact, and all of them are distinct.
FOUR_EYES_LABEL = "Four eyes"
REQUESTER_CAPACITY_LABEL = "Requester capacity"
NOT_AN_APPROVER_LABEL = "Not an approver"
ORG_ADMIN_DECIDES_LABEL = "Org admin decides"
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
DECIDE_ORG_ADMIN_MSG = "%s: %s" % (
    ORG_ADMIN_DECIDES_LABEL,
    "this organization has its own administrator, who decides its requests",
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


def _allowed(context, org_id, permission, cache=None):
    """The stock capacity, memoized per organization when a projection asks for
    the same caller and organization more than once (the queue does, once per
    row of the page).
    """
    if not org_id:
        return False
    if cache is None:
        cache = {}
    return _memo(
        cache,
        "caller_allowed",
        (permission, org_id),
        lambda: ckan_authz.has_user_permission_for_group_or_org(
            org_id, context.get("user"), permission
        ),
    )


def _memo(cache, bucket, key, compute):
    """Resolve `key` once per call and keep it for the rest of the call.

    The cache is the caller's, never the module's: it holds this call's caller
    and this call's requester, so a shared one would answer for the wrong user.
    """
    seen = cache.setdefault(bucket, {})
    if key not in seen:
        seen[key] = compute()
    return seen[key]


def _requester_holds_capacity(row, cache=None):
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

    The exception of S1 is not an exception to this: it lifts four eyes, and
    this check has none (S5).
    """
    if not row.requested_by:
        return False
    if cache is None:
        cache = {}
    return _memo(
        cache,
        "requester_capacity",
        (row.requested_by, _org_id_of_dataset(row.dataset_id)),
        lambda: _requester_holds_capacity_now(row),
    )


def _requester_holds_capacity_now(row):
    """The uncached resolution `_requester_holds_capacity` memoizes."""
    requester = model.User.get(row.requested_by)
    owner_org = _org_id_of_dataset(row.dataset_id)
    if requester is None or not owner_org:
        return False
    return ckan_authz.has_user_permission_for_group_or_org(
        owner_org, requester.name, UPDATE_PERMISSION
    )


def _is_sysadmin_caller(context, cache):
    return _memo(
        cache,
        "caller",
        "sysadmin",
        lambda: bool(ckan_authz.is_sysadmin(context.get("user"))),
    )


def _is_requester(row, context, cache):
    caller = _memo(cache, "caller", "id", lambda: caller_id(context))
    return bool(row.requested_by) and row.requested_by == caller


def _caller_is_an_approver(context, row, cache):
    """Whether the caller holds a capacity that decides at all: a platform
    sysadmin, or an `admin` of the dataset's currently owning organization.

    S1's exception is not an alternative to this gate. It lifts a refusal, it
    never grants a capacity, so a caller who is not an approver keeps reading
    `Not an approver` whether or not they opened the request.
    """
    if _is_sysadmin_caller(context, cache):
        return True
    return _allowed(
        context, _org_id_of_dataset(row.dataset_id), ADMIN_PERMISSION, cache
    )


def _org_admin_facts(org_ids):
    """`{org_id: {"approvers": {user_id}, "unresolvable": bool}}` for the given
    organizations, in **one** statement.

    Memberships, never effective permissions. `has_user_permission_for_group_or_org`
    cascades down the organization hierarchy whenever
    `ckan.roles_that_cascade_to_sub_groups` names the capacity — it names `admin`
    on this instance — so the permission-shaped question answers yes for an
    `admin` of a **related** organization. The relation between organizations is
    figurative here and the exception may not count it, so this asks the member
    table about **this** organization and nothing else.

    The join to the user row is an outer one because `member.table_id` carries no
    foreign key: a membership whose user does not resolve is not a person we can
    rule out, and `unresolvable` is how that gets said. The platform's sysadmins
    are dropped here, which is also why the system account `default` needs no
    name, no allow-list and no guess about its credentials: a sysadmin is not a
    second person of the organization.
    """
    facts = {org_id: {"approvers": set(), "unresolvable": False} for org_id in org_ids}
    if not org_ids:
        return facts
    rows = (
        model.Session.query(
            model.Member.group_id,
            model.Member.table_id,
            model.User.id,
            model.User.sysadmin,
        )
        .outerjoin(model.User, model.User.id == model.Member.table_id)
        .filter(model.Member.group_id.in_(list(org_ids)))
        .filter(model.Member.table_name == MEMBER_TABLE_NAME)
        .filter(model.Member.capacity == ADMIN_PERMISSION)
        .filter(model.Member.state == MEMBER_STATE_ACTIVE)
        .all()
    )
    for group_id, table_id, user_id, is_sysadmin in rows:
        entry = facts.setdefault(
            group_id, {"approvers": set(), "unresolvable": False}
        )
        if user_id is None:
            entry["unresolvable"] = True
        elif not is_sysadmin:
            entry["approvers"].add(table_id)
    return facts


def _org_admin_facts_for(cache, org_ids):
    """Fill `cache` with the facts of `org_ids` in one statement for the whole
    set, keeping the ids it already holds."""
    held = cache.setdefault("org_admins", {})
    missing = {org_id for org_id in org_ids if org_id} - set(held)
    if missing:
        held.update(_org_admin_facts(missing))
    return held


def _another_person_can_approve(row, cache):
    """S1's count: whether some **other person of the dataset's currently owning
    organization** could decide this request. `True` means four eyes stays.

    The requester's own membership is not a second person, and neither is a
    sysadmin — the exclusion is by role, which is a fact of the system, so no
    account name enters this predicate.

    Fails closed: an organization the facts say nothing resolvable about — no
    owning organization, or a membership the user table cannot resolve — answers
    `True`, because the absence of a second person is what has to be established
    before an approval may be self-approved, and an unestablished count must
    never concede.
    """
    org_id = _org_id_of_dataset(row.dataset_id)
    if not org_id:
        return True
    facts = _org_admin_facts_for(cache, [org_id])[org_id]
    if facts["unresolvable"]:
        return True
    return bool(facts["approvers"] - {row.requested_by})


def _sysadmin_defers_to_org_admin(context, row, cache):
    """Whether the caller is a sysadmin who must leave this request to the
    organization's own administrator: the owning organization has an active
    non-sysadmin `admin` member and the caller is not an active `admin` member
    of it themselves (adding oneself as one is the auditable way in).

    Fails closed: a membership the user table cannot resolve may be an
    administrator, so it counts as one. Memberships of the owning organization
    only, never the cascade: a parent organization's admins do not make a
    sysadmin defer, and are not what the sysadmin defers to.
    """
    if not _is_sysadmin_caller(context, cache):
        return False
    org_id = _org_id_of_dataset(row.dataset_id)
    if not org_id:
        return False
    facts = _org_admin_facts_for(cache, [org_id])[org_id]
    if not (facts["approvers"] or facts["unresolvable"]):
        return False
    caller = _memo(cache, "caller", "id", lambda: caller_id(context))
    return not _memo(
        cache,
        "caller_admin_member",
        (org_id, caller),
        lambda: _is_admin_member(org_id, caller),
    )


def _is_admin_member(org_id, user_id):
    if not user_id:
        return False
    return (
        model.Session.query(model.Member.id)
        .filter(model.Member.group_id == org_id)
        .filter(model.Member.table_id == user_id)
        .filter(model.Member.table_name == MEMBER_TABLE_NAME)
        .filter(model.Member.capacity == ADMIN_PERMISSION)
        .filter(model.Member.state == MEMBER_STATE_ACTIVE)
        .first()
        is not None
    )


def decide_cache(org_ids=()):
    """A per-call memo for `decide_reason`, seeded with every organization the
    call is about to ask about.

    A caller that projects a whole page hands every organization it will ask
    about here, and then the membership facts cost **one** statement for the
    page; a caller with a single row leaves it empty and pays one. Do not keep
    it: it caches this call's caller too.
    """
    cache = {}
    _org_admin_facts_for(cache, org_ids)
    return cache


def decide_reason(context, row, cache=None):
    """Whether `publication_request_decide` authorizes **this caller** on
    **this row**, and why not if it does not: `(allowed, msg)`.

    The one copy of the decide rule, so the predicate and the field a consumer
    reads out of a row cannot drift apart (S4).

    The order is the rule, and it is the landed order with only the four-eyes
    step amended. The requester's **current** capacity comes first and has no
    exception (S5). Then the caller has to be an approver at all — the refusal
    for a caller who is not one is `Not an approver`, four eyes being reserved
    for a requester who holds the capacity. Only then four eyes, and it now
    refuses **if and only if** another person of the organization could decide
    the request. Last, a sysadmin who is not an admin of the organization
    defers to the organization's own administrator, if it has one.
    """
    if cache is None:
        cache = {}
    if not _requester_holds_capacity(row, cache):
        return False, DECIDE_REQUESTER_CAPACITY_MSG
    if not _caller_is_an_approver(context, row, cache):
        return False, DECIDE_DENIED_MSG
    if _is_requester(row, context, cache) and _another_person_can_approve(row, cache):
        return False, DECIDE_FOUR_EYES_MSG
    if _sysadmin_defers_to_org_admin(context, row, cache):
        return False, DECIDE_ORG_ADMIN_MSG
    return True, None


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
    """D4, plus the governance amendment's conditional four eyes and the state
    re-check.

    An org `admin` decides for someone else. Four eyes: the requester cannot
    decide their own request, **unless no other person of the organization can
    decide it** — the exception the author chose on 2026-10-15, and the only rule
    this predicate changed. Sysadmins have no exception of their own here, and
    they are not the "other person" either: the platform's administration is not
    the organization reviewing itself.

    A2.6 also re-checks the requester's **current** capacity against the
    dataset's **current** owning organization, mirrored from
    `publication_request_create`'s predicate. That check has no sysadmin
    exception either, so it runs before the sysadmin is consulted as an approver;
    and a requester
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
    that caller explicitly — and the exception above makes that answer sharper,
    not weaker: a sysadmin who is the only person of the organization may now
    decide their own request, and one who is not still may not.

    The body is `decide_reason` so that this verdict and the `decidable_by_caller`
    a consumer reads out of a row are one rule, not two (S4).
    """
    row = _request_row(data_dict.get("request_id"))
    if row is None:
        return {"success": True}

    allowed, msg = decide_reason(context, row)
    if allowed:
        return {"success": True}
    return {"success": False, "msg": msg}


def publication_request_list(context, data_dict):
    """D4: anyone who administers or edits in the org.

    Always allowed, because the action is what narrows the answer to the
    requests the caller may see. It is deliberately **not** flagged
    `auth_allow_anonymous_access`: D4 grants the queue to admins and editors,
    and an anonymous caller is neither.
    """
    return {"success": True}
