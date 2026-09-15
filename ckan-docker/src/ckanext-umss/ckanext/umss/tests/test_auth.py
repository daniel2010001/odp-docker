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
from ckan.tests import factories, helpers

from ckanext.umss import auth as umss_auth


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


# ---------------------------------------------------------------------------
# 1.2.1 — the update path: an editor must not publish, and must not change state
# ---------------------------------------------------------------------------


def test_the_guard_is_registered_for_both_action_names():
    """The plugin must own the chained functions, not merely define them."""
    assert umss_auth.package_update.chained_auth_function is True
    assert umss_auth.package_create.chained_auth_function is True
    for action in ("package_update", "package_create"):
        registered = ckan.authz._AuthFunctions.get(action)
        assert getattr(registered, "chained_auth_function", False) is True, action


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


def test_sysadmin_publishes(scene):
    """CKAN short-circuits sysadmins before any auth function; the guard must
    not disable that by flagging itself `auth_sysadmins_check`."""
    call_as(factories.Sysadmin(), "package_patch",
            id=scene["dataset"]["id"], private=False)
    assert stored(scene["dataset"]["id"])["private"] is False


def test_org_admin_publishes(scene):
    call_as(scene["admin"], "package_patch",
            id=scene["dataset"]["id"], private=False)
    assert stored(scene["dataset"]["id"])["private"] is False


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


def test_unresolvable_id_defers_to_core(scene):
    """Core raises its own `NotFound`; the guard must not convert it to a `403`."""
    with pytest.raises(logic.NotFound):
        call_as(scene["editor"], "package_patch",
                id="00000000-0000-0000-0000-000000000000", private=False)


def test_private_false_on_an_already_public_dataset_requests_no_transition(scene):
    """No diff is requested, so the guard has nothing to refuse."""
    call_as(scene["admin"], "package_patch",
            id=scene["dataset"]["id"], private=False)
    call_as(scene["editor"], "package_patch",
            id=scene["dataset"]["id"], private=False)
    assert stored(scene["dataset"]["id"])["private"] is False


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
    (`ckan/model/package.py:75`, `default=False`) applies.
    """
    created = factories.Dataset(owner_org=scene["org"]["id"])
    assert created["private"] is False
    assert stored(created["id"])["private"] is False


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


def test_admin_of_a_parent_org_publishes_a_child_org_dataset():
    """The cascade in `Approver Capacity` is measured (design P10), so it is a
    test rather than an assumption: `has_user_permission_for_group_or_org` walks
    `get_parent_group_hierarchy` for the capacities in
    `ckan.auth.roles_that_cascade_to_sub_groups` (`admin`)."""
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

    call_as(admin, "package_patch", id=dataset["id"], private=False)
    assert stored(dataset["id"])["private"] is False
