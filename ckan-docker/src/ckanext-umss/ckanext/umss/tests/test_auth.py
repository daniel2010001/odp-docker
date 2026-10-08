"""
Tests for the publication-lifecycle authorization guard (`ckanext.umss.auth`).

These tests exercise the guard the way a caller does — through CKAN's action
layer, with the authorization layer active — so they prove the predicate is
*reachable*, not merely that a Python function returns a dict. They run
in-process and therefore prove intent only: `probe.sh` in
`openspec/changes/2026-09-13-publication-lifecycle/` is what proves the running
image, because no test here can show that the deployment registered this plugin.

Design reference: `design.md` D1 (the carrier), D3 (the rule and its truth
table) and the "Measured baseline" section, which the expectations below follow
wherever the two disagree. The one expectation that a correct reading of the
measured baseline forced away from `design.md`/`spec.md`'s literal wording is
marked `MEASURED OVERRIDE` and explained in place.
"""
import pytest

import ckan.authz
import ckan.logic as logic
import ckan.model
from ckan.tests import factories, helpers

from ckanext.umss import auth as umss_auth
from ckanext.umss import model as umss_model


pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss"),
    pytest.mark.usefixtures("with_plugins", "clean_db"),
]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def call_as(user, action, **data):
    """Call ``action`` as ``user`` (or anonymously when ``user`` is ``None``).

    Deliberately **not** `ckan.tests.helpers.call_action`, for the two reasons
    that make it unusable for authorization assertions:

    1. it does ``context.setdefault("ignore_auth", True)``, and
       ``authz.is_authorized`` returns success immediately when ``ignore_auth``
       is truthy — so a test written with it exercises the action, never the
       guard;
    2. forcing the key to ``False`` is not the fix either.
       ``ckan.logic.validators.ignore_not_package_admin`` tests
       ``'ignore_auth' in context`` — key *presence*, not value — so a present
       but False key silently disables the create-time ``state`` drop that a real
       request gets, which would make the create-time behaviour under test
       differ from production.

    This calls the action the way the API controller does, with a context that
    carries only ``user``.
    """
    return logic.get_action(action)({"user": user and user["name"]}, data)


def stored(dataset_id):
    """The dataset as the database holds it, with authorization bypassed."""
    ctx = {"user": "default", "ignore_auth": True, "use_cache": False}
    return helpers.call_action("package_show", ctx, id=dataset_id)


def add_user_member(org_id, user_id, capacity):
    """`member_create` for a user row.

    Fixtures use `helpers.call_action` on purpose: they run as the site user (a
    sysadmin) and must not be gated by the rule under test.
    """
    return helpers.call_action(
        "member_create",
        id=org_id,
        object=user_id,
        object_type="user",
        capacity=capacity,
    )


@pytest.fixture
def scene():
    """One organization with an editor/member/admin, an outsider's organization,
    and a private dataset owned by the first one."""
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
    return {
        "org": org,
        "other_org": other_org,
        "editor": editor,
        "member": member,
        "admin": admin,
        "outsider": outsider,
        "dataset": dataset,
    }


@pytest.fixture
def store(clean_db, migrate_db_for):
    """The store table, built by this extension's own migration.

    The recorded-door assertions need `publication_requests`; `clean_db` does
    not build extension tables, so the migration has to run. `clean_db` is an
    explicit dependency of this fixture, not an accident: it must wipe every
    row before the table is built and before any factory in the test runs.
    """
    migrate_db_for("umss")


def publication_rows(dataset_id):
    """The `publication_requests` rows for a dataset."""
    ckan.model.Session.commit()
    ckan.model.Session.expire_all()
    return (
        ckan.model.Session.query(umss_model.PublicationRequest)
        .filter(umss_model.PublicationRequest.dataset_id == dataset_id)
        .all()
    )


# ---------------------------------------------------------------------------
# 1.2.1 — the update path: an editor must not publish, and must not change state
# ---------------------------------------------------------------------------


def test_the_guard_is_registered_for_every_guarded_action_name():
    """The plugin must own the chained functions, not merely define them."""
    assert umss_auth.package_update.chained_auth_function is True
    assert umss_auth.package_create.chained_auth_function is True
    assert umss_auth.bulk_update_public.chained_auth_function is True
    for action in ("package_update", "package_create", "bulk_update_public"):
        registered = ckan.authz._AuthFunctions.get(action)
        assert getattr(registered, "chained_auth_function", False) is True, action


def test_the_guard_sets_auth_sysadmins_check():
    """The closure is load-bearing: with `auth_sysadmins_check`, `is_authorized`
    calls the chained rule for a sysadmin instead of short-circuiting to success
    (`ckan/authz.py:224-228`), which is what lets the wall refuse the publication
    transition to every caller. Without the flag the sysadmin would still
    publish through the stock route."""
    for action in ("package_update", "package_create", "bulk_update_public"):
        registered = ckan.authz._AuthFunctions.get(action)
        assert getattr(registered, "auth_sysadmins_check", False) is True, action


def test_editor_package_patch_private_false_is_refused(scene):
    """The deliverable. Measured P3: answered `200` and stored a public dataset."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["editor"], "package_patch",
                id=scene["dataset"]["id"], private=False)
    assert umss_auth.PUBLISH_DENIED_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_editor_package_patch_state_draft_is_refused(scene):
    """Measured P4a: answered `200` and stored `state=draft` before the guard."""
    with pytest.raises(logic.NotAuthorized):
        call_as(scene["editor"], "package_patch",
                id=scene["dataset"]["id"], state="draft")
    assert stored(scene["dataset"]["id"])["state"] == "active"


def test_editor_full_package_update_flipping_private_is_refused(scene):
    payload = dict(stored(scene["dataset"]["id"]))
    payload.pop("tracking_summary", None)
    payload["private"] = False
    with pytest.raises(logic.NotAuthorized):
        call_as(scene["editor"], "package_update", **payload)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_sysadmin_stock_package_patch_is_refused_and_writes_nothing(scene, store):
    """The closed route, not the old bypass. This test used to pin the
    sysadmin's unrecorded stock escape: CKAN short-circuits a sysadmin before any
    auth function unless the function carries `auth_sysadmins_check`, and the wall
    did not. The author's decision closes that route — no caller publishes
    directly — so the wall now carries the flag and refuses the transition, and
    the refusal writes no row either."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(factories.Sysadmin(), "package_patch",
                id=scene["dataset"]["id"], private=False)
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True
    assert publication_rows(scene["dataset"]["id"]) == []


def test_sysadmin_publishing_package_update_is_refused(scene, store):
    """The full update loop, not only `package_patch`: a `package_update` that
    requests `private=False` is refused for a sysadmin too, and the stored value
    does not move."""
    payload = dict(stored(scene["dataset"]["id"]))
    payload.pop("tracking_summary", None)
    payload["private"] = False
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(factories.Sysadmin(), "package_update", **payload)
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True
    assert publication_rows(scene["dataset"]["id"]) == []


@pytest.mark.parametrize("name, payload", [
    ("sysadmin-create-false", {"private": False}),
    ("sysadmin-create-omitted", {}),
])
def test_sysadmin_public_package_create_is_refused(scene, store, name, payload):
    """`false` and an omitted key are both publication attempts at create time,
    and the sysadmin is refused on both: nothing is created."""
    data = dict(payload, name=name, owner_org=scene["org"]["id"])
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(factories.Sysadmin(), "package_create", **data)
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    with pytest.raises(logic.NotFound):
        stored(name)


def test_sysadmin_bulk_update_public_is_refused(scene, store):
    """The bulk door carves out no caller: the sysadmin gets the flow message,
    like any caller the flow authorizes."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(factories.Sysadmin(), "bulk_update_public",
                org_id=scene["org"]["id"], datasets=[scene["dataset"]["id"]])
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_sysadmin_private_package_create_is_still_allowed(scene):
    """Creating private is not publishing: the wizard's payload keeps working
    for a sysadmin."""
    created = call_as(factories.Sysadmin(), "package_create",
                      name="sysadmin-private-create",
                      owner_org=scene["org"]["id"], private=True)
    assert created["private"] is True


def test_sysadmin_metadata_only_patch_is_still_allowed(scene):
    call_as(factories.Sysadmin(), "package_patch",
            id=scene["dataset"]["id"], title="sysadmin metadata edit")
    updated = stored(scene["dataset"]["id"])
    assert updated["title"] == "sysadmin metadata edit"
    assert updated["private"] is True


def test_sysadmin_state_change_is_still_allowed(scene):
    """`state` administration is not publishing: the sysadmin keeps it, and the
    wall refuses it only below a sysadmin."""
    call_as(factories.Sysadmin(), "package_patch",
            id=scene["dataset"]["id"], state="draft")
    assert stored(scene["dataset"]["id"])["state"] == "draft"


def test_sysadmin_bulk_update_delete_is_still_allowed(scene):
    """The preserved capability through the bulk route: `bulk_update_delete`
    loops `package_patch {state: 'deleted'}`, and the wall lets the sysadmin's
    `state` change through."""
    call_as(factories.Sysadmin(), "bulk_update_delete",
            org_id=scene["org"]["id"], datasets=[scene["dataset"]["id"]])
    ckan.model.Session.commit()
    ckan.model.Session.expire_all()
    deleted = ckan.model.Session.get(ckan.model.Package, scene["dataset"]["id"])
    assert deleted.state == "deleted"


def test_the_sanctioned_flow_still_publishes_with_a_sysadmin_approver(scene, store):
    """The proof the closed wall did not break the only remaining door: the
    sysadmin cannot publish directly, but the flow still publishes when the
    sysadmin approves someone else's request. The approving action writes with
    `ignore_auth`, so the wall is never consulted for the flip."""
    created = call_as(scene["editor"], "publication_request_create",
                      dataset_id=scene["dataset"]["id"])
    decided = call_as(factories.Sysadmin(), "publication_request_decide",
                      request_id=created["id"], approve=True)
    assert decided["status"] == "approved"
    assert stored(scene["dataset"]["id"])["private"] is False


def test_org_admin_package_patch_private_false_is_refused(scene):
    """A3.1: the reversal of measured P6 (`apply-progress.md:282`).

    The same call answered `200` and stored `private: false` before this change.
    The admin is the *approver* the door's authorization names, but the door is
    the publication flow, so the wall refuses with its own message — distinct
    from the one a non-administrator gets.
    """
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["admin"], "package_patch",
                id=scene["dataset"]["id"], private=False)
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert umss_auth.PUBLISH_DENIED_MSG not in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_org_admin_package_patch_state_draft_is_refused(scene):
    """A3.1: measured P4a, now for the approver too — the capacity exception is
    gone, so a `state` change is refused with the flow message."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["admin"], "package_patch",
                id=scene["dataset"]["id"], state="draft")
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["state"] == "active"


def test_unrecognized_private_value_does_not_publish(scene):
    """MEASURED OVERRIDE of `spec.md` ("An unrecognized value defers to core CKAN").

    `design.md` D3's rule 2 and `spec.md`'s scenario both assume core rejects an
    unrecognized `private` with a validation error. It does not: CKAN's
    `boolean_validator` (`ckan/logic/validators.py:160-173`) is total and returns
    `False` for every value outside `{'true','yes','t','y','1'}` — measured live
    as probe rows P4d (`'banana'`) and P4e (`''`), both of which answered `200`
    and stored a **public** dataset before the guard existed. Deferring on such a
    value would therefore not defer to a validation error; it would defer to a
    publication, which is the transition `Publication Authorization` forbids.
    The guard refuses instead, and that spec clause needs amending.
    """
    for value in ("banana", "", None):
        with pytest.raises(logic.NotAuthorized):
            call_as(scene["editor"], "package_patch",
                    id=scene["dataset"]["id"], private=value)
        assert stored(scene["dataset"]["id"])["private"] is True


@pytest.mark.parametrize("value, tag", [
    (0, "int-zero"),
    (0.0, "float-zero"),
    ([], "empty-list"),
    ({}, "empty-dict"),
])
def test_editor_package_patch_private_never_defers_to_core(scene, value, tag):
    """`boolean_validator` is total and never raises: it coerces a `bool`/`int`
    (`private = 0` -> `False` = **public**) and returns `False` for everything
    else. `_as_bool` must mirror that, so no value may defer to core's
    validation — deferring here is deferring to a publication. Measured before
    the fix (probe P4f): `private = 0` answered `200` and stored a public
    dataset, because the guard read the non-`str` value as `None` and deferred."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["editor"], "package_patch",
                id=scene["dataset"]["id"], private=value)
    assert umss_auth.PUBLISH_DENIED_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["private"] is True


@pytest.mark.parametrize("value", [0, 0.0, [], {}])
def test_editor_full_package_update_private_never_defers_to_core(scene, value):
    payload = dict(stored(scene["dataset"]["id"]))
    payload.pop("tracking_summary", None)
    payload["private"] = value
    with pytest.raises(logic.NotAuthorized):
        call_as(scene["editor"], "package_update", **payload)
    assert stored(scene["dataset"]["id"])["private"] is True


@pytest.mark.parametrize("value, tag", [
    (0, "int-zero"),
    (0.0, "float-zero"),
    ([], "empty-list"),
    ({}, "empty-dict"),
])
def test_editor_package_create_private_never_defers_to_core(scene, value, tag):
    name = "probe-editor-create-%s" % tag
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["editor"], "package_create",
                name=name, owner_org=scene["org"]["id"], private=value)
    assert umss_auth.PUBLISH_DENIED_MSG in str(excinfo.value)
    with pytest.raises(logic.NotFound):
        stored(name)


def test_unresolvable_id_defers_to_core(scene):
    """Core raises its own `NotFound`; the guard must not convert it to a `403`."""
    with pytest.raises(logic.NotFound):
        call_as(scene["editor"], "package_patch",
                id="00000000-0000-0000-0000-000000000000", private=False)


def test_private_false_on_an_already_public_dataset_requests_no_transition(scene):
    """No diff is requested, so the guard has nothing to refuse — for every
    caller, approver or not. Built public through a factory, not through the
    stock route the wall now closes."""
    public = factories.Dataset(owner_org=scene["org"]["id"], private=False)
    call_as(scene["admin"], "package_patch", id=public["id"], private=False)
    call_as(scene["editor"], "package_patch", id=public["id"], private=False)
    assert stored(public["id"])["private"] is False


def test_the_two_refusal_messages_are_distinguishable(scene):
    """A3.3: the wall carries two messages, and they are not interchangeable.
    Each carries its **own frozen label** as the first token, so the two
    refusals separate on the label the portal matches rather than on the free
    sentence after it."""
    with pytest.raises(logic.NotAuthorized) as editor_exc:
        call_as(scene["editor"], "package_patch",
                id=scene["dataset"]["id"], private=False)
    with pytest.raises(logic.NotAuthorized) as admin_exc:
        call_as(scene["admin"], "package_patch",
                id=scene["dataset"]["id"], private=False)

    assert umss_auth.PUBLISH_DENIED_MSG in str(editor_exc.value)
    assert umss_auth.PUBLISH_DENIED_MSG not in str(admin_exc.value)
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(admin_exc.value)
    assert umss_auth.PUBLISH_DENIED_MSG != umss_auth.PUBLISH_VIA_FLOW_MSG
    assert umss_auth.PUBLISH_DENIED_MSG.startswith(
        umss_auth.PUBLISH_DENIED_LABEL + ": "
    )
    assert umss_auth.PUBLISH_VIA_FLOW_MSG.startswith(
        umss_auth.PUBLICATION_FLOW_LABEL + ": "
    )
    assert umss_auth.PUBLISH_DENIED_LABEL != umss_auth.PUBLICATION_FLOW_LABEL


def test_the_wall_refusal_labels_are_the_interface_values():
    """The wall's two refusal **labels** are what a consumer matches on: the
    portal reads them from a single constant that points at
    `PUBLICATION-ACTIONS.md`, and CKAN gives no machine-readable code — an
    authorization failure is only `{"__type": "Authorization Error",
    "message": ...}` — so the label is the interface and the sentence after it
    is free prose.

    This pin freezes the labels, not the sentences. Rewording the prose after a
    colon passes; changing a label fails, and that failure is the signal that
    the consumer's constant has gone stale. Breaking it means a refusal two
    different callers receive can no longer be told apart, or a label the
    portal does not know arrives where it expects one it does.

    A **pin, not a RED**: it passes against the constants as first written.
    """
    assert umss_auth.PUBLISH_DENIED_LABEL == "Publish denied"
    assert umss_auth.PUBLICATION_FLOW_LABEL == "Publication flow"
    assert umss_auth.PUBLISH_DENIED_LABEL != umss_auth.PUBLICATION_FLOW_LABEL
    assert umss_auth.PUBLISH_DENIED_MSG.startswith("Publish denied: ")
    assert umss_auth.PUBLISH_VIA_FLOW_MSG.startswith("Publication flow: ")


# ---------------------------------------------------------------------------
# 1.2.3 — the create path: at create time an omitted `private` IS a publication
# ---------------------------------------------------------------------------


def test_editor_package_create_to_a_public_dataset_is_refused(scene):
    """`false`, an omitted key and a value core coerces to `false` are the same
    publication attempt at create time, and none of them may create anything."""
    for name, payload in (
        ("probe-create-false", {"private": False}),
        ("probe-create-omitted", {}),
        ("probe-create-coerced", {"private": "banana"}),
    ):
        data = dict(payload, name=name, owner_org=scene["org"]["id"])
        with pytest.raises(logic.NotAuthorized) as excinfo:
            call_as(scene["editor"], "package_create", **data)
        assert umss_auth.PUBLISH_DENIED_MSG in str(excinfo.value)
        with pytest.raises(logic.NotFound):
            stored(name)


def test_omitting_private_resolves_to_the_public_column_default(scene):
    """Why the omission above is a publication attempt and not a formality.

    The same payload the editor was refused for, sent by a caller who *may*
    publish, stores a public dataset: `private` is absent from the requested
    dict, so `ignore_missing` drops it and the column default
    (`ckan/model/package.py:75`, `default=False`) applies. No action caller may
    publish directly any more, so the factory below is a test-only write that
    is not gated by the wall.
    """
    created = factories.Dataset(owner_org=scene["org"]["id"])
    assert created["private"] is False
    assert stored(created["id"])["private"] is False


def test_org_admin_package_create_public_is_refused(scene):
    """A3.1/D6.2: creation is private for **everyone**. An omitted `private` is
    the same publish attempt as `false`, and even the approver is refused — the
    administrator publishes afterwards through the action, not through create."""
    for name, payload in (
        ("probe-admin-create-false", {"private": False}),
        ("probe-admin-create-omitted", {}),
    ):
        data = dict(payload, name=name, owner_org=scene["org"]["id"])
        with pytest.raises(logic.NotAuthorized) as excinfo:
            call_as(scene["admin"], "package_create", **data)
        assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
        with pytest.raises(logic.NotFound):
            stored(name)


def test_editor_package_create_with_the_wizards_payload_is_allowed(scene):
    created = call_as(scene["editor"], "package_create",
                      name="probe-create-private",
                      owner_org=scene["org"]["id"], private=True)
    assert created["private"] is True
    assert stored(created["id"])["state"] == "active"


def test_create_time_state_is_not_refused_by_this_rule(scene):
    """`state` is dropped for every non-sysadmin at create time
    (`ignore_not_package_admin` finds no `context['package']`), so a refusal here
    would deny a request CKAN was never going to honour."""
    created = call_as(scene["editor"], "package_create",
                      name="probe-create-draft",
                      owner_org=scene["org"]["id"],
                      private=True, state="draft")
    assert stored(created["id"])["state"] == "active"


def test_editor_full_update_omitting_private_is_allowed(scene):
    """The other side of the asymmetry: on update, omission changes nothing."""
    payload = dict(stored(scene["dataset"]["id"]))
    payload.pop("tracking_summary", None)
    payload.pop("private", None)
    payload["title"] = "probe full update without private"
    call_as(scene["editor"], "package_update", **payload)
    assert stored(scene["dataset"]["id"])["private"] is True


# ---------------------------------------------------------------------------
# 1.2.5 — the refusals that already held, and the writes the rule must not touch
# ---------------------------------------------------------------------------


def test_org_member_is_refused(scene):
    with pytest.raises(logic.NotAuthorized):
        call_as(scene["member"], "package_patch",
                id=scene["dataset"]["id"], private=False)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_editor_of_another_org_is_refused(scene):
    with pytest.raises(logic.NotAuthorized):
        call_as(scene["outsider"], "package_patch",
                id=scene["dataset"]["id"], private=False)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_anonymous_is_refused(scene):
    with pytest.raises(logic.NotAuthorized):
        call_as(None, "package_patch", id=scene["dataset"]["id"], private=False)
    assert stored(scene["dataset"]["id"])["private"] is True


def test_metadata_only_patch_is_allowed(scene):
    call_as(scene["editor"], "package_patch",
            id=scene["dataset"]["id"], title="probe metadata edit")
    updated = stored(scene["dataset"]["id"])
    assert updated["title"] == "probe metadata edit"
    assert updated["private"] is True
    assert updated["state"] == "active"


def test_package_delete_is_allowed(scene):
    """`package_delete` authorizes through `package_update` and passes only
    `{id}`, so the guard must see no visibility request at all."""
    call_as(scene["editor"], "package_delete", id=scene["dataset"]["id"])


def test_resource_create_is_allowed(scene):
    """`resource_create` authorizes `package_update` with `{id: pkg.id}` only."""
    created = call_as(scene["editor"], "resource_create",
                      package_id=scene["dataset"]["id"],
                      name="probe resource", url="https://example.invalid/data.csv")
    assert created["package_id"] == scene["dataset"]["id"]
    assert len(stored(scene["dataset"]["id"])["resources"]) == 1
    assert stored(scene["dataset"]["id"])["private"] is True


# ---------------------------------------------------------------------------
# D6.3 — the bulk door: refused on its own auth, before the package_patch loop
# ---------------------------------------------------------------------------


def test_bulk_update_public_is_refused_by_the_chained_rule(scene):
    """A3.1/D6.3: the refusal is this plugin's, on `bulk_update_public`'s own auth.

    Measured on the running CKAN 2.12.0: `_bulk_update_dataset`
    (`ckan/logic/action/update.py:1200-1216`) loops `package_patch`, so the
    action **does** reach `package_update` — the earlier claim that it did not
    came from a stale checkout. The chain here is on the action's own auth and
    refuses before the body runs, so that loop is never entered.

    Core's own auth for the action (`ckan/logic/auth/update.py:262`) checks only
    `has_user_permission_for_group_or_org(org_id, user, 'update')`: an `editor`
    fails that (the `editor` role carries `update_dataset`, not `update`), an
    `admin` passes it. The refusal must therefore be **ours** in both cases —
    the editor's message proves core did not answer it, because core answers
    `{'success': False}` with no message.

    The message is chosen by fact, and on this route the fact is the payload's
    `org_id`: the `admin` who holds the capacity for that organization is a
    caller the flow authorizes, so the refusal names the flow — exactly as it
    did before the sysadmin's own route was closed.
    """
    with pytest.raises(logic.NotAuthorized) as editor_exc:
        call_as(scene["editor"], "bulk_update_public",
                org_id=scene["org"]["id"], datasets=[scene["dataset"]["id"]])
    assert umss_auth.PUBLISH_DENIED_MSG in str(editor_exc.value)

    with pytest.raises(logic.NotAuthorized) as admin_exc:
        call_as(scene["admin"], "bulk_update_public",
                org_id=scene["org"]["id"], datasets=[scene["dataset"]["id"]])
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(admin_exc.value)
    assert umss_auth.PUBLISH_DENIED_MSG not in str(admin_exc.value)

    assert stored(scene["dataset"]["id"])["private"] is True


# ---------------------------------------------------------------------------
# The bulk-update inventory, executed rather than source-read
# ---------------------------------------------------------------------------


def test_editor_bulk_update_delete_is_refused(scene):
    """A2 named the bulk-update delete bypass; the editor's outcome is refusal,
    but **not** by the wall. Measured: core's `bulk_update_delete` auth requires
    `has_user_permission_for_group_or_org(org_id, user, 'update')`, which the
    `editor` role does not carry (`ckan/authz.py:360`), so core denies before
    `_bulk_update_dataset` runs and the wall's chain is never reached. The state
    stays `active` either way."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["editor"], "bulk_update_delete",
                org_id=scene["org"]["id"], datasets=[scene["dataset"]["id"]])
    assert umss_auth.PUBLISH_DENIED_MSG not in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["state"] == "active"


def test_admin_bulk_update_delete_is_refused_by_the_wall(scene):
    """The caller A2's bypass actually named is the admin: the `admin` role
    satisfies every permission, so it passes core's `bulk_update_delete` auth,
    and the wall then refuses the `state` change the inner `package_patch`
    carries, with the flow message."""
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["admin"], "bulk_update_delete",
                org_id=scene["org"]["id"], datasets=[scene["dataset"]["id"]])
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert stored(scene["dataset"]["id"])["state"] == "active"


def test_admin_bulk_update_private_is_still_allowed(scene):
    """Narrowing visibility is not a publication: the admin passes core's
    `bulk_update_private` auth, and the wall's `package_update` chain permits the
    `private: True` the inner `package_patch` carries."""
    public = factories.Dataset(owner_org=scene["org"]["id"], private=False)
    call_as(scene["admin"], "bulk_update_private",
            org_id=scene["org"]["id"], datasets=[public["id"]])
    assert stored(public["id"])["private"] is True


def test_editor_bulk_update_private_is_refused_by_core_not_the_wall(scene):
    """The same outer-auth asymmetry as delete: an `editor` lacks the `update`
    permission `bulk_update_private` requires, so core refuses before the body
    and the wall never sees it. The editor is not the caller the inventory's
    'still allowed' reading holds for; the admin is."""
    public = factories.Dataset(owner_org=scene["org"]["id"], private=False)
    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(scene["editor"], "bulk_update_private",
                org_id=scene["org"]["id"], datasets=[public["id"]])
    assert umss_auth.PUBLISH_DENIED_MSG not in str(excinfo.value)
    assert stored(public["id"])["private"] is False


# ---------------------------------------------------------------------------
# S5 row 9 — what a partial update hands the chained rule (measured 2026-09-29)
# ---------------------------------------------------------------------------


@pytest.fixture
def handed_to_the_rule(monkeypatch):
    """The `data_dict` CKAN passes to the registered `package_update` chain.

    The *registry* has to be wrapped, not the module attribute:
    `chained_auth_function` registers a `functools.partial` bound to core's
    decision (`ckan/authz.py`, the `partial(func, prev_func)` build), so
    replacing `umss_auth.package_update` would intercept nothing. `AuthFunctions`
    exposes only `get`/`clear`/`keys`, so what gets patched is the class-level
    dict its own `get` reads; `monkeypatch` puts the original back at teardown,
    and `get` is called first so the cache is built before it is read.

    A context carrying `ignore_auth` returns before the chain even for a
    sysadmin — which is why the fixture's own `helpers.call_action` writes never
    touch this spy. With `auth_sysadmins_check` on the chain, a plain sysadmin
    request does reach it.
    """
    original = ckan.authz._AuthFunctions.get("package_update")
    seen = {}

    def spy(context, data_dict):
        seen["data_dict"] = dict(data_dict)
        return original(context, data_dict)

    monkeypatch.setitem(
        ckan.authz._AuthFunctions._functions, "package_update", spy
    )
    return seen


def test_an_update_without_resources_keeps_them_and_hands_the_rule_none(
    handed_to_the_rule,
):
    """CKAN 2.12, measured: the wizard's edit shape is safe, and why it is safe.

    S5 row 9 of the upgrade carried an assumption that a rule chained onto
    `package_update` sees *every* resource. On 2.12 the flattened data omits the
    unchanged ones (#5713) and `allow_partial_update` is gone, so an edit that
    omits `resources` no longer deletes them either. Both halves matter to the
    portal's wizard, whose payload is the package dict with the resource list
    dropped (`src/routes/dashboard/datasets/new/+page.svelte`).

    Measured here rather than assumed: the chained function is handed the
    **request payload**, so a rule that reads `private`/`state` — this one reads
    only that — cannot depend on which resources the client sent. If a future
    CKAN starts flattening stored resources into the `data_dict` an auth function
    receives, the first assertion below is where it shows up; if one starts
    deleting omitted resources, the last one is.
    """
    admin = factories.User()
    org = factories.Organization()
    add_user_member(org["id"], admin["id"], "admin")
    dataset = factories.Dataset(owner_org=org["id"], private=True)
    for name in ("one", "two"):
        helpers.call_action(
            "resource_create",
            package_id=dataset["id"],
            name=name,
            url="https://example.invalid/%s.csv" % name,
        )
    before = stored(dataset["id"])
    assert len(before["resources"]) == 2

    # The wizard's shape: everything `package_show` answered, minus the resources.
    payload = {
        key: value
        for key, value in before.items()
        if key not in ("resources", "num_resources")
    }
    payload["title"] = "edited without sending resources"
    # The fixture's own writes cannot reach the spy (`ignore_auth` short-circuits
    # before the registry), but clearing makes the assertion below about this call
    # alone rather than about whatever ran last.
    handed_to_the_rule.clear()

    call_as(admin, "package_update", **payload)

    handed = handed_to_the_rule["data_dict"]
    assert "resources" not in handed
    after = stored(dataset["id"])
    assert after["title"] == "edited without sending resources"
    assert [resource["name"] for resource in after["resources"]] == ["one", "two"]


def test_admin_of_a_parent_org_is_refused_and_gets_the_flow_message():
    """The cascade in `Approver Capacity` is measured (design P10), so it is a
    test rather than an assumption: `has_user_permission_for_group_or_org` walks
    `get_parent_group_hierarchy` for the capacities in
    `ckan.auth.roles_that_cascade_to_sub_groups` (`admin`).

    Under the wall the cascade no longer grants a publish; it grants the
    approver identity, so the parent admin is refused with the administrator's
    flow message rather than the non-administrator's role message.
    """
    parent = factories.Organization()
    child = factories.Organization()
    admin = factories.User()
    add_user_member(parent["id"], admin["id"], "admin")
    # The hierarchy row is inverted from the intuitive reading: the child org is
    # the member row's `id` and the parent is `object`. The other direction
    # answers 200 and registers nothing the cascade reader ever sees.
    helpers.call_action("member_create", id=child["id"], object=parent["id"],
                        object_type="group", capacity="parent")
    dataset = factories.Dataset(owner_org=child["id"], private=True)

    with pytest.raises(logic.NotAuthorized) as excinfo:
        call_as(admin, "package_patch", id=dataset["id"], private=False)
    assert umss_auth.PUBLISH_VIA_FLOW_MSG in str(excinfo.value)
    assert stored(dataset["id"])["private"] is True
