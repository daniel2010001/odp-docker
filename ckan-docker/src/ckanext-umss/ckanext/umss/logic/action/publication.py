"""
The five publication actions: `design.md` D4, and the door of D5.

They are the queue and the door at once: `publication_request_create` /
`_cancel` / `_decide` / `_list` are the queue, and `_decide {approve: true}` and
`publication_publish` are the only **recorded** way a dataset becomes public.
The wall in `ckanext.umss.auth` refuses a flip by a caller who is neither an
organization admin nor a sysadmin, but it does **not** close the stock
`package_patch {private: false}` route for an organization admin — that caller
is an approver to the wall — so a raw core call is a second, unrecorded door
until `A3` closes it.

They write the record and flip the value in one transaction (D5). Measured: the
update path does not commit on its own — `package_patch`
(`ckan/logic/action/patch.py:17`) delegates to `package_update`
(`ckan/logic/action/update.py:234`) and that file has no `Session.commit()`
between them — so the single commit below is what covers both writes.

`requested_by` and `approved_by` hold **user ids**, CKAN's convention
(`package_show` answers `creator_user_id`).
"""
from __future__ import annotations

import datetime

import ckan.authz as ckan_authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit
import sqlalchemy as sa

from ckanext.umss import model as umss_model
from ckanext.umss.logic import caller_id
from ckanext.umss.logic.auth.publication import UPDATE_PERMISSION


__all__ = [
    "publication_request_create",
    "publication_request_cancel",
    "publication_request_decide",
    "publication_publish",
    "publication_request_list",
]


_Session = model.Session

# Rule 3's neutral fallback: an id that is set but does not resolve to a user
# answers this token, never the raw id. A field called `..._name` must never
# contain an id.
UNKNOWN_NAME = "unknown"


def _now():
    return datetime.datetime.now()


def _required(data_dict, name):
    value = data_dict.get(name)
    if value in (None, ""):
        raise toolkit.ValidationError({name: ["Missing value"]})
    return value


def _existing_dataset(dataset_id):
    """The dataset, or `NotFound`.

    This is what stands between a bogus `dataset_id` and an orphan row: the auth
    functions answer `success` for an unresolvable id on purpose (a `403` would
    misreport a missing thing as a missing capacity), so the check is the
    action's (R1-ORPHAN-ROW, R3-001).
    """
    dataset = model.Package.get(dataset_id)
    if dataset is None:
        raise toolkit.ObjectNotFound("Dataset not found: %s" % dataset_id)
    return dataset


def _names_for(user_ids):
    """Resolve a call's user ids to names in **one** query (rule 3).

    The queue must not resolve N users per page, so the caller collects every
    `requested_by`/`approved_by` in the page and hands them here once; the
    `IN` carries them all. Empty ids are dropped — the caller still decides the
    fallback for an id that is set but does not resolve.
    """
    ids = {user_id for user_id in user_ids if user_id}
    if not ids:
        return {}
    return {
        user_id: name
        for user_id, name in _Session.query(model.User.id, model.User.name)
        .filter(model.User.id.in_(ids))
        .all()
    }


def _name_key(user_id, names):
    """One id column's presentation name, with rule 3's two empties distinct:
    an unset column answers `None`, a set-but-unresolvable id answers
    `"unknown"` (never the id)."""
    if not user_id:
        return None
    return names.get(user_id, UNKNOWN_NAME)


def _row_dict(row, names=None):
    """The row as the API will answer it: the declared columns, keyed by name,
    plus the two presentation names (rule 3).

    `names` is the batched resolution for the whole call (`_names_for`); when it
    is omitted — a single-row return — both id columns are resolved here in one
    query, so one row and a whole page answer the same shape at the same cost.
    """
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    if names is None:
        names = _names_for((row.requested_by, row.approved_by))
    data["requested_by_name"] = _name_key(row.requested_by, names)
    data["approved_by_name"] = _name_key(row.approved_by, names)
    return data


def _request_row(request_id):
    row = _Session.get(umss_model.PublicationRequest, request_id)
    if row is None:
        raise toolkit.ObjectNotFound(
            "Publication request not found: %s" % request_id
        )
    return row


def _pending_for(dataset_id):
    """D2's invariant, read the way the index enforces it."""
    return (
        _Session.query(umss_model.PublicationRequest)
        .filter(umss_model.PublicationRequest.dataset_id == dataset_id)
        .filter(umss_model.PublicationRequest.status == umss_model.PENDING)
        .first()
    )


def _flip_to_public(context, dataset_id):
    """D5's door, from server-side code.

    `logic.get_action`, not `helpers.call_action` — that is the production entry
    point — and `ignore_auth`, because the caller's capacity was already settled
    by this action's own auth function. Without `ignore_auth` the wall would
    refuse the very flip it exists to be the only way through.
    """
    toolkit.get_action("package_patch")(
        {"ignore_auth": True, "user": context.get("user")},
        {"id": dataset_id, "private": False},
    )


def _commit_row(row, context, flip=False):
    """Write the record and, when asked, the flip — in one commit.

    A failure before that commit rolls back and re-raises, so an
    `approved`/`consumed` row cannot survive a flip that did not happen (A2.5).
    """
    _Session.add(row)
    try:
        if flip:
            _flip_to_public(context, row.dataset_id)
        _Session.commit()
    except Exception:
        _Session.rollback()
        raise
    _Session.expire_all()
    return _row_dict(row)


def publication_request_create(context, data_dict):
    """D4: writes one `pending` row, and is idempotent on the pending one."""
    toolkit.check_access("publication_request_create", context, data_dict)
    dataset_id = _required(data_dict, "dataset_id")
    _existing_dataset(dataset_id)

    existing = _pending_for(dataset_id)
    if existing is not None:
        return _row_dict(existing)

    row = umss_model.PublicationRequest(
        dataset_id=dataset_id,
        requested_visibility="public",
        status=umss_model.PENDING,
        requested_by=caller_id(context),
        comments=data_dict.get("comments"),
    )
    try:
        return _commit_row(row, context)
    except sa.exc.IntegrityError:
        # Two identical creates can interleave between the read above and this
        # insert. What settles that race is D2's partial unique index, and the
        # loser answers with the winner's row — that is what "idempotent"
        # promises. Anything else is a genuine integrity error and is re-raised.
        existing = _pending_for(dataset_id)
        if existing is not None:
            return _row_dict(existing)
        raise


def publication_request_cancel(context, data_dict):
    """D4: `pending` → `cancelled`. Never flips the dataset."""
    toolkit.check_access("publication_request_cancel", context, data_dict)
    row = _request_row(_required(data_dict, "request_id"))

    if row.status != umss_model.PENDING:
        raise toolkit.ValidationError(
            {"request_id": ["Only a pending request can be cancelled"]}
        )

    row.status = umss_model.CANCELLED
    return _commit_row(row, context)


def publication_request_decide(context, data_dict):
    """D4: `rejected`, or `approved` **and** the flip, in one transaction."""
    toolkit.check_access("publication_request_decide", context, data_dict)
    row = _request_row(_required(data_dict, "request_id"))

    approve = data_dict.get("approve")
    if not isinstance(approve, bool):
        raise toolkit.ValidationError({"approve": ["Missing value: true or false"]})
    if row.status != umss_model.PENDING:
        raise toolkit.ValidationError(
            {"request_id": ["That request is no longer pending"]}
        )

    if not approve:
        comments = data_dict.get("comments")
        if not isinstance(comments, str) or not comments.strip():
            raise toolkit.ValidationError(
                {"comments": ["Missing value: a rejection must carry a comment"]}
            )

    if data_dict.get("comments"):
        row.comments = data_dict["comments"]
    row.approved_by = caller_id(context)
    row.decided_at = _now()

    if not approve:
        row.status = umss_model.REJECTED
        return _commit_row(row, context)

    row.status = umss_model.APPROVED
    row.consumed_at = _now()
    return _commit_row(row, context, flip=True)


def publication_publish(context, data_dict):
    """The sysadmin's recorded path: one row, born already decided and consumed.

    A pending request for the same dataset is **annulled**, not cancelled: the
    requester did not withdraw it, a direct action by the sysadmin made it moot —
    which is what D2 added `annulled` for.

    Publishing an already public dataset is refused by the auth function, the
    way `publication_request_create` refuses one: the flip would be a no-op and
    the caller would only be piling up identical `approved` rows (A2's review,
    R3-003).
    """
    toolkit.check_access("publication_publish", context, data_dict)
    dataset_id = _required(data_dict, "dataset_id")
    _existing_dataset(dataset_id)

    caller = caller_id(context)
    now = _now()

    moot = _pending_for(dataset_id)
    if moot is not None:
        moot.status = umss_model.ANNULLED
        moot.decided_at = now
        moot.motive = umss_model.MOTIVE_PUBLISHED_BY_ANOTHER_PATH

    row = umss_model.PublicationRequest(
        dataset_id=dataset_id,
        requested_visibility="public",
        status=umss_model.APPROVED,
        requested_by=caller,
        approved_by=caller,
        comments=data_dict.get("comments"),
        decided_at=now,
        consumed_at=now,
    )
    return _commit_row(row, context, flip=True)


@toolkit.side_effect_free
def publication_request_list(context, data_dict):
    """D4's queue: what the caller may see, optionally narrowed by status.

    "May see" is the stock `update_dataset` capacity on the dataset's org, plus
    the caller's own requests. The predicate stays in Python on purpose: the
    stock helper walks the org hierarchy (`ckan/authz.py:302`), and re-writing
    that walk in SQL is how a queue starts showing rows to the wrong org. The
    cost is not per row, though — one query resolves every dataset's org and the
    capacity is evaluated once per organization (R4-list-unbounded).

    Declared limitation: every row of the requested status is still
    materialized, because D4's contract has no pagination. If the portal's queue
    needs paging, that is a contract change and it belongs to B2.
    """
    query = _Session.query(umss_model.PublicationRequest)
    status = data_dict.get("status")
    if status:
        query = query.filter(umss_model.PublicationRequest.status == status)
    rows = query.order_by(umss_model.PublicationRequest.created_at).all()

    caller = caller_id(context)
    org_of = _orgs_by_dataset(row.dataset_id for row in rows)
    allowed_orgs = {}

    def may_see(dataset_id):
        org_id = org_of.get(dataset_id)
        if org_id is None:
            return False
        if org_id not in allowed_orgs:
            allowed_orgs[org_id] = ckan_authz.has_user_permission_for_group_or_org(
                org_id, context.get("user"), UPDATE_PERMISSION
            )
        return allowed_orgs[org_id]

    visible = [
        row
        for row in rows
        if row.requested_by == caller or may_see(row.dataset_id)
    ]
    names = _names_for(
        user_id
        for row in visible
        for user_id in (row.requested_by, row.approved_by)
    )
    return [_row_dict(row, names) for row in visible]


def _orgs_by_dataset(dataset_ids):
    """`{dataset_id: owner_org}` for the given ids, in one query."""
    wanted = set(dataset_ids)
    if not wanted:
        return {}
    return dict(
        _Session.query(model.Package.id, model.Package.owner_org)
        .filter(model.Package.id.in_(wanted))
        .all()
    )
