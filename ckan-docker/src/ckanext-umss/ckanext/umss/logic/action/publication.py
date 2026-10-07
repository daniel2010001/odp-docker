"""
The five publication actions: `design.md` D4, and the door of D5.

They are the queue and the door at once: `publication_request_create` /
`_cancel` / `_decide` / `_list` are the queue, and `_decide {approve: true}` and
`publication_publish` are the **only** way a dataset becomes public — the wall in
`ckanext.umss.auth` refuses every other flip, including an org admin's.

`_decide {approve: true}` and `_publish` write the record **and** flip the value
in one transaction (D5): the row is added to the session and `package_patch` is
called with `ignore_auth` before the single commit, so a flip that fails leaves
no `approved`/`consumed` row behind.

`requested_by` and `approved_by` hold **user ids**, the convention the rest of
CKAN follows (`package_show` answers `creator_user_id`, not a name).
"""
from __future__ import annotations

import datetime

import ckan.authz as ckan_authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.umss import model as umss_model
from ckanext.umss.logic.auth.publication import UPDATE_PERMISSION


__all__ = [
    "publication_request_create",
    "publication_request_cancel",
    "publication_request_decide",
    "publication_publish",
    "publication_request_list",
]


_Session = model.Session


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


def _caller_id(context):
    user = context.get("auth_user_obj") or model.User.get(context.get("user"))
    return user.id if user else None


def _row_dict(row):
    """The row as the API will answer it: the declared columns, keyed by name."""
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


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
    """Write the record and, when asked, the flip — in one transaction.

    `package_patch` commits the session it is handed, so anything that fails
    before that commit leaves the row uncommitted; the rollback is what makes
    "either both writes or neither" true rather than merely asserted (A2.5).
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
        requested_by=_caller_id(context),
        comments=data_dict.get("comments"),
    )
    return _commit_row(row, context)


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
    row.approved_by = _caller_id(context)
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
    """
    toolkit.check_access("publication_publish", context, data_dict)
    dataset_id = _required(data_dict, "dataset_id")
    _existing_dataset(dataset_id)

    caller = _caller_id(context)
    now = _now()

    moot = _pending_for(dataset_id)
    if moot is not None:
        moot.status = umss_model.ANNULLED
        moot.decided_at = now

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
    the caller's own requests — a requester keeps seeing their own row even if
    their capacity is gone.
    """
    query = _Session.query(umss_model.PublicationRequest)
    status = data_dict.get("status")
    if status:
        query = query.filter(umss_model.PublicationRequest.status == status)
    rows = query.order_by(umss_model.PublicationRequest.created_at).all()

    caller = _caller_id(context)
    return [
        _row_dict(row)
        for row in rows
        if row.requested_by == caller or _may_see(context, row.dataset_id)
    ]


def _may_see(context, dataset_id):
    dataset = model.Package.get(dataset_id)
    if dataset is None:
        return False
    return ckan_authz.has_user_permission_for_group_or_org(
        dataset.owner_org, context.get("user"), UPDATE_PERMISSION
    )
