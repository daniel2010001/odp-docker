"""
Tests for the five publication actions (`ckanext.umss.logic.action.publication`).

Design reference: `design.md` D4 (the five actions and who they are for) and D5
(the door writes the record and flips the value in one transaction). These tests
drive the actions through `logic.get_action`, which is the path the API
controller takes, so they prove the actions are **registered**, not merely
defined.

Phase A2.1 is RED: the module does not exist yet, so this file fails at import.
There is no `skip`/`xfail` to soften that.

The contract these tests pin, for the portal that consumes it:

| action | arguments | returns |
|---|---|---|
| `publication_request_create` | `dataset_id`, `comments` (optional) | the row as a dict |
| `publication_request_cancel` | `request_id` | the row as a dict |
| `publication_request_decide` | `request_id`, `approve`, `comments` (optional) | the row as a dict |
| `publication_publish` | `dataset_id`, `comments` (optional) | the row as a dict |
| `publication_request_list` | `status` (optional) | a list of row dicts |

`requested_by` and `approved_by` carry **user ids**, which is the convention the
rest of CKAN follows (`package_show` answers `creator_user_id`, not a name).
"""
import pytest

import ckan.model as ckan_model
import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers

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
# A2.1 — the surface: the five actions exist, are registered, and do what D4 says
# ---------------------------------------------------------------------------


def test_the_five_actions_are_defined():
    assert callable(actions.publication_request_create)
    assert callable(actions.publication_request_cancel)
    assert callable(actions.publication_request_decide)
    assert callable(actions.publication_publish)
    assert callable(actions.publication_request_list)


def test_the_plugin_registers_the_five_actions():
    """The deliverable: `logic.get_action` must resolve each name, which is what
    the API and the portal use."""
    for name in (
        "publication_request_create",
        "publication_request_cancel",
        "publication_request_decide",
        "publication_publish",
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


def test_publish_writes_and_consumes_the_row_in_the_act(scene, store):
    """D4's sysadmin path: no queue, one row that is born already decided."""
    sysadmin = factories.Sysadmin()
    published = call_as(
        sysadmin,
        "publication_publish",
        dataset_id=scene["dataset"]["id"],
        comments="lo publico yo",
    )

    row = the_row(scene["dataset"]["id"])
    assert published["status"] == "approved"
    assert row.status == "approved"
    assert row.requested_by == sysadmin["id"]
    assert row.approved_by == sysadmin["id"]
    assert row.decided_at is not None
    assert row.consumed_at is not None
    assert stored(scene["dataset"]["id"])["private"] is False


def test_publish_annuls_a_pending_request_instead_of_leaving_it_open(scene, store):
    """`annulled` is what D2 added it for, and it is not `cancelled`: the
    requester did not withdraw it — a direct sysadmin action made it moot.

    The pending row records **why** (`motive`), and the direct publish writes
    **one** new row: the pending one is annulled, never turned into a second
    `approved`."""
    request_for(scene["editor"], scene["dataset"]["id"])

    call_as(
        factories.Sysadmin(),
        "publication_publish",
        dataset_id=scene["dataset"]["id"],
    )

    for_dataset = [r for r in rows() if r.dataset_id == scene["dataset"]["id"]]
    statuses = sorted(r.status for r in for_dataset)
    assert statuses == ["annulled", "approved"]
    assert len(for_dataset) == 2

    annulled = [r for r in for_dataset if r.status == umss_model.ANNULLED]
    approved = [r for r in for_dataset if r.status == umss_model.APPROVED]
    assert len(annulled) == 1
    assert len(approved) == 1, "the direct publish must not write a second approval"
    assert annulled[0].status != umss_model.CANCELLED
    assert annulled[0].motive is not None
    assert annulled[0].motive == umss_model.MOTIVE_PUBLISHED_BY_ANOTHER_PATH
    assert annulled[0].decided_at is not None
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
# A2.3 — who may do each of the five (D4), written before the auth existed
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


def test_an_editor_cannot_publish(scene, store):
    refused(scene["editor"], "publication_publish", dataset_id=scene["dataset"]["id"])
    assert stored(scene["dataset"]["id"])["private"] is True


def test_an_org_admin_cannot_publish_directly(scene, store):
    """The governance amendment. `publication_publish` is sysadmin-only; the
    org admin keeps only the stock `package_patch {private: false}` route, which
    the wall (`ckanext.umss.auth`) owns, not this action."""
    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            scene["admin"],
            "publication_publish",
            dataset_id=scene["dataset"]["id"],
        )

    assert "sysadmin" in str(excinfo.value).lower()
    assert stored(scene["dataset"]["id"])["private"] is True
    assert rows() == []


def test_a_sysadmin_can_publish(scene, store):
    """Positive control, not a discriminating test: this passes under the old
    org-admin-grants-publish code too, because CKAN short-circuits a sysadmin to
    success. It guards the amendment against over-narrowing — the action must
    not be closed to a sysadmin — but the change itself is proven by
    `test_an_org_admin_cannot_publish_directly`."""
    sysadmin = factories.Sysadmin()
    published = call_as(
        sysadmin,
        "publication_publish",
        dataset_id=scene["dataset"]["id"],
        comments="por el sysadmin",
    )

    row = the_row(scene["dataset"]["id"])
    assert published["status"] == "approved"
    assert row.requested_by == sysadmin["id"]
    assert row.approved_by == sysadmin["id"]
    assert stored(scene["dataset"]["id"])["private"] is False


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


def test_an_org_admin_can_decide_but_not_publish(scene, store):
    """The governance amendment narrows D4: an org admin still decides for
    someone else, but `publication_publish` is no longer an org-admin authority.
    The direct publish is a sysadmin's (`test_a_sysadmin_can_publish`)."""
    created = request_for(scene["editor"], scene["dataset"]["id"])
    decided = call_as(
        scene["admin"], "publication_request_decide", request_id=created["id"], approve=True
    )
    assert decided["status"] == "approved"

    with pytest.raises(toolkit.NotAuthorized):
        call_as(
            scene["admin"],
            "publication_publish",
            dataset_id=scene["second_dataset"]["id"],
        )
    assert stored(scene["second_dataset"]["id"])["private"] is True


def test_an_admin_cannot_publish_in_an_organization_they_do_not_administer(scene, store):
    """The capacity is per organization, which is the whole point of `owner_org`."""
    refused(
        scene["admin"], "publication_publish", dataset_id=scene["other_dataset"]["id"]
    )
    assert stored(scene["other_dataset"]["id"])["private"] is True


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

    assert "four eyes" in str(excinfo.value).lower()
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
    sysadmin, whose sanctioned path is `publication_publish`."""
    sysadmin = factories.Sysadmin()
    created = request_for(sysadmin, scene["dataset"]["id"])

    with pytest.raises(toolkit.NotAuthorized) as excinfo:
        call_as(
            sysadmin,
            "publication_request_decide",
            request_id=created["id"],
            approve=True,
        )

    assert "four eyes" in str(excinfo.value).lower()
    assert stored(scene["dataset"]["id"])["private"] is True
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


def test_a_failed_flip_on_publish_leaves_the_pending_request_untouched(
    scene, store, failing_flip
):
    """The annulment of the pending request rolls back with the flip."""
    request_for(scene["editor"], scene["dataset"]["id"])

    with pytest.raises(toolkit.ValidationError):
        call_as(
            factories.Sysadmin(),
            "publication_publish",
            dataset_id=scene["dataset"]["id"],
        )

    row = the_row(scene["dataset"]["id"])
    assert row.status == "pending"
    assert stored(scene["dataset"]["id"])["private"] is True


# ---------------------------------------------------------------------------
# The correction round: what the four-lens review at tier high opened
#
# Only the two CRITICALs are obligatory (`fix_finding_ids`), and they are one
# defect seen twice: neither writing action resolved its `dataset_id`, and the
# auth functions answer `success` for an unresolvable id on purpose.
# ---------------------------------------------------------------------------


def test_create_refuses_an_unknown_dataset_and_writes_nothing(scene, store):
    """R1-ORPHAN-ROW and R3-001: without this check, any authenticated editor
    could write `pending` rows for datasets that do not exist."""
    with pytest.raises(toolkit.ObjectNotFound):
        call_as(
            scene["editor"], "publication_request_create", dataset_id="no-such-dataset"
        )

    assert rows() == []


def test_publish_refuses_an_unknown_dataset_and_writes_nothing(scene, store):
    """The same hole lived in the other writing action."""
    with pytest.raises(toolkit.ObjectNotFound):
        call_as(
            factories.Sysadmin(),
            "publication_publish",
            dataset_id="no-such-dataset",
        )

    assert rows() == []


def test_a_non_sysadmin_gets_not_found_for_an_unknown_dataset(scene, store):
    """Contract rule 7: an unresolvable `dataset_id` answers `NotFound`, not
    `403` — for a non-sysadmin too. The auth function resolves the id first and
    defers existence to the action; the capacity predicate narrows *who* may
    publish, it does not pre-empt *whether the thing exists*."""
    with pytest.raises(toolkit.ObjectNotFound):
        call_as(
            scene["admin"],
            "publication_publish",
            dataset_id="no-such-dataset",
        )

    assert rows() == []


# ---------------------------------------------------------------------------
# A2.6 / A2.7 — the decision re-checks the current state, and a pending request
# whose object is gone is annulled
#
# A2.7 has exactly two triggers: the dataset deleted, or published by another
# path. The requester losing capacity is **not** one of them: the decision is
# refused as an authorization failure and the row stays `pending` (the author's
# decision, 2026-10-07). `annulled` and `cancelled` stay distinct throughout:
# the requester did not withdraw.
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
    assert "four eyes" not in str(excinfo.value).lower()
    assert the_row(scene["dataset"]["id"]).status == "pending"
    assert stored(scene["dataset"]["id"])["private"] is True


def test_a_sysadmin_approver_is_also_refused_when_the_requester_lost_capacity(
    scene, store
):
    """The requester-capacity rule has no sysadmin exception, for the same
    reason four eyes has none: a sysadmin's sanctioned alternative is
    `publication_publish`, which annuls the pending row and publishes in the
    act. Without this, the rule would be written, green and hollow for a
    sysadmin approver — the function carries `auth_sysadmins_check`, so the
    check must sit before the sysadmin short-circuit.

    Discriminating: the sysadmin short-circuit would otherwise return success
    before the rule ran.
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
    assert "four eyes" not in str(excinfo.value).lower()
    assert the_row(scene["dataset"]["id"]).status == "pending"
    assert stored(scene["dataset"]["id"])["private"] is True


def test_decide_fails_closed_when_the_requester_cannot_be_resolved(scene, store):
    """Triangulation of the requester half: an unresolvable requester (a
    deleted user) fails closed. `requested_by` holds a user id; the escape
    hatch is the sysadmin's `publication_publish`, not an open decision."""
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
    assert "four eyes" not in str(excinfo.value).lower()
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
