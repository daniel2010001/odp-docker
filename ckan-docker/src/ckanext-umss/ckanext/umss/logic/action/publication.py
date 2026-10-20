"""
The four publication actions: `design.md` D4, and the door of D5.

They are the queue and the door at once: `publication_request_create` /
`_cancel` / `_decide` / `_list` are the queue, and `_decide {approve: true}` is
the only **recorded** way a dataset becomes public. There is no direct publish
action: the author retired `publication_publish`, and the wall in
`ckanext.umss.auth` refuses a flip by every caller, the sysadmin included — the
stock `package_patch {private: false}` route is refused, as is a change to
`state` below a sysadmin, `package_create` cannot store a public dataset, and
`bulk_update_public` is covered by a chain of its own, which answers every
caller directly because it does not call `next_auth`. Core itself refuses
`member`, a cross-organization `editor` and anonymous callers before the wall
runs. So no raw core call is a second, unrecorded door: the flow's own flip is
the one write that bypasses the wall (`ignore_auth`), and it is the sanctioned
door. That door **completes the publication**: CKAN grants the `public` label to
a dataset that is active and not private, so a `draft` dataset whose visibility
is flipped would be published and invisible at once. See `_flip_to_public`.

They write the record and flip the value in one commit (D5) because
`package_patch` commits the session it is handed. Measured against the CKAN
source: `package_patch` (`ckan/logic/action/patch.py`,
`_get_action('package_update')(update_context, patched)`) delegates to
`package_update` (`ckan/logic/action/update.py`, `def package_update(`), which
calls `model.repo.commit()` (`ckan/logic/action/update.py`,
`model.repo.commit()`) unless `context['defer_commit']` is set, and `patch.py`
never sets it. `model.repo.commit` is `model.Session.commit`
(`ckan/model/__init__.py`, `self.commit = session.commit` and
`repo = Repository(meta.metadata, meta.Session)`), the same session this module
holds as `_Session`, and `package_patch`'s context is given that session by
`_prepopulate_context` (`ckan/logic/__init__.py`,
`context.setdefault('session', model.Session)`). So the staged row is
committed by the flip's own commit, and the trailing `_Session.commit()` below
is a second, no-op commit.

The `either both writes or neither` claim (A2.5) is measured for the first
window and for the point of the second window that the second-window test makes
the code fail at. A failure **inside `_commit_row`'s `try` and before**
`model.repo.commit()` is rolled back there: its rollback discards the staged row. A failure **right after** that
commit -- the point the second-window test raises at -- does not split the pair
either: the staged row rode the flip's own commit, so both writes are already
durable and what the caller receives is an error over that durable state. A
failure later in the same path (the trailing `_Session.commit()`,
`_Session.expire_all()`, `_row_dict`) is **not separately measured**; it follows
from the same already-landed commit, and no separate claim is made for it.

`requested_by`, `approved_by` and `resolved_by` hold **user ids**, CKAN's convention
(`package_show` answers `creator_user_id`).

One key of the row is **computed for the caller** and is not a column:
`decidable_by_caller` (S4) answers whether *this* caller may decide *this* row,
and it answers through the same rule the decide predicate answers through
(`logic/auth/publication.py`, `decide_reason`), so a consumer renders the offer
without owning the policy. It is `False` on a row that is no longer `pending`,
because the action refuses a settled request.
"""
from __future__ import annotations

import datetime

import ckan.authz as ckan_authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit
import sqlalchemy as sa

from ckanext.umss import model as umss_model
from ckanext.umss.logic import caller_id
from ckanext.umss.logic.auth.publication import (
    UPDATE_PERMISSION,
    decide_cache,
    decide_reason,
)


__all__ = [
    "publication_request_create",
    "publication_request_cancel",
    "publication_request_decide",
    "publication_request_list",
]


_Session = model.Session

# Rule 3's neutral fallback: an id that is set but does not resolve to a user
# answers this token, never the raw id. A field called `..._name` must never
# contain an id.
UNKNOWN_NAME = "unknown"

# S4's computed key: the decide predicate's verdict for the caller reading the
# row. Not a column, and not a name — see `_decidable_by_caller`.
DECIDABLE_KEY = "decidable_by_caller"


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
    `requested_by`/`approved_by`/`resolved_by` in the page and hands them here once; the
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


def _decidable_by_caller(row, context, cache=None):
    """S4: whether **this caller** may decide **this row** — the question a queue
    and a record card ask before offering the control.

    It is the predicate's verdict, not a flag about four eyes: a non-requester
    `admin` reads `True` because they may decide it, the requester reads `True`
    only where no other person of the organization can, and a caller who is not
    an approver reads `False`.

    `False` for a row that is no longer `pending`: the action refuses a settled
    request (`409`, "That request is no longer pending"), so `True` there would
    send a consumer straight to a refusal.

    `requested_by` is on the row, so a consumer that words the offer differently
    for the requester compares two ids; it does not re-derive a rule.
    """
    if row.status != umss_model.PENDING:
        return False
    return decide_reason(context, row, cache)[0]


def _row_dict(row, context, names=None, datasets=None, cache=None):
    """The row as the API will answer it: the declared columns, keyed by name,
    plus the presentation fields the portal reads (rule 3) and the computed
    `decidable_by_caller` (S4).

    `context` is required because one of those keys is about **the caller**: the
    four faces answer the same shape, and this is the only key that depends on
    who is asking.

    `names` is the batched user resolution for the whole call (`_names_for`)
    and `datasets` the batched package/organisation resolution
    (`_datasets_by_id`); when either is omitted — a single-row return — it is
    resolved here in one batched query, so one row and a whole page answer the
    same shape at the same cost per resolver. `cache` is `decide_cache`'s memo:
    a page hands in the one it warmed with every organization it is about, so the
    membership question costs **one** statement for the page instead of one per
    row.
    """
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    if names is None:
        names = _names_for(_user_ids(row))
    if datasets is None:
        datasets = _datasets_by_id((row.dataset_id,))
    data["requested_by_name"] = _name_key(row.requested_by, names)
    data["approved_by_name"] = _name_key(row.approved_by, names)
    data["resolved_by_name"] = _name_key(row.resolved_by, names)
    data["dataset_title"] = _dataset_title(row.dataset_id, datasets)
    data["organization_title"] = _organization_title(row.dataset_id, datasets)
    data[DECIDABLE_KEY] = _decidable_by_caller(row, context, cache)
    return data


def _user_ids(row):
    """The row's three user-id columns, the ones that answer a `..._name`."""
    return (row.requested_by, row.approved_by, row.resolved_by)


def _has_text(value):
    """Whether a resolved title carries text. An empty or whitespace-only
    title is a presentation hole, not a value: the consumer's natural code is
    `title ?? fallback`, an empty string is truthy there, and it renders as a
    blank line everywhere. So a blank title answers `None`, exactly like an
    unset id answers `None` for the names."""
    return isinstance(value, str) and value.strip() != ""


def _dataset_title(dataset_id, datasets):
    """The dataset's own title, with rule 3's two empties: an unset
    `dataset_id` answers `None`, a set-but-unresolvable one answers the neutral
    token `"unknown"` — never the raw id in a field called `..._title`. A
    dataset that resolves but carries an empty or whitespace-only title answers
    `None`, not a blank string."""
    if not dataset_id:
        return None
    meta = datasets.get(dataset_id)
    if meta is None:
        return UNKNOWN_NAME
    return meta["dataset_title"] if _has_text(meta["dataset_title"]) else None


def _organization_title(dataset_id, datasets):
    """The owner organisation's title, with the same two empties: an unowned
    dataset answers `None` (there is nothing to resolve), and a `dataset_id`
    that is set but does not resolve answers `"unknown"` — the dataset is gone,
    so its organisation is unknown too. A resolvable dataset whose `owner_org`
    points at a missing group answers `"unknown"` as well (the reference is set,
    the thing behind it is not there); one whose group resolves but carries an
    empty or whitespace-only title answers `None`, not a blank string."""
    if not dataset_id:
        return None
    meta = datasets.get(dataset_id)
    if meta is None:
        return UNKNOWN_NAME
    if not meta["owner_org"]:
        return None
    if meta["organization_id"] is None:
        return UNKNOWN_NAME
    return meta["organization_title"] if _has_text(meta["organization_title"]) else None


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

    **It completes the publication, not only the visibility.** CKAN grants the
    `public` label to a dataset that is active and not private
    (`ckan/lib/plugins.py`,
    `if dataset_obj.state == u'active' and not dataset_obj.private:`), and an
    anonymous reader holds only that label. A dataset in `state='draft'` would
    therefore be published — `private = false` — and invisible to every anonymous
    caller: a dataset the flow reports as published and the catalog refuses to
    show. So a `draft` is completed here too, and **only** a `draft`: a `deleted`
    dataset is never revived by a publication, which is why the patch is built
    from the dataset's current state instead of writing the state
    unconditionally.

    Two CKAN facts make that patch possible, both read in the source the suite
    runs. `state` is part of the update schema (`ignore_not_package_admin`,
    `ignore_missing`), and that validator steps aside exactly when the context
    carries the flag this flip already sets (`ckan/logic/validators.py`,
    `if 'ignore_auth' in context:`). Without that flag the wall would drop the
    state and this function would silently complete half the publication again.
    """
    patch = {"id": dataset_id, "private": False}
    dataset = model.Package.get(dataset_id)
    if dataset is not None and dataset.state == "draft":
        patch["state"] = "active"
    toolkit.get_action("package_patch")(
        {"ignore_auth": True, "user": context.get("user")}, patch
    )


def _commit_row(row, context, flip=False):
    """Write the record and, when asked, the flip — in one commit.

    `package_patch` commits the session it is handed
    (`ckan/logic/action/update.py`, `model.repo.commit()`), and that session is
    this module's `_Session`, so the row staged here is committed by
    the flip's own commit rather than by the trailing `_Session.commit()`. What
    the rollback guarantees is the window **inside that `try` and before** that
    commit: a failure there discards the staged row and re-raises, so a row
    cannot survive a flip that never committed. A failure **after** the commit is not rolled back —
    the flip is already durable — and it does not split the pair either: the row
    staged here rode the flip's own commit, so both writes are durable when the
    caller receives the error (A2.5; the second-window test measures the point
    immediately after the commit, and any later point follows from that same
    landed commit rather than from a measurement of its own).
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
    return _row_dict(row, context)


def publication_request_create(context, data_dict):
    """D4: writes one `pending` row, and is idempotent on the pending one."""
    toolkit.check_access("publication_request_create", context, data_dict)
    # The input may name the dataset any way CKAN accepts — `Package.get` takes an id
    # **or** a name, and that tolerance is deliberate. What gets **stored** has to be
    # the canonical id: a row keyed by a name is invisible to every read path that
    # resolves datasets by id (`_datasets_by_id`), which is how the approver's queue
    # ends up empty for the very request they have to decide (measured live on
    # 2026-10-08). It also closes a duplicate hole: the partial unique index is on
    # `dataset_id`, so the same dataset reached by name and by id produced two
    # `pending` rows.
    dataset = _existing_dataset(_required(data_dict, "dataset_id"))
    dataset_id = dataset.id

    existing = _pending_for(dataset_id)
    if existing is not None:
        return _row_dict(existing, context)

    row = umss_model.PublicationRequest(
        dataset_id=dataset_id,
        requested_visibility="public",
        status=umss_model.PENDING,
        requested_by=caller_id(context),
        comments=data_dict.get("comments"),
        request_comment=data_dict.get("comments"),
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
            return _row_dict(existing, context)
        raise


def publication_request_cancel(context, data_dict):
    """D4: `pending` → `cancelled`. Never flips the dataset.

    It records when and by whom (0002): `decided_at` and `resolved_by`, which
    is the requester or an org `admin`. It is not a decision, so `approved_by`
    and `decision_comment` stay unset.
    """
    toolkit.check_access("publication_request_cancel", context, data_dict)
    row = _request_row(_required(data_dict, "request_id"))

    if row.status != umss_model.PENDING:
        raise toolkit.ValidationError(
            {"request_id": ["Only a pending request can be cancelled"]}
        )

    row.status = umss_model.CANCELLED
    row.resolved_by = caller_id(context)
    row.decided_at = _now()
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

    # `comments` keeps its pre-0002 behaviour (the decider's comment replaces the
    # requester's note); `decision_comment` keeps the decider's apart from it.
    if data_dict.get("comments"):
        row.comments = data_dict["comments"]
        row.decision_comment = data_dict["comments"]
    row.approved_by = caller_id(context)
    row.resolved_by = row.approved_by
    row.decided_at = _now()

    if not approve:
        row.status = umss_model.REJECTED
        return _commit_row(row, context)

    row.status = umss_model.APPROVED
    row.consumed_at = _now()
    return _commit_row(row, context, flip=True)


@toolkit.side_effect_free
def publication_request_list(context, data_dict):
    """D4's queue: what the caller may see, optionally narrowed by status and
    by one dataset, oldest first unless `sort` asks for the newest first.

    `dataset_id` (0002) is the history of one dataset: an id **or** a name, like
    `create`, resolved to the dataset (`NotFound` when it does not resolve), and
    matched against **both** forms, because rows written before the
    canonicalisation hold the name (rule 9). It narrows the query; it does not
    widen what the caller may see, which is the same predicate as without it.

    "May see" is the stock `update_dataset` capacity on the dataset's org, plus
    the caller's own requests. The predicate stays in Python on purpose: the
    stock helper walks the org hierarchy (`ckan/authz.py`,
    `def has_user_permission_for_group_or_org(`), and re-writing
    that walk in SQL is how a queue starts showing rows to the wrong org. The
    cost is not per row, though — one query resolves every dataset's org and the
    capacity is evaluated once per organization (R4-list-unbounded).

    The computed key (S4) costs two things, and they have different shapes. The
    **other-approver** facts of every organization the page is about are warmed
    into one `decide_cache` here, so that question is **one statement for the
    page**. The requester's **capacity** — the decision's first gate, which the
    predicate asks before anything else — is a **per-row** question: it is
    memoized per `(requester, organization)` pair, so a page where one person
    asked for several datasets in one organization pays it once, while a page of
    N different requesters pays it N times. The list did not ask it at all before
    this key existed, and it cannot be batched without re-writing the stock
    hierarchy walk that `may_see` above deliberately reuses.

    Declared limitation: every row of the requested status is still
    materialized, because D4's contract has no pagination. If the portal's queue
    needs paging, that is a contract change and it belongs to B2.
    """
    order = _list_order(data_dict.get("sort"))
    query = _Session.query(umss_model.PublicationRequest)
    status = data_dict.get("status")
    if status:
        query = query.filter(umss_model.PublicationRequest.status == status)
    if data_dict.get("dataset_id"):
        dataset = _existing_dataset(data_dict["dataset_id"])
        query = query.filter(
            umss_model.PublicationRequest.dataset_id.in_((dataset.id, dataset.name))
        )
    rows = query.order_by(order).all()

    caller = caller_id(context)
    datasets = _datasets_by_id(row.dataset_id for row in rows)
    allowed_orgs = {}

    def may_see(dataset_id):
        meta = datasets.get(dataset_id)
        if meta is None or not meta["owner_org"]:
            return False
        org_id = meta["owner_org"]
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
        for user_id in _user_ids(row)
    )
    cache = decide_cache(
        meta["owner_org"] for meta in datasets.values() if meta["owner_org"]
    )
    return [
        _row_dict(row, context, names, datasets, cache=cache) for row in visible
    ]


# `publication_request_list`'s two orders (0002), by creation time. A closed
# set, not a passthrough to SQL: anything else is a `ValidationError`.
LIST_SORTS = {
    "created_at asc": umss_model.PublicationRequest.created_at.asc(),
    "created_at desc": umss_model.PublicationRequest.created_at.desc(),
}
DEFAULT_LIST_SORT = "created_at asc"


def _list_order(sort):
    if sort in (None, ""):
        sort = DEFAULT_LIST_SORT
    if not isinstance(sort, str) or sort not in LIST_SORTS:
        raise toolkit.ValidationError(
            {"sort": ['Must be "created_at asc" or "created_at desc"']}
        )
    return LIST_SORTS[sort]


def _datasets_by_id(dataset_ids):
    """`{dataset_id: {owner_org, organization_id, dataset_title,
    organization_title}}` for the given ids, in **one** query.

    The owner organisation is joined in the same statement, so the two titles
    the portal reads ride the organisation resolution instead of adding a query
    to it: the page pays one resolver statement, not one per row (rule 3's cost
    shape). `organization_id` is the joined group's own id, which is what
    distinguishes a **missing** group (`None` -> `"unknown"`) from a group that
    resolves with a blank title (`None` title -> `None`). A dataset the query
    does not return is one that no longer resolves; the caller decides the
    fallback, exactly as `_names_for` leaves its empties to `_name_key`.
    """
    wanted = set(dataset_ids)
    if not wanted:
        return {}
    rows = (
        _Session.query(
            model.Package.id,
            model.Package.name,
            model.Package.owner_org,
            model.Package.title,
            model.Group.id,
            model.Group.title,
        )
        .outerjoin(model.Group, model.Group.id == model.Package.owner_org)
        # By id **or** by name, and both are looked up: rows written before the
        # canonicalisation exist in deployed stores and carry the name the caller
        # used, while every row written since carries the id. The mapping below is
        # keyed by both forms so the caller's `datasets.get(row.dataset_id)` finds
        # the dataset whichever form the row holds — that is what makes the old
        # rows visible without a data migration.
        .filter(
            sa.or_(
                model.Package.id.in_(wanted),
                model.Package.name.in_(wanted),
            )
        )
        .all()
    )
    resolved = {}
    for (
        dataset_id,
        dataset_name,
        owner_org,
        dataset_title,
        organization_id,
        organization_title,
    ) in rows:
        entry = {
            "owner_org": owner_org,
            "organization_id": organization_id,
            "dataset_title": dataset_title,
            "organization_title": organization_title,
        }
        resolved[dataset_id] = entry
        if dataset_name:
            resolved[dataset_name] = entry
    return resolved
