"""
Tests for the four publication actions (`ckanext.umss.logic.action.publication`).

Design reference: `design.md` D4 (the actions and who they are for) and D5
(the door writes the record and flips the value in one transaction). These tests
drive the actions through `logic.get_action`, which is the path the API
controller takes, so they prove the actions are **registered**, not merely
defined.

Publication has exactly one route: `publication_request_create` ->
`publication_request_decide {approve: true}`. The direct `publication_publish`
action was retired, so the wall in `ckanext.umss.auth` refuses a stock flip by
every caller, the sysadmin included.

Phases A2.1–A2.5 landed together in this file's own commit: CKAN raises
`ValueError('Authorization function not found: ...')` for an action with no auth
function (`ckan/authz.py:235-254`), so the actions and their authorization could
not be separated in time, and the authorization tests below were written as RED
together with the surface ones.

The contract these tests pin, for the portal that consumes it:

| action | arguments | returns |
|---|---|---|
| `publication_request_create` | `dataset_id`, `comments` (optional) | the row as a dict |
| `publication_request_cancel` | `request_id` | the row as a dict |
| `publication_request_decide` | `request_id`, `approve`, `comments` (optional) | the row as a dict |
| `publication_request_list` | `status` (optional) | a list of row dicts |

`requested_by` and `approved_by` carry **user ids**, which is the convention the
rest of CKAN follows (`package_show` answers `creator_user_id`, not a name).
"""
import datetime
import importlib.util
from unittest import mock

import pytest
import sqlalchemy as sa

import ckan.model as ckan_model
import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers

from ckanext.umss import auth as umss_auth
from ckanext.umss import model as umss_model
from ckanext.umss.logic.action import publication as actions
from ckanext.umss.logic.auth import publication as auth_publication


pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss"),
    pytest.mark.usefixtures("with_plugins"),
]


@pytest.fixture
def store(clean_db, migrate_db_for):
    """The store table, built by this extension's own migration.

    The actions write rows, so they need the table the migration creates —
    `clean_db` does not build extension tables (see `test_publication_store.py`).
    """
    migrate_db_for("umss")


def add_user_member(org_id, user_id, capacity):
    return helpers.call_action(
        "member_create", id=org_id, object=user_id, object_type="user", capacity=capacity
    )


@pytest.fixture
def scene(clean_db):
    """One organization with an editor, a member and an admin, an outsider's
    organization, and a **private** dataset in each.

    `clean_db` is a dependency on purpose, not a marker: it wipes every row, so
    anything this fixture creates has to be created **after** it. With the two
    as sibling fixtures the order follows the test's argument list, and
    `(scene, store)` created the users and then wiped them — which surfaced as
    `NotAuthorized: ... requires an authenticated user`, three tests passing for
    the wrong reason, and one (`test_a_member_cannot_create`) passing because
    the anonymous branch refused it, not the member rule.
    """
    editor = factories.User()
    member = factories.User()
    admin = factories.User()
    outsider = factories.User()

    org = factories.Organization()
    other_org = factories.Organization()
    add_user_member(org["id"], editor["id"], "editor")
    add_user_member(org["id"], member["id"], "member")
    add_user_member(org["id"], admin["id"], "admin")
    add_user_member(other_org["id"], outsider["id"], "editor")

    dataset = factories.Dataset(owner_org=org["id"], private=True)
    second_dataset = factories.Dataset(owner_org=org["id"], private=True)
    other_dataset = factories.Dataset(owner_org=other_org["id"], private=True)
    return {
        "org": org,
        "other_org": other_org,
        "editor": editor,
        "member": member,
        "admin": admin,
        "outsider": outsider,
        "dataset": dataset,
        "second_dataset": second_dataset,
        "other_dataset": other_dataset,
    }


@pytest.fixture
def suborg_scene(scene, clean_db):
    """One level down: a child org of `scene`'s org, with its **own** editor and
    its own private dataset. Its admin is a member of the child only."""
    child = factories.Organization(groups=[{"name": scene["org"]["name"]}])
    child_editor = factories.User()
    child_admin = factories.User()
    add_user_member(child["id"], child_editor["id"], "editor")
    add_user_member(child["id"], child_admin["id"], "admin")

    return dict(
        scene,
        child=child,
        child_editor=child_editor,
        child_admin=child_admin,
        child_dataset=factories.Dataset(owner_org=child["id"], private=True),
    )


def call_as(user, action, **data):
    """Call an action the way the API controller does: a context with only the
    caller. Never `helpers.call_action`, which sets `ignore_auth`."""
    return toolkit.get_action(action)({"user": user and user["name"]}, data)


def stored(dataset_id):
    """The dataset as the database holds it, with authorization bypassed."""
    return helpers.call_action(
        "package_show",
        {"user": "default", "ignore_auth": True, "use_cache": False},
        id=dataset_id,
    )


def rows():
    ckan_model.Session.commit()
    ckan_model.Session.expire_all()
    return ckan_model.Session.query(umss_model.PublicationRequest).all()


def the_row(dataset_id):
    """The single store row for a dataset, whatever its status."""
    matching = [r for r in rows() if r.dataset_id == dataset_id]
    assert len(matching) == 1, matching
    return matching[0]


# ---------------------------------------------------------------------------
# A2.1 — the surface: the four actions exist, are registered, and do what D4 says
# ---------------------------------------------------------------------------


def test_the_four_actions_are_defined():
    assert callable(actions.publication_request_create)
    assert callable(actions.publication_request_cancel)
    assert callable(actions.publication_request_decide)
    assert callable(actions.publication_request_list)


def test_the_removed_publish_action_is_gone():
    """The direct publish action was retired: it is neither defined nor
    registered, so a caller of the old name gets CKAN's own
    `KeyError("Action 'publication_publish' not found")` — the prelude to the
    API's `400 Action name not known` — and never a plugin message."""
    assert not hasattr(actions, "publication_publish")
    with pytest.raises(KeyError):
        toolkit.get_action("publication_publish")


def test_the_plugin_registers_the_four_actions():
    """The deliverable: `logic.get_action` must resolve each name, which is what
    the API and the portal use."""
    for name in (
        "publication_request_create",
        "publication_request_cancel",
        "publication_request_decide",
        "publication_request_list",
    ):
        assert toolkit.get_action(name) is not None, name


def test_create_writes_one_pending_row_with_the_requester_and_the_comment(scene, store):
    created = call_as(
        scene["editor"],
        "publication_request_create",
        dataset_id=scene["dataset"]["id"],
        comments="por favor",
    )

    row = the_row(scene["dataset"]["id"])
    assert row.status == "pending"
    assert row.requested_visibility == "public"
    assert row.requested_by == scene["editor"]["id"]
    assert row.comments == "por favor"
    assert row.decided_at is None
    assert row.consumed_at is None
    assert created["id"] == row.id


def test_create_is_idempotent_and_returns_the_existing_pending_row(scene, store):
    first = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )
    second = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )

    assert first["id"] == second["id"]
    assert len(rows()) == 1
    # The idempotent path returns `_row_dict(existing)`; it must carry the same
    # presentation fields as the insert path, not just the id. Covered here
    # rather than reasoned about, because a consumer branches on neither action
    # nor path.
    for row in (first, second):
        assert row["dataset_title"] == scene["dataset"]["title"]
        assert row["organization_title"] == scene["org"]["title"]
        assert row["requested_by_name"] == scene["editor"]["name"]


def test_cancel_marks_the_request_cancelled_and_does_not_flip(scene, store):
    created = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )

    cancelled = call_as(scene["editor"], "publication_request_cancel", request_id=created["id"])

    assert cancelled["status"] == "cancelled"
    assert the_row(scene["dataset"]["id"]).status == "cancelled"
    assert stored(scene["dataset"]["id"])["private"] is True


def test_decide_rejecting_leaves_the_dataset_private(scene, store):
    created = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )

    decided = call_as(
        scene["admin"],
        "publication_request_decide",
        request_id=created["id"],
        approve=False,
        comments="todavía no",
    )

    assert decided["status"] == "rejected"
    assert decided["comments"] == "todavía no"
    assert decided["approved_by"] == scene["admin"]["id"]
    assert stored(scene["dataset"]["id"])["private"] is True


def test_decide_rejecting_without_comments_is_a_validation_error(scene, store):
    """The governance amendment: a rejection must carry a comment, and the
    failure is a `ValidationError` — the spec separates validation from the
    authorization refusal."""
    created = request_for(scene["editor"], scene["dataset"]["id"])

    with pytest.raises(toolkit.ValidationError) as excinfo:
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=False,
        )

    assert "comments" in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_the_missing_comment_rejection_is_keyed_on_the_comments_field(scene, store):
    """A rejection with no comment is the one refusal a consumer can read
    **without matching prose**: it is a `ValidationError` whose error dict is
    keyed on `comments`. This pins the **key**, not the sentence inside it — the
    key is what a consumer branches on ("the motive is missing"), while the
    sentence is presentation and may be reworded. Pinning the key keeps that
    branch honest if the message changes; the sibling tests assert the message
    only as a substring of the exception, which a reword would not fail.

    A **pin, not a RED**: it passes against the code as first written.
    """
    created = request_for(scene["editor"], scene["dataset"]["id"])

    with pytest.raises(toolkit.ValidationError) as excinfo:
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=False,
        )

    assert list(excinfo.value.error_dict) == ["comments"]


def test_decide_rejecting_with_blank_comments_is_a_validation_error(scene, store):
    created = request_for(scene["editor"], scene["dataset"]["id"])

    with pytest.raises(toolkit.ValidationError):
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=False,
            comments="   ",
        )

    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_decide_approving_without_comments_still_works(scene, store):
    """`comments` is optional when approving; only the rejection requires it."""
    created = request_for(scene["editor"], scene["dataset"]["id"])

    decided = call_as(
        scene["admin"],
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )

    assert decided["status"] == "approved"


def test_decide_approving_records_the_decision_and_flips_in_the_same_call(scene, store):
    created = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )

    decided = call_as(
        scene["admin"], "publication_request_decide", request_id=created["id"], approve=True
    )

    row = the_row(scene["dataset"]["id"])
    assert decided["status"] == "approved"
    assert row.status == "approved"
    assert row.decided_at is not None
    assert row.consumed_at is not None
    assert row.approved_by == scene["admin"]["id"]
    assert stored(scene["dataset"]["id"])["private"] is False


def test_list_filters_by_status(scene, store):
    rejected = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )
    call_as(
        scene["admin"],
        "publication_request_decide",
        request_id=rejected["id"],
        approve=False,
        comments="no",
    )

    every = call_as(scene["admin"], "publication_request_list")
    only_rejected = call_as(scene["admin"], "publication_request_list", status="rejected")
    only_pending = call_as(scene["admin"], "publication_request_list", status="pending")

    assert [r["status"] for r in every] == ["rejected"]
    assert [r["id"] for r in only_rejected] == [rejected["id"]]
    assert only_pending == []


def refused(user, action, **data):
    with pytest.raises(toolkit.NotAuthorized):
        call_as(user, action, **data)


def request_for(user, dataset_id):
    return call_as(user, "publication_request_create", dataset_id=dataset_id)


# ---------------------------------------------------------------------------
# A2.3 — who may do each of the four (D4), written before the auth existed
#
# These were RED together with the surface tests above, and deliberately so:
# CKAN raises `ValueError('Authorization function not found: ...')` when an
# action has no auth function registered (`ckan/authz.py:235-254`), so an action
# and its authorization cannot be separated in time. A stub that allowed
# everything would have been a real open door in the intermediate state.
# ---------------------------------------------------------------------------


def test_an_editor_cannot_decide(scene, store):
    created = request_for(scene["editor"], scene["dataset"]["id"])
    refused(
        scene["editor"],
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_a_member_cannot_create(scene, store):
    """D4 asks for a caller who can `update_dataset`; a member cannot."""
    refused(scene["member"], "publication_request_create", dataset_id=scene["dataset"]["id"])
    assert rows() == []


def test_a_stranger_cannot_cancel_someone_elses_request(scene, store):
    created = request_for(scene["editor"], scene["dataset"]["id"])
    refused(
        scene["outsider"], "publication_request_cancel", request_id=created["id"]
    )
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_the_requester_can_cancel_their_own_request(scene, store):
    created = request_for(scene["editor"], scene["dataset"]["id"])
    cancelled = call_as(
        scene["editor"], "publication_request_cancel", request_id=created["id"]
    )
    assert cancelled["status"] == "cancelled"


@pytest.mark.ckan_config("ckan.roles_that_cascade_to_sub_groups", "admin")
def test_a_parent_org_admin_can_decide_a_child_org_request(suborg_scene, store):
    """Capacity cascades down the hierarchy: the child's own admin is not needed.

    Measured: `has_user_permission_for_group_or_org` (`ckan/authz.py:302`) walks
    `get_parent_group_hierarchy` for the capacities in
    `roles_that_cascade_to_sub_groups`, which is why the predicate is the stock
    one and the extension defines no permission of its own (D4).
    """
    created = request_for(
        suborg_scene["child_editor"], suborg_scene["child_dataset"]["id"]
    )

    decided = call_as(
        suborg_scene["admin"],
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )

    assert decided["status"] == "approved"
    assert stored(suborg_scene["child_dataset"]["id"])["private"] is False


def test_the_child_orgs_own_admin_can_decide(suborg_scene, store):
    created = request_for(
        suborg_scene["child_editor"], suborg_scene["child_dataset"]["id"]
    )
    decided = call_as(
        suborg_scene["child_admin"],
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )
    assert decided["status"] == "approved"


def test_the_requester_cannot_decide_their_own_request(scene, store):
    """Four eyes: an org admin who opened the request cannot approve it, and the
    refusal is not a silent no-op — the row stays `pending`."""
    created = request_for(scene["admin"], scene["dataset"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.FOUR_EYES_LABEL + ": " in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_a_sysadmin_can_decide_someone_elses_request(scene, store):
    """Positive control, not a discriminating test: this passes under the old
    code too, through the stock sysadmin short-circuit. With
    `auth_sysadmins_check` now forcing the function to run for a sysadmin, it
    guards against the function over-narrowing; the flag's real proof is
    `test_a_sysadmin_cannot_decide_their_own_request`."""
    sysadmin = factories.Sysadmin()
    created = request_for(scene["editor"], scene["dataset"]["id"])

    decided = call_as(
        sysadmin,
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )

    assert decided["status"] == "approved"
    assert stored(scene["dataset"]["id"])["private"] is False


def test_a_sysadmin_cannot_decide_their_own_request(scene, store):
    """Four eyes has no sysadmin exception: the requester is refused even as a
    sysadmin. There is no direct-publish escape hatch any more, so the request
    simply stays pending until someone else decides it."""
    sysadmin = factories.Sysadmin()
    created = request_for(sysadmin, scene["dataset"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            sysadmin,
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.FOUR_EYES_LABEL + ": " in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_the_decide_path_checks_capacity_before_four_eyes(scene, store):
    """The decide path's precedence, measured: the requester's **current
    capacity** is re-checked before four eyes, so an approver who opened the
    request and then lost their capacity gets `Requester capacity`, not
    `Four eyes`. The four-eyes label therefore only appears for a requester who
    still holds the admin capacity the decision demands."""
    created = request_for(scene["admin"], scene["dataset"]["id"])

    revoke_membership(scene["org"]["id"], scene["admin"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.REQUESTER_CAPACITY_LABEL + ": " in str(excinfo.value)
    assert auth_publication.FOUR_EYES_LABEL + ": " not in str(excinfo.value)
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_a_non_approver_requester_gets_not_an_approver_not_four_eyes(scene, store):
    """The other half of the precedence, measured: an `editor` who opened a
    request is not an approver, so the decide refusal is `Not an approver` —
    the `Not an approver` branch sits before the four-eyes branch for a
    non-sysadmin, and four eyes is reserved for a requester who **has** the
    admin capacity."""
    created = request_for(scene["editor"], scene["dataset"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["editor"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.NOT_AN_APPROVER_LABEL + ": " in str(excinfo.value)
    assert auth_publication.FOUR_EYES_LABEL + ": " not in str(excinfo.value)
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_list_returns_only_what_the_caller_may_see(scene, store):
    mine = request_for(scene["editor"], scene["dataset"]["id"])

    seen_by_outsider = call_as(scene["outsider"], "publication_request_list")
    seen_by_admin = call_as(scene["admin"], "publication_request_list")
    seen_by_requester = call_as(scene["editor"], "publication_request_list")

    assert seen_by_outsider == []
    assert [r["id"] for r in seen_by_admin] == [mine["id"]]
    assert [r["id"] for r in seen_by_requester] == [mine["id"]]


# ---------------------------------------------------------------------------
# The correction round: what the four-lens review at tier high opened
#
# Only the two CRITICALs are obligatory (`fix_finding_ids`), and they are one
# defect seen twice: neither writing action resolved its `dataset_id`, and the
# auth functions answer `success` for an unresolvable id on purpose.
# ---------------------------------------------------------------------------


def test_create_refuses_an_unknown_dataset_and_writes_nothing(scene, store):
    """The auth answers `success` for an unresolvable id so the action can
    answer `NotFound` — which means the action has to actually check it, or a
    bogus `dataset_id` becomes an orphan row (R1-ORPHAN-ROW, R3-001)."""
    with pytest.raises(toolkit.ObjectNotFound):
        call_as(
            scene["editor"], "publication_request_create", dataset_id="no-such-dataset"
        )

    assert rows() == []


def test_create_refuses_a_dataset_that_is_already_public(scene, store):
    """Re-pointed from the retired direct publish: the already-public guard
    survives on `publication_request_create`, which refuses the no-op flip
    instead of piling up an `approved` row for it (A2's review, R3-003)."""
    public = factories.Dataset(owner_org=scene["org"]["id"], private=False)

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["editor"], "publication_request_create", dataset_id=public["id"]
        )

    assert auth_publication.ALREADY_PUBLIC_MSG in str(excinfo.value)
    assert rows() == []


def test_create_loses_the_race_by_returning_the_winners_row(scene, store, monkeypatch):
    """Idempotency has to hold when two creates interleave, not only in sequence.

    The seam is `_pending_for`, patched to miss on its first call so the action
    takes the insert path with a `pending` row already in the table — the race,
    deterministically. What settles it is D2's partial unique index, and the
    loser's answer must be the winner's row (R4-create-race, R3-002).
    """
    winner = request_for(scene["editor"], scene["dataset"]["id"])

    real_pending_for = actions._pending_for
    seen = []

    def blind_first(dataset_id):
        seen.append(dataset_id)
        return None if len(seen) == 1 else real_pending_for(dataset_id)

    monkeypatch.setattr(actions, "_pending_for", blind_first)

    same = call_as(
        scene["editor"], "publication_request_create", dataset_id=scene["dataset"]["id"]
    )

    assert same["id"] == winner["id"]
    assert len(rows()) == 1


# ---------------------------------------------------------------------------
# A2.5 — the record and the flip are one transaction (D5's "asserted, not measured")
# ---------------------------------------------------------------------------


@pytest.fixture
def failing_flip(monkeypatch):
    """Make the door's flip fail, so the transaction has something to roll back.

    Patches the entry point the action itself uses — `toolkit.get_action` — so
    what is exercised is the real code path, not a swapped-out private helper.
    """
    real_get_action = toolkit.get_action

    def get_action(name):
        if name == "package_patch":

            def boom(context, data_dict):
                raise toolkit.ValidationError({"private": ["the flip failed"]})

            return boom
        return real_get_action(name)

    monkeypatch.setattr(toolkit, "get_action", get_action)


def test_a_failed_flip_on_decide_leaves_no_approved_row_behind(scene, store, failing_flip):
    created = request_for(scene["editor"], scene["dataset"]["id"])

    with pytest.raises(toolkit.ValidationError):
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    row = the_row(scene["dataset"]["id"])
    assert row.status == "pending"
    assert row.decided_at is None
    assert row.consumed_at is None
    assert stored(scene["dataset"]["id"])["private"] is True


# ---------------------------------------------------------------------------
# A2.6 / A2.7 — the decision re-checks the current state, and a pending request
# whose object is gone is annulled
#
# A2.7 has exactly two triggers, and **one of them is still live**: the dataset was
# deleted, which the `after_dataset_delete` hook still annuls and which the test
# below exercises end to end. The other is historical: it was published by the
# retired direct-publish path, so nothing writes that motive any more. The requester
# losing capacity is **not** one of them: the decision is refused as an
# authorization failure and the row stays `pending` (the author's decision,
# 2026-10-07). `annulled` and `cancelled` stay distinct throughout: the
# requester did not withdraw.
# ---------------------------------------------------------------------------


def revoke_membership(org_id, user_id):
    return helpers.call_action(
        "member_delete", id=org_id, object=user_id, object_type="user"
    )


def test_the_motive_tokens_are_the_interface_values():
    """The `motive` values are a **cross-repository interface**: the portal and
    the tracked contract match on the exact strings, while every other test
    here compares against the Python constants. A consistent typo in a
    constant's value would pass all of those and still break the consumer, so
    this is the one place that pins the literals. It is a pin, not a RED: it
    passes against the constants as first written.
    """
    assert umss_model.MOTIVE_DATASET_DELETED == "dataset_deleted"
    assert (
        umss_model.MOTIVE_PUBLISHED_BY_ANOTHER_PATH == "published_by_another_path"
    )


def test_the_eight_refusal_labels_are_the_interface_values():
    """The eight refusal **labels** are the cross-repository interface values a
    consumer matches on: the portal reads them from a single constant that
    points at `PUBLICATION-ACTIONS.md`, and CKAN gives no machine-readable code
    — an authorization failure is only `{"__type": "Authorization Error",
    "message": ...}` — so the label is the interface and the sentence after the
    colon is free prose.

    The direct-publish action was retired with its `Not a sysadmin` label, so
    the surviving set is eight: six on this extension's own actions and the
    wall's two.

    This pin freezes the labels, not the sentences: rewording the prose after a
    colon passes, changing a label fails. Breaking it means a refusal the
    portal knows arrives under a label it does not.

    All eight are asserted distinct here because that distinctness *is* the
    interface.

    A **pin, not a RED**: it passes against the constants as first written.
    """
    action_labels = [
        auth_publication.FOUR_EYES_LABEL,
        auth_publication.REQUESTER_CAPACITY_LABEL,
        auth_publication.NOT_AN_APPROVER_LABEL,
        auth_publication.ALREADY_PUBLIC_LABEL,
        auth_publication.CANNOT_REQUEST_LABEL,
        auth_publication.CANNOT_CANCEL_LABEL,
    ]
    wall_labels = [
        umss_auth.PUBLICATION_FLOW_LABEL,
        umss_auth.PUBLISH_DENIED_LABEL,
    ]

    assert auth_publication.FOUR_EYES_LABEL == "Four eyes"
    assert auth_publication.REQUESTER_CAPACITY_LABEL == "Requester capacity"
    assert auth_publication.NOT_AN_APPROVER_LABEL == "Not an approver"
    assert auth_publication.ALREADY_PUBLIC_LABEL == "Already public"
    assert auth_publication.CANNOT_REQUEST_LABEL == "Cannot request"
    assert auth_publication.CANNOT_CANCEL_LABEL == "Cannot cancel"
    assert umss_auth.PUBLICATION_FLOW_LABEL == "Publication flow"
    assert umss_auth.PUBLISH_DENIED_LABEL == "Publish denied"

    labels = action_labels + wall_labels
    assert len(labels) == 8
    assert len(set(labels)) == 8, labels

    messages = [
        (auth_publication.FOUR_EYES_LABEL, auth_publication.DECIDE_FOUR_EYES_MSG),
        (
            auth_publication.REQUESTER_CAPACITY_LABEL,
            auth_publication.DECIDE_REQUESTER_CAPACITY_MSG,
        ),
        (auth_publication.NOT_AN_APPROVER_LABEL, auth_publication.DECIDE_DENIED_MSG),
        (auth_publication.ALREADY_PUBLIC_LABEL, auth_publication.ALREADY_PUBLIC_MSG),
        (auth_publication.CANNOT_REQUEST_LABEL, auth_publication.REQUEST_DENIED_MSG),
        (auth_publication.CANNOT_CANCEL_LABEL, auth_publication.CANCEL_DENIED_MSG),
        (umss_auth.PUBLICATION_FLOW_LABEL, umss_auth.PUBLISH_VIA_FLOW_MSG),
        (umss_auth.PUBLISH_DENIED_LABEL, umss_auth.PUBLISH_DENIED_MSG),
    ]
    for label, message in messages:
        assert message.startswith(label + ": "), (label, message)


#: The two modules that declare refusals to a consumer.
REFUSAL_MODULES = (auth_publication, umss_auth)

#: What stands in for the translator in the structural guardian below: a
#: translator that leaves a visible mark on every string it is handed. A label
#: that is *inside* a translatable unit arrives with the mark **in front of it**.
TRANSLATION_SENTINEL = "[t]"


def _declared_labels(module):
    """Every `<name>_LABEL` string the module declares."""
    return {
        value
        for name, value in vars(module).items()
        if name.endswith("_LABEL") and isinstance(value, str)
    }


def _declared_messages(module):
    """Every `<name>_MSG` string the module declares, as `{name: value}`."""
    return {
        name: value
        for name, value in vars(module).items()
        if name.endswith("_MSG") and isinstance(value, str)
    }


def _module_under_a_marking_translator(module):
    """Load `module` a second time, in a namespace of its own, with `toolkit._`
    replaced by the marking translator.

    The live module is not touched: the probe is a **separate module object**, so
    the mark cannot leak into another test, and `importlib.reload` is avoided
    precisely because it would mutate the module every other test in the process
    holds. Both modules are pure (a docstring, imports, assignments and
    functions, measured with `ast`), so re-executing one has no side effect to
    undo.

    It is a **probe, not a simulation**: the real source runs, so what it
    measures is where the label sits relative to the translator's argument — the
    property no behaviour can show, since there is no catalog whose absence or
    presence would change what a caller sees.
    """
    spec = importlib.util.spec_from_file_location(
        module.__name__ + "__under_translation", module.__file__
    )
    probe = importlib.util.module_from_spec(spec)
    with mock.patch.object(
        toolkit, "_", lambda message: TRANSLATION_SENTINEL + message
    ):
        spec.loader.exec_module(probe)
    return probe


def test_every_refusal_message_begins_with_a_declared_label():
    """The invariant the labels exist for: **no consumer ever has to match a
    sentence** — for every refusal the modules declare as a message constant.

    This walks every refusal declared as a module-level `*_MSG` string in the two
    refusal modules and every declared `*_LABEL`, and fails if a message does not
    open with `<label>: `. The reach is exactly that and no more: it does **not**
    see inline strings, handler-local strings, `ValidationError` dict entries, or
    constants named otherwise, so it is a guard on the declared-message surface,
    not a proof about every string these modules emit. Within that surface it is
    structural rather than a list of the eight known messages: a future refusal
    declared with a label passes, one declared without fails, and a future label
    is picked up automatically from the module.
    """
    labels = set()
    for module in REFUSAL_MODULES:
        labels |= _declared_labels(module)
    assert labels

    prefixes = tuple(label + ": " for label in labels)
    seen = 0
    for module in REFUSAL_MODULES:
        for name, value in _declared_messages(module).items():
            seen += 1
            assert value.startswith(prefixes), (module.__name__, name, value)
    assert seen >= 8, seen


def test_the_refusal_labels_are_not_translated():
    """The same invariant, re-checked in a translated world — the property the
    labels exist for, and the one no behaviour can show.

    A label is the interface a consumer matches and the sentence after it is
    free prose, so the label must stay **outside** the translatable unit: what is
    translated is what a human reads, never what is matched. Nothing translates
    these messages today — the extension ships no catalog (`ckanext/umss/i18n/`
    holds an empty `.gitignore` and nothing else) and this project extracts only
    the `translate` / `isPlural` keywords, not `_` — so the defect is **invisible
    by inspection**: the day the composed string gains a translation, the prefix
    stops matching and no other row in this suite would notice. That is why the
    guard is structural: each refusal module is loaded a second time under a
    **marking** translator, and the invariant above runs again. If a label moves
    back inside `toolkit._(...)`, the mark lands in front of it and the invariant
    falls.

    A **RED until the wall's `_()` stops containing its label**, and a pin on the
    rest: the probe asserts the mark *still* appears in the wall's two messages,
    because the fix pulls the label out of the translator and not the prose. That
    assertion is also what keeps this row from being vacuous — a probe where
    `toolkit._` was never replaced would satisfy the invariant and prove nothing.
    """
    probes = {
        module.__name__: _module_under_a_marking_translator(module)
        for module in REFUSAL_MODULES
    }

    wall = probes[umss_auth.__name__]
    marked = [
        name
        for name, value in _declared_messages(wall).items()
        if TRANSLATION_SENTINEL in value
    ]
    assert marked, "the marking translator never reached a message: it proves nothing"

    for probe in probes.values():
        labels = _declared_labels(probe)
        assert labels
        prefixes = tuple(label + ": " for label in labels)
        for name, value in _declared_messages(probe).items():
            assert value.startswith(prefixes), (probe.__name__, name, value)


def test_the_translation_guardian_can_fail():
    """The witness that the guardian above is worth its green: the shape it has
    to reject, next to the shape it has to accept.

    A row that promises a property it cannot observe is worse than no row, so the
    discriminating power is itself pinned: with the label inside the translated
    unit the mark precedes the label and the invariant fails; with the label
    outside it, the same message opens with the label and passes. Both strings
    are built here rather than imported from the modules, so this test states the
    rule and not the current state of the code.
    """
    label = umss_auth.PUBLISH_DENIED_LABEL

    label_inside = TRANSLATION_SENTINEL + "%s: %s" % (label, "any sentence")
    label_outside = "%s: %s" % (label, TRANSLATION_SENTINEL + "any sentence")

    assert not label_inside.startswith(label + ": ")
    assert label_outside.startswith(label + ": ")


def test_decide_re_checks_the_owning_organization_at_decision_time(scene, store):
    """A2.6, owner half — a **regression pin**, not a TDD proof.

    This passes at the commit this cut started from (`b98d823`): the auth
    recomputes the owning organization from the dataset at call time and never
    stored it, so the observable consequence — an approver whose admin capacity
    no longer covers the current owner is refused — was already satisfied by
    construction. It is written to **keep** that property: a later cut that
    caches the org on the row or in the decision context would let the old
    org's admin decide a request that no longer lives in their org, and this
    test would fall.

    The requester is made an editor of the new owner on purpose: the point
    under test is the **approver's** capacity. Leaving the requester without
    capacity on the new owner would trip the requester half instead and refuse
    every approver, which would prove the wrong thing.
    """
    created = request_for(scene["editor"], scene["dataset"]["id"])

    new_org = factories.Organization()
    new_admin = factories.User()
    add_user_member(new_org["id"], new_admin["id"], "admin")
    add_user_member(new_org["id"], scene["editor"]["id"], "editor")

    helpers.call_action(
        "package_patch",
        {"ignore_auth": True, "user": "default"},
        id=scene["dataset"]["id"],
        owner_org=new_org["id"],
    )

    with pytest.raises(toolkit.NotAuthorized):
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    decided = call_as(
        new_admin,
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )

    assert decided["status"] == "approved"
    assert stored(scene["dataset"]["id"])["private"] is False


def test_decide_refuses_when_the_requester_lost_their_capacity(scene, store):
    """A2.6, requester half: the decision re-checks the requester's **current**
    capacity. The request was valid when created; the requester is then removed
    from the owning organization, so the decision is refused as an
    authorization failure and the row stays `pending`.

    It is **not** `annulled`: the object did not disappear, and the two
    annulment triggers are exactly the deleted dataset and the other publish
    path. It is not `cancelled` either: the requester did not withdraw.

    Discriminating: without the requester-capacity check the org admin's decide
    succeeds — the requester is not the caller, so four eyes does not fire. The
    RED was observed before the check existed.
    """
    created = request_for(scene["editor"], scene["dataset"]["id"])

    revoke_membership(scene["org"]["id"], scene["editor"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.DECIDE_REQUESTER_CAPACITY_MSG in str(excinfo.value)
    assert auth_publication.REQUESTER_CAPACITY_LABEL + ": " in str(excinfo.value)
    assert auth_publication.FOUR_EYES_LABEL + ": " not in str(excinfo.value)
    assert the_row(scene["dataset"]["id"]).status == "pending"
    assert stored(scene["dataset"]["id"])["private"] is True


def test_a_sysadmin_approver_is_also_refused_when_the_requester_lost_capacity(
    scene, store
):
    """The requester-capacity rule has no sysadmin exception, for the same
    reason four eyes has none. Without this, the rule would be written, green
    and hollow for a sysadmin approver — the function carries
    `auth_sysadmins_check`, so the check must sit before the sysadmin
    short-circuit.

    Discriminating: the sysadmin short-circuit would otherwise return success
    before the rule ran. There is no direct-publish escape hatch any more, so
    the request cannot be rescued by anyone either.
    """
    created = request_for(scene["editor"], scene["dataset"]["id"])

    revoke_membership(scene["org"]["id"], scene["editor"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            factories.Sysadmin(),
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.DECIDE_REQUESTER_CAPACITY_MSG in str(excinfo.value)
    assert auth_publication.REQUESTER_CAPACITY_LABEL + ": " in str(excinfo.value)
    assert auth_publication.FOUR_EYES_LABEL + ": " not in str(excinfo.value)
    assert the_row(scene["dataset"]["id"]).status == "pending"
    assert stored(scene["dataset"]["id"])["private"] is True


def test_decide_fails_closed_when_the_requester_cannot_be_resolved(scene, store):
    """Triangulation of the requester half: an unresolvable requester (a
    deleted user) fails closed. `requested_by` holds a user id, and there is no
    direct-publish escape hatch any more: the request stays `pending` rather
    than being decided."""
    created = request_for(scene["editor"], scene["dataset"]["id"])
    the_row(scene["dataset"]["id"]).requested_by = "deleted-user-id"
    ckan_model.Session.commit()

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["admin"],
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert auth_publication.DECIDE_REQUESTER_CAPACITY_MSG in str(excinfo.value)
    assert auth_publication.REQUESTER_CAPACITY_LABEL + ": " in str(excinfo.value)
    assert auth_publication.FOUR_EYES_LABEL + ": " not in str(excinfo.value)
    assert the_row(scene["dataset"]["id"]).status == "pending"


def test_deleting_the_dataset_annuls_the_pending_request_with_the_deleted_motive(
    scene, store
):
    """A2.7, deleted-object trigger: the dataset is removed under a `pending`
    request, and the row becomes `annulled` with the motive the store records
    for a deleted object.

    The whole path is exercised — `package_delete` invokes the plugin hook
    before `entity.delete()` and commits once — not the hook in isolation. The
    annulled row is not `cancelled`: the requester did not withdraw.
    """
    request_for(scene["editor"], scene["dataset"]["id"])

    helpers.call_action("package_delete", id=scene["dataset"]["id"])

    for_dataset = [r for r in rows() if r.dataset_id == scene["dataset"]["id"]]
    assert len(for_dataset) == 1
    assert for_dataset[0].status == umss_model.ANNULLED
    assert for_dataset[0].status != umss_model.CANCELLED
    assert for_dataset[0].motive == umss_model.MOTIVE_DATASET_DELETED
    assert for_dataset[0].decided_at is not None


def test_deleting_the_dataset_by_name_annuls_the_pending_request(scene, store):
    """The hook's docstring claims a dataset **name** is legal input to
    `package_delete` and is resolved to the canonical id before the row is
    matched. Source reading confirmed the mechanism but no executed test did;
    this closes that gap.

    A **pin for an untested path, not a RED**: it passes against the WU2 hook
    as first written, because that hook already resolved the name through
    `model.Package.get`. No source code changed for this test.
    """
    dataset_name = scene["dataset"]["name"]
    request_for(scene["editor"], scene["dataset"]["id"])

    helpers.call_action("package_delete", id=dataset_name)

    for_dataset = [r for r in rows() if r.dataset_id == scene["dataset"]["id"]]
    assert len(for_dataset) == 1
    assert for_dataset[0].status == umss_model.ANNULLED
    assert for_dataset[0].motive == umss_model.MOTIVE_DATASET_DELETED


def test_deleting_a_dataset_with_no_pending_request_is_a_clean_no_op(scene, store):
    """The ordinary delete path with the plugin loaded and no pending row: the
    hook returns without touching anything, and the deletion still commits."""
    dataset_id = scene["second_dataset"]["id"]

    helpers.call_action("package_delete", id=dataset_id)

    assert [r for r in rows() if r.dataset_id == dataset_id] == []
    assert ckan_model.Session.get(ckan_model.Package, dataset_id).state == "deleted"


def test_deleting_a_dataset_without_the_store_table_is_a_clean_no_op(scene):
    """The plugin is loaded but this extension's table is absent — the state
    `clean_db` leaves before `migrate_db_for` rebuilds it, and the state the
    wall's tests (`tests/test_auth.py`) run in. There can be no pending row, and
    the ordinary delete must not abort its transaction on the missing table.

    This pins the hook's existence guard directly; before it, the delete raised
    `ProgrammingError: relation "publication_requests" does not exist`.
    """
    dataset_id = scene["second_dataset"]["id"]

    helpers.call_action("package_delete", id=dataset_id)

    assert ckan_model.Session.get(ckan_model.Package, dataset_id).state == "deleted"


# ---------------------------------------------------------------------------
# Rule 3 — the rows carry presentation names
#
# `requested_by` and `approved_by` stay user **ids** (rule 2); the two
# `..._name` keys are additive. They resolve in **one batched query per call**
# so the queue does not resolve N users per page, and the fallback is neutral:
# an empty id column answers `None`, a set-but-unresolvable id answers
# `"unknown"`, never the raw id.
# ---------------------------------------------------------------------------


NAME_KEYS = ("requested_by_name", "approved_by_name")
TITLE_KEYS = ("dataset_title", "organization_title")


def assert_names_present(row):
    for key in NAME_KEYS:
        assert key in row, (key, sorted(row))


def assert_titles_present(row):
    for key in TITLE_KEYS:
        assert key in row, (key, sorted(row))


def fresh_dataset(scene):
    return factories.Dataset(owner_org=scene["org"]["id"], private=True)


def name_resolution_selects(statements):
    """The `SELECT ... FROM "user" WHERE "user".id IN (...)` statements, the
    only shape the batched resolver emits. Rule 3's own path is isolated from
    the `may_see` capacity lookups, which use `WHERE "user".name = ...` or
    `"user".id = ...` and therefore never match this shape.
    """
    return [
        statement
        for statement in statements
        if 'from "user"' in statement.lower() and '"user".id in' in statement.lower()
    ]


def measured(call):
    """Run `call` while recording every statement that reaches the cursor."""
    statements = []
    bind = ckan_model.Session.get_bind()
    engine = getattr(bind, "engine", bind)

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        result = call()
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)
    return result, statements


def test_every_action_returns_rows_carrying_both_presentation_names(scene, store):
    """The four faces answer the same shape: the row, with both name keys. A
    consumer must never branch on which action produced the row."""
    editor = scene["editor"]
    admin = scene["admin"]

    created = call_as(
        editor, "publication_request_create", dataset_id=scene["dataset"]["id"]
    )
    assert_names_present(created)

    cancelled = call_as(editor, "publication_request_cancel", request_id=created["id"])
    assert_names_present(cancelled)

    rejected_source = request_for(editor, fresh_dataset(scene)["id"])
    rejected = call_as(
        admin,
        "publication_request_decide",
        request_id=rejected_source["id"],
        approve=False,
        comments="no",
    )
    assert_names_present(rejected)

    approved = call_as(
        admin,
        "publication_request_decide",
        request_id=request_for(editor, scene["second_dataset"]["id"])["id"],
        approve=True,
    )
    assert_names_present(approved)

    listed = call_as(admin, "publication_request_list")
    assert listed
    for row in listed:
        assert_names_present(row)


def test_the_row_dict_is_additive_over_the_eleven_table_columns(scene, store):
    """The change adds exactly four keys: the eleven table columns stay, and no
    other key appears."""
    created = request_for(scene["editor"], scene["dataset"]["id"])

    columns = {column.name for column in umss_model.PublicationRequest.__table__.columns}
    assert len(columns) == 11
    assert set(created) == columns | set(NAME_KEYS) | set(TITLE_KEYS)


def dataset_resolution_selects(statements):
    """The `SELECT ... FROM package ... WHERE package.id IN (...)` statements,
    the only shape the batched dataset resolver emits. A single-dataset
    `model.Package.get` uses `WHERE package.id = ...` and therefore never matches
    this shape, which is what keeps this counter on the resolver rather than on
    every package lookup the call happens to make. `package` is not a reserved
    word in PostgreSQL, so it is emitted unquoted (unlike `"user"`); whitespace
    and quotes are normalised so the match does not depend on either.
    """
    selects = []
    for statement in statements:
        flat = " ".join(statement.lower().replace('"', "").split())
        if " from package " in " %s " % flat and "package.id in" in flat:
            selects.append(statement)
    return selects


def test_every_action_returns_rows_carrying_both_dataset_titles(scene, store):
    """The portal's queue reads a dataset by its **title** and needs the
    organisation to locate the request, so all four faces answer the same two
    additive keys: `dataset_title` and `organization_title`."""
    editor = scene["editor"]
    admin = scene["admin"]

    created = call_as(
        editor, "publication_request_create", dataset_id=scene["dataset"]["id"]
    )
    assert_titles_present(created)

    cancelled = call_as(editor, "publication_request_cancel", request_id=created["id"])
    assert_titles_present(cancelled)

    rejected_source = request_for(editor, fresh_dataset(scene)["id"])
    rejected = call_as(
        admin,
        "publication_request_decide",
        request_id=rejected_source["id"],
        approve=False,
        comments="no",
    )
    assert_titles_present(rejected)

    approved = call_as(
        admin,
        "publication_request_decide",
        request_id=request_for(editor, scene["second_dataset"]["id"])["id"],
        approve=True,
    )
    assert_titles_present(approved)

    listed = call_as(admin, "publication_request_list")
    assert listed
    for row in listed:
        assert_titles_present(row)


def test_the_dataset_and_org_titles_resolve_to_the_stored_titles(scene, store):
    """The two keys carry the package's own title and its owner
    organisation's title, not ids and not blanks."""
    created = request_for(scene["editor"], scene["dataset"]["id"])

    assert created["dataset_title"] == scene["dataset"]["title"]
    assert created["dataset_title"] != scene["dataset"]["id"]
    assert created["organization_title"] == scene["org"]["title"]
    assert created["organization_title"] != scene["org"]["id"]


def test_an_unowned_dataset_answers_none_for_the_organisation_title(scene, store):
    """Rule 3's two empties, title half: a dataset with no owner organisation
    has nothing to resolve, so `organization_title` is `None` — an empty field
    answers `None`, never `"unknown"` (which claims a broken reference).

    The row is built against an owned dataset and then unowned at the model
    layer on purpose: `publication_request_create` refuses an unowned dataset
    (the caller cannot hold `update_dataset` on nothing), so the ordinary path
    cannot produce this row."""
    editor = scene["editor"]
    dataset = fresh_dataset(scene)
    created = request_for(editor, dataset["id"])

    package = ckan_model.Session.get(ckan_model.Package, dataset["id"])
    package.owner_org = None
    ckan_model.Session.commit()

    listed = call_as(editor, "publication_request_list")
    target = [row for row in listed if row["id"] == created["id"]]
    assert len(target) == 1
    assert target[0]["organization_title"] is None
    assert target[0]["dataset_title"] == dataset["title"]


def test_a_blank_title_answers_none_in_both_fields(scene, store):
    """A resolvable dataset or organisation whose title is empty or
    whitespace-only is a presentation hole, not a value: the consumer's natural
    code is `title ?? fallback`, an empty string is truthy there, and it renders
    as a blank line everywhere. Both fields answer `None`, exactly like an unset
    id answers `None` for the names — never `""` and never `"   "`."""
    editor = scene["editor"]
    dataset = fresh_dataset(scene)
    created = request_for(editor, dataset["id"])

    package = ckan_model.Session.get(ckan_model.Package, dataset["id"])
    package.title = "   "
    organisation = ckan_model.Session.get(ckan_model.Group, scene["org"]["id"])
    organisation.title = ""
    ckan_model.Session.commit()

    listed = call_as(editor, "publication_request_list")
    target = [row for row in listed if row["id"] == created["id"]]
    assert len(target) == 1
    assert target[0]["dataset_title"] is None
    assert target[0]["dataset_title"] != ""
    assert target[0]["organization_title"] is None
    assert target[0]["organization_title"] != ""


def test_an_owner_org_pointing_at_a_missing_group_answers_unknown(scene, store):
    """The other blank neighbouring the empty title: the dataset resolves and
    its `owner_org` is set, but the group behind it does not. That is a broken
    **reference**, so `organization_title` is `"unknown"` — the empty-title
    branch must not swallow it. The dataset's own title still resolves."""
    editor = scene["editor"]
    ghost = "00000000-0000-0000-0000-000000000000"
    dataset = fresh_dataset(scene)
    created = request_for(editor, dataset["id"])

    package = ckan_model.Session.get(ckan_model.Package, dataset["id"])
    package.owner_org = ghost
    ckan_model.Session.commit()

    listed = call_as(editor, "publication_request_list")
    target = [row for row in listed if row["id"] == created["id"]]
    assert len(target) == 1
    assert target[0]["dataset_title"] == dataset["title"]
    assert target[0]["organization_title"] == "unknown"


def test_a_gone_dataset_answers_unknown_for_both_titles(scene, store):
    """Rule 3's other empty: a `dataset_id` that is **set but does not
    resolve** answers the neutral token `"unknown"` for both titles — never the
    raw id in a field called `..._title`."""
    editor = scene["editor"]
    ghost = "00000000-0000-0000-0000-000000000000"
    created = request_for(editor, scene["dataset"]["id"])

    row = the_row(scene["dataset"]["id"])
    row.dataset_id = ghost
    ckan_model.Session.commit()

    listed = call_as(editor, "publication_request_list")
    target = [candidate for candidate in listed if candidate["id"] == created["id"]]
    assert len(target) == 1
    assert target[0]["dataset_id"] == ghost
    assert target[0]["dataset_title"] == "unknown"
    assert target[0]["dataset_title"] != ghost
    assert target[0]["organization_title"] == "unknown"
    assert target[0]["organization_title"] != ghost


def test_list_resolves_dataset_and_org_titles_in_one_query_for_the_whole_page(
    scene, store
):
    """The cost contract: the page's dataset and organisation titles ride the
    **same single query** as the owner-organisation resolution. Three datasets
    under one list call must hit the package/group join once; a per-row resolver
    would measure three. The group join in that same statement is what makes
    the titles ride the organisation resolution rather than add a query to it."""
    editor = scene["editor"]
    admin = scene["admin"]
    for dataset in (
        scene["dataset"],
        scene["second_dataset"],
        fresh_dataset(scene),
    ):
        request_for(editor, dataset["id"])

    listed, statements = measured(lambda: call_as(admin, "publication_request_list"))

    assert len(listed) >= 3
    assert all(row["dataset_title"] for row in listed)

    dataset_selects = dataset_resolution_selects(statements)
    assert len(dataset_selects) == 1, dataset_selects
    assert "join" in dataset_selects[0].lower(), dataset_selects[0]
    assert "group" in dataset_selects[0].lower(), dataset_selects[0]


def test_a_single_row_return_resolves_dataset_titles_in_one_query(scene, store):
    """The single-row faces share the batching: the dataset and its
    organisation resolve in one package/group statement, not two."""
    editor = scene["editor"]
    admin = scene["admin"]
    created = request_for(editor, scene["dataset"]["id"])

    decided, statements = measured(
        lambda: call_as(
            admin,
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )
    )

    assert decided["dataset_title"] == scene["dataset"]["title"]
    assert decided["organization_title"] == scene["org"]["title"]

    dataset_selects = dataset_resolution_selects(statements)
    assert len(dataset_selects) == 1, dataset_selects
    assert "join" in dataset_selects[0].lower(), dataset_selects[0]
    assert "group" in dataset_selects[0].lower(), dataset_selects[0]


def test_the_names_resolve_the_ids_to_usernames(scene, store):
    created = request_for(scene["editor"], scene["dataset"]["id"])
    decided = call_as(
        scene["admin"],
        "publication_request_decide",
        request_id=created["id"],
        approve=True,
    )

    assert created["requested_by_name"] == scene["editor"]["name"]
    assert created["requested_by_name"] != scene["editor"]["id"]
    assert decided["requested_by_name"] == scene["editor"]["name"]
    assert decided["approved_by_name"] == scene["admin"]["name"]


def test_approved_by_name_is_none_when_there_was_no_decision(scene, store):
    """Rule 3's two distinct empties. On `cancelled` the requester withdrew; on
    `annulled` there was no decision. In both the `approved_by` column is empty,
    so the name key is `None` — not `"unknown"`, which would claim a missing
    user rather than no user."""
    editor = scene["editor"]
    admin = scene["admin"]

    cancelled = request_for(editor, scene["dataset"]["id"])
    cancelled_row = call_as(
        editor, "publication_request_cancel", request_id=cancelled["id"]
    )
    assert cancelled_row["approved_by"] is None
    assert cancelled_row["approved_by_name"] is None
    assert cancelled_row["requested_by_name"] == editor["name"]

    # `annulled` used to be produced by the retired `publication_publish`; no
    # action writes that motive any more, so the historical row is built at the
    # model layer. The presentation invariant under test is unchanged: an
    # annulled row carries no decision, so `approved_by` is empty.
    annulled = request_for(editor, scene["second_dataset"]["id"])
    historical = the_row(scene["second_dataset"]["id"])
    historical.status = umss_model.ANNULLED
    historical.motive = umss_model.MOTIVE_PUBLISHED_BY_ANOTHER_PATH
    historical.decided_at = datetime.datetime.now()
    ckan_model.Session.commit()

    listed = call_as(admin, "publication_request_list")
    annulled_rows = [row for row in listed if row["id"] == annulled["id"]]
    assert len(annulled_rows) == 1
    assert annulled_rows[0]["status"] == umss_model.ANNULLED
    assert annulled_rows[0]["approved_by"] is None
    assert annulled_rows[0]["approved_by_name"] is None
    assert annulled_rows[0]["requested_by_name"] == editor["name"]


def test_approved_by_name_names_the_decider_on_approved_and_rejected(scene, store):
    """`approved_by_name` is meaningful exactly on the two decided outcomes."""
    editor = scene["editor"]
    admin = scene["admin"]

    rejected_source = request_for(editor, scene["dataset"]["id"])
    rejected = call_as(
        admin,
        "publication_request_decide",
        request_id=rejected_source["id"],
        approve=False,
        comments="no",
    )
    assert rejected["approved_by"] == admin["id"]
    assert rejected["approved_by_name"] == admin["name"]

    approved_source = request_for(editor, scene["second_dataset"]["id"])
    approved = call_as(
        admin,
        "publication_request_decide",
        request_id=approved_source["id"],
        approve=True,
    )
    assert approved["approved_by"] == admin["id"]
    assert approved["approved_by_name"] == admin["name"]


def test_a_set_but_unresolvable_id_answers_unknown_and_never_the_id(scene, store):
    """Rule 3's neutral fallback: a field called `..._name` must never contain
    an id. The row keeps the raw id in its id column; the name key says
    `"unknown"`."""
    editor = scene["editor"]
    admin = scene["admin"]
    ghost = "00000000-0000-0000-0000-000000000000"

    created = request_for(editor, scene["dataset"]["id"])
    call_as(admin, "publication_request_decide", request_id=created["id"], approve=True)

    row = the_row(scene["dataset"]["id"])
    row.approved_by = ghost
    ckan_model.Session.commit()

    listed = call_as(admin, "publication_request_list", status="approved")
    target = [candidate for candidate in listed if candidate["id"] == created["id"]]
    assert len(target) == 1
    assert target[0]["approved_by"] == ghost
    assert target[0]["approved_by_name"] == "unknown"
    assert target[0]["approved_by_name"] != ghost
    assert ghost not in target[0]["approved_by_name"]

    # The requester side has the same fallback.
    row.requested_by = ghost
    ckan_model.Session.commit()

    listed = call_as(admin, "publication_request_list", status="approved")
    target = [candidate for candidate in listed if candidate["id"] == created["id"]]
    assert len(target) == 1
    assert target[0]["requested_by"] == ghost
    assert target[0]["requested_by_name"] == "unknown"
    assert target[0]["requested_by_name"] != ghost


def test_list_resolves_every_name_in_one_query_for_the_whole_page(scene, store):
    """Rule 3 is a **cost** contract, so the test measures the cost, not only
    the names: three pending rows under one list call must hit the user table
    once. A per-row resolver would measure three."""
    editor = scene["editor"]
    admin = scene["admin"]
    datasets = [
        scene["dataset"],
        scene["second_dataset"],
        fresh_dataset(scene),
    ]
    for dataset in datasets:
        request_for(editor, dataset["id"])

    listed, statements = measured(
        lambda: call_as(admin, "publication_request_list")
    )

    assert len(listed) >= 3
    assert all(row["requested_by_name"] == editor["name"] for row in listed)

    user_selects = name_resolution_selects(statements)
    assert len(user_selects) == 1, user_selects


def test_a_single_row_return_resolves_both_ids_in_one_query(scene, store):
    """The single-row faces share the batching: both id columns live in the
    same `IN`, so a decision costs one user query, not two."""
    editor = scene["editor"]
    admin = scene["admin"]
    created = request_for(editor, scene["dataset"]["id"])

    decided, statements = measured(
        lambda: call_as(
            admin,
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )
    )

    assert decided["requested_by_name"] == editor["name"]
    assert decided["approved_by_name"] == admin["name"]

    user_selects = name_resolution_selects(statements)
    assert len(user_selects) == 1, user_selects


