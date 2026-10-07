"""Publication-lifecycle authorization guard.

A dataset becomes public when its stored ``private`` value flips to ``False``,
and only an organization ``admin`` (or a ``sysadmin``) may flip it. This module
enforces that inside CKAN's authorization layer, so the refusal happens before
validation and before persistence, and it applies to the API, CKAN's own web UI
and the portal alike.

What this wall does **not** leave open: for every caller core admits, it refuses
the transition — including the organization ``admin``. On the ``package_update``
and ``package_create`` chains, core itself refuses ``member``,
cross-organization ``editor`` and anonymous callers before the chain runs, so
there the wall is the refuser only for the callers core admits.
``bulk_update_public`` is the exception: it does not consult core, so it refuses
every authenticated non-sysadmin itself. The ``admin`` is the
approver the publication flow authorizes (``publication_request_decide``), but
no update path publishes — not even for them — so the refusal carries a
**second**, distinct message (``PUBLISH_VIA_FLOW_MSG``) that names the flow
instead of the role the caller already holds. ``package_create`` is private for
everyone (the omitted key is the same publish attempt as ``false``), and
``bulk_update_public`` is covered by a chained refusal of its own: measured on
the running CKAN 2.12.0 it loops ``package_patch`` and therefore **does** reach
``package_update``, but the chain refuses at the action's own auth, before the
body runs, with a message of ours.

The ``sysadmin`` is the one declared exception, and it is not special-cased
here: none of the functions here carries ``auth_sysadmins_check``, so
``authz.is_authorized`` returns success for a sysadmin *before* any rule below
runs — the emergency escape hatch the design keeps, with ``publication_publish``
as the recorded door.

The functions below are *chained* onto core (`toolkit.chained_auth_function`)
rather than replacing it. Core's ``package_update`` auth is not trivial — owner-org
capacity, the unowned-dataset config path, optional collaborator fallback and
``_check_group_auth`` — and re-implementing it would mean re-implementing its
bugs. Chaining runs the core decision first and adds one predicate on top. The
``bulk_update_public`` chain is the one exception to that ordering, because no
caller below a sysadmin may reach that action at all and attributing the
refusal to this module is the point.

What that covers. ``package_patch``, ``package_delete`` and ``package_revise``
authorize by delegating to ``package_update``'s auth (``package_revise`` passes
the fully revised dict, which carries ``id``), so the wall sees their requests.
A ``state`` change is covered wherever it is actually reachable: a full
``package_update``, ``package_patch`` carrying ``state``, and
``bulk_update_delete``, which loops ``package_patch`` with ``state='deleted'``.
``package_change_state`` is **not** an action in this CKAN — measured,
``t.get_action('package_change_state')`` raises ``KeyError: Action
'package_change_state' not found`` — so naming it as a covered action would be
naming a route no API caller can take. It survives only as an auth function this
CKAN references from the ``ignore_not_package_admin`` validator
(``ckan/logic/validators.py:560``); that validator reaches this same chain, so
nothing is lost by dropping the name as an action.

Three properties keep this safe rather than clever:

1. **A missing key means "no request".** ``package_delete``, ``resource_create``
   and metadata-only edits pass ``private``/``state`` absent or unchanged, so
   they keep working. Only an explicit, interpretable diff is refused.
2. **Unresolvable packages defer.** If the stored row cannot be loaded, the core
   result is returned. That is not a hole: the core path also needs the package
   to write anything, so a request this module cannot resolve cannot publish
   either.
3. **``private`` never defers to core's validation.** ``boolean_validator`` is a
   *total* function and never raises: it coerces a ``bool``/``int`` (so
   ``private = 0`` is a **public** dataset) and returns ``False`` for every value
   outside ``{'true','yes','t','y','1'}``. ``_as_bool`` mirrors it exactly, so no
   value defers — deferring would be deferring to the publication itself.

``state`` is deliberately **not** guarded at create time: CKAN silently drops it
for every non-sysadmin, so a ``403`` there would deny a request CKAN was never
going to honour.

Sysadmin access is preserved for free. ``authz.is_authorized`` returns success
for a sysadmin *before* calling any registered auth function, unless that
function carries ``auth_sysadmins_check``. None of the functions here sets that
flag.

``auth_allow_anonymous_access`` is declared for a subtler reason: CKAN builds a
chained function as ``functools.partial(func, prev_func)`` and copies only
``func``'s attributes onto it, so core's own ``auth_allow_anonymous_access`` is
lost the moment the chain is installed. Without the flag, ``is_authorized``
would refuse every anonymous caller before consulting core — a behaviour change
this change did not ask for, invisible under the default configuration (anonymous
dataset creation is off) and wrong under one that enables it. Declaring the flag
lets core keep deciding for anonymous callers, which is the pre-change behaviour
with the pre-change message. ``bulk_update_public`` deliberately does **not**
declare it: core's own function does not either (unlike those two), so
re-declaring it would *change* core's anonymous behaviour rather than preserve it.
"""
from typing import Any

import ckan.authz as authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit

PUBLISH_DENIED_MSG = toolkit._(
    'Only an organization administrator can publish a dataset'
)

#: The message an *approver* gets from the wall. The caller who holds the
#: ``admin`` capacity (or cascades to it through a parent organization) is the
#: one the publication flow authorizes, so the refusal has to name that flow
#: rather than the role the caller already holds. It is deliberately a second
#: constant: the spec's `Distinguishable Authorization Errors` requires the two
#: messages to be distinguishable, and a consumer that reads only one of them
#: must not conflate the two callers.
PUBLISH_VIA_FLOW_MSG = toolkit._(
    'Publication goes through the publication flow, not package_patch'
)

#: Exactly the strings `ckan.logic.validators.boolean_validator` reads as `True`.
#: The set must mirror core, not improve on it: a value this module reads as
#: `True` while core reads it as `False` would defer a publication, and the
#: reverse would refuse a request that changes nothing.
_BOOLEAN_TRUE = frozenset(("true", "yes", "t", "y", "1"))


def _is_approver(context, owner_org) -> bool:
    """True when the caller holds the ``admin`` capacity for ``owner_org``.

    ``has_user_permission_for_group_or_org`` is the same predicate CKAN's own
    ``package_update`` uses for ``update_dataset``, so it also brings the org
    hierarchy cascade for the capacities in
    ``ckan.auth.roles_that_cascade_to_sub_groups`` (``admin`` in the running
    configuration) and the sysadmin short-circuit. The portal reads this same
    predicate through ``organization_list_for_user {permission: "admin"}``
    instead of re-deriving roles.
    """
    if not owner_org:
        return False
    return authz.has_user_permission_for_group_or_org(
        owner_org, context.get("user"), "admin"
    )


def _as_bool(value: Any) -> bool:
    """The value CKAN will store for a requested ``private``.

    This mirrors ``ckan.logic.validators.boolean_validator`` exactly, because the
    guard's decision has to agree with what persistence will do afterwards. That
    validator is total and **never raises**: ``None`` and every value outside
    ``{'true','yes','t','y','1'}`` are ``False``, and a ``bool``/``int`` is
    coerced — ``bool(0)`` is ``False``, so ``private = 0`` is a **public**
    dataset. ``'banana'``, ``0.0``, ``[]`` and ``{}`` are therefore all
    publication attempts, and the guard refuses them rather than defer.
    """
    if value is None:
        return False
    if isinstance(value, (bool, int)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in _BOOLEAN_TRUE
    return False


def _load_or_defer(context, data_dict):
    """The stored package for ``data_dict``, or ``None`` when it cannot
    be resolved — in which case the caller must return the core result."""
    pkg_id = data_dict.get("id") or data_dict.get("name")
    if not pkg_id:
        return None
    try:
        return model.Package.get(pkg_id)
    except Exception:  # pragma: no cover - defensive: a broken lookup defers
        return None


@toolkit.chained_auth_function
@toolkit.auth_allow_anonymous_access
def package_update(next_auth, context, data_dict):
    """Refuse a visibility or state transition requested by any caller."""
    result = next_auth(context, data_dict)
    if not result.get("success"):
        return result
    if "private" not in data_dict and "state" not in data_dict:
        # Not a visibility request at all: `package_delete`, `resource_create`
        # and metadata-only edits all land here.
        return result

    pkg = _load_or_defer(context, data_dict)
    if pkg is None:
        return result

    if "private" in data_dict:
        wanted_private = _as_bool(data_dict["private"])
    else:
        wanted_private = bool(pkg.private)

    wants_private_public = wanted_private is False and bool(pkg.private)

    wanted_state = data_dict.get("state", pkg.state)
    wants_state_change = wanted_state is not None and wanted_state != pkg.state

    if not (wants_private_public or wants_state_change):
        return result
    if _is_approver(context, pkg.owner_org):
        # The approver is refused too; the message names the door so the two
        # callers are not conflated.
        return {"success": False, "msg": PUBLISH_VIA_FLOW_MSG}
    return {"success": False, "msg": PUBLISH_DENIED_MSG}


@toolkit.chained_auth_function
@toolkit.auth_allow_anonymous_access
def package_create(next_auth, context, data_dict):
    """Refuse a dataset that would be stored public.

    At **create** time an absent ``private`` is *not* "keep what is stored":
    ``private`` sits in the schema's ``ignore_missing`` chain
    (``ckan/logic/schema.py:160-161``), so an omitted key falls through to the
    column default, and ``Column('private', types.Boolean, default=False)``
    (``ckan/model/package.py:75``) makes that default **public**. An omitted key
    is therefore a publication attempt, exactly like an explicit ``False``. Only
    an explicit value that core reads as ``True`` may defer here — creation is
    private for **everyone**, the organization ``admin`` included, because the
    administrator publishes afterwards through the flow. Every other value is
    refused. This asymmetry with ``package_update`` — where omission leaves the
    stored value untouched — is why create needs its own shape instead of
    reusing the update one.
    """
    result = next_auth(context, data_dict)
    if not result.get("success"):
        return result

    owner_org = data_dict.get("owner_org")
    if not owner_org:
        # Deliberate deferral to core, not a declaration of safety: an unowned
        # dataset is outside every organization, so the approver predicate has
        # nothing to evaluate. It is unreachable today only because
        # `create_unowned_dataset` is false; if it is enabled, a public unowned
        # create is a real publication path, which is the config-gated gap the
        # contract names (PUBLICATION-ACTIONS.md, *Named bypasses*).
        return result

    if "private" in data_dict and _as_bool(data_dict["private"]) is True:
        return result
    # An absent key, or any value core reads as public, is a publication attempt.
    # Creation is private for everyone, the approver included: the administrator
    # publishes afterwards through the action, so the message names the flow.
    if _is_approver(context, owner_org):
        return {"success": False, "msg": PUBLISH_VIA_FLOW_MSG}
    return {"success": False, "msg": PUBLISH_DENIED_MSG}


@toolkit.chained_auth_function
def bulk_update_public(next_auth, context, data_dict):
    """Refuse the bulk publication door for every caller below a sysadmin.

    Measured on the running CKAN 2.12.0 (``/srv/app/src/ckan``, commit
    ``0058b2eb``), ``_bulk_update_dataset`` loops ``_get_action('package_patch')``
    (``ckan/logic/action/update.py:1200-1216``) and therefore **does** reach
    ``package_update``. An earlier version of this docstring claimed the
    opposite; it was reading a stale CKAN ``2.12.0a0`` checkout whose loop ran a
    direct ``model.Session.query(...).update(...)``.

    The chain still belongs on this action's own auth, and the correction does
    not change that: it refuses at this action's own auth check, before
    ``_bulk_update_dataset`` runs, so no dataset is patched and the caller gets
    an attributable message of ours rather than core's. It also holds if that
    inner loop ever changes back to a direct write, which the ``package_update``
    chain alone would not see. Core's own auth for this action is a *separate*
    function (``ckan/logic/auth/update.py:262-269``) that checks only
    ``has_user_permission_for_group_or_org(org_id, user, 'update')`` — a
    permission the ``editor`` role does not carry but the ``admin`` role does.

    No caller below a sysadmin may use this action: it is a publication path
    with no record and no diff to inspect, so the refusal does not depend on
    core's answer. ``next_auth`` is therefore deliberately not called — core
    refuses an ``editor`` with an empty message, and the plugin's own message is
    what makes the refusal distinguishable and attributable to this rule.

    There is no ``auth_allow_anonymous_access`` here on purpose: core's own
    ``bulk_update_public`` does not carry the flag either (unlike
    ``package_update``/``package_create``, whose flag the chain would otherwise
    drop), so re-declaring it would *change* core's anonymous behaviour instead
    of preserving it.
    """
    if _is_approver(context, data_dict.get("org_id")):
        return {"success": False, "msg": PUBLISH_VIA_FLOW_MSG}
    return {"success": False, "msg": PUBLISH_DENIED_MSG}
