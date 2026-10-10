"""Publication-lifecycle authorization guard.

A dataset becomes public when its stored ``private`` value flips to ``False``,
and **no caller** may flip it here: the publication flow is the only route, and
its own flip writes with ``ignore_auth``. This module enforces that inside
CKAN's authorization layer, so the refusal happens before validation and before
persistence, and it applies to the API, CKAN's own web UI and the portal alike.

What this wall does **not** leave open: it refuses the publication transition
for every caller, the ``sysadmin`` included. On the ``package_update`` and
``package_create`` chains, core itself refuses ``member``,
cross-organization ``editor`` and anonymous callers before the chain runs, so
there the wall is the refuser only for the callers core admits.
``bulk_update_public`` is the exception: it does not consult core, so it refuses
every caller itself. The organization ``admin`` is the approver the publication
flow authorizes (``publication_request_decide``), and a ``sysadmin`` administers
the instance, but no update path publishes — for either of them — so the refusal
carries a **second**, distinct message (``PUBLISH_VIA_FLOW_MSG``) when the caller
is one the flow authorizes (a ``sysadmin`` or an organization ``admin``), and
``PUBLISH_DENIED_MSG`` otherwise. ``package_create`` is private for everyone (the
omitted key is the same publish attempt as ``false``), and ``bulk_update_public``
is covered by a chained refusal of its own: measured on the running CKAN 2.12.0
it loops ``package_patch`` and therefore **does** reach ``package_update``, but
the chain refuses at the action's own auth, before the body runs, with a message
of ours.

The ``sysadmin`` is not an exception to the publication rule. All three functions
carry ``auth_sysadmins_check``, so ``authz.is_authorized`` calls them for a
``sysadmin`` instead of short-circuiting to success, and the publication
transition is refused for them too. The one capability this leaves the sysadmin
is ``state`` administration, which is not publishing: ``package_update`` refuses
a ``state`` change only below a sysadmin, so ``bulk_update_delete`` and a
``package_patch {state: ...}`` remain the sysadmin's.

The functions below are *chained* onto core (`toolkit.chained_auth_function`)
rather than replacing it. Core's ``package_update`` auth is not trivial — owner-org
capacity, the unowned-dataset config path, optional collaborator fallback and
``_check_group_auth`` — and re-implementing it would mean re-implementing its
bugs. Chaining runs the core decision first and adds one predicate on top. The
``bulk_update_public`` chain is the one exception to that ordering, because no
caller may reach that action at all and attributing the
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
(``ckan/logic/validators.py``,
``logic.check_access('package_change_state',context, {"id": pkg.id})``); that
validator reaches this same chain, so nothing is lost by dropping the name as an
action.

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

Sysadmin access is deliberately narrowed, not preserved for free:
``authz.is_authorized`` returns success for a sysadmin *before* calling any
registered auth function unless that function carries
``auth_sysadmins_check``, and all three functions here set that flag. So the wall
runs for a sysadmin and refuses the publication transition; the only sysadmin
capability it keeps is ``state`` administration.

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

#: The refusal **labels** are the frozen interface a consumer matches on:
#: every message below reads ``<label>: <sentence>``, the label is the part
#: that must not change, and the sentence after it is free prose. ``Publish
#: denied`` belongs to the wall's own ``PUBLISH_DENIED_MSG``; a caller the
#: publication flow authorizes reads ``Publication flow`` instead.
PUBLISH_DENIED_LABEL = "Publish denied"
PUBLICATION_FLOW_LABEL = "Publication flow"

#: The label stays **outside** the translatable unit: what a consumer matches
#: must not move with the language, while the sentence after it is what a human
#: reads and is the part `toolkit._` is applied to. Composing the two outside the
#: call also keeps the translator's message the sentence itself: with `_` as an
#: extraction keyword, `_("%s: %s" % (label, sentence))` extracts the template
#: `"%s: %s"`, which the runtime lookup can never match — the *composed* string is
#: what reaches the translator.
PUBLISH_DENIED_MSG = "%s: %s" % (
    PUBLISH_DENIED_LABEL,
    toolkit._("only an organization administrator can decide a publication request"),
)

#: The message a caller the publication flow authorizes gets from the wall.
#: That caller is a ``sysadmin`` or the one who holds the ``admin`` capacity (or
#: cascades to it through a parent organization), so the refusal names the flow
#: rather than the role the caller already holds. It is deliberately a second
#: constant: the spec's `Distinguishable Authorization Errors` requires the two
#: messages to be distinguishable, and a consumer that reads only one of them
#: must not conflate the two callers.
PUBLISH_VIA_FLOW_MSG = "%s: %s" % (
    PUBLICATION_FLOW_LABEL,
    toolkit._("publication goes through the publication flow, not package_patch"),
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


def _is_flow_caller(context, owner_org) -> bool:
    """True when the caller is one the publication flow authorizes.

    That is a ``sysadmin`` (the instance's administrator) or an org ``admin``
    for ``owner_org`` (`_is_approver`). The refusal message is chosen by this
    fact, not by role: ``PUBLISH_VIA_FLOW_MSG`` tells a caller the flow
    authorizes that the flow is the door, and ``PUBLISH_DENIED_MSG`` tells
    everyone else they are not one.
    """
    if authz.is_sysadmin(context.get("user")):
        return True
    return _is_approver(context, owner_org)


def _refusal(context, owner_org):
    """The refusal dict for ``context``, with its message chosen by fact."""
    if _is_flow_caller(context, owner_org):
        return {"success": False, "msg": PUBLISH_VIA_FLOW_MSG}
    return {"success": False, "msg": PUBLISH_DENIED_MSG}


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
@toolkit.auth_sysadmins_check
@toolkit.auth_allow_anonymous_access
def package_update(next_auth, context, data_dict):
    """Refuse a publication transition for every caller; refuse a state
    transition only below a sysadmin.

    The two are separated on purpose. A publication is a ``private`` flip to
    ``False``, and no caller — the sysadmin included — may make it here: the
    flow is the only route, so the wall runs for the sysadmin too. A ``state``
    change is not a publication, so it stays the sysadmin's exactly as it was:
    administering ``state`` (``bulk_update_delete``, for instance) must not be
    closed by accident.
    """
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

    if wants_private_public:
        # Publication: refused for every caller, the sysadmin included.
        return _refusal(context, pkg.owner_org)
    if wants_state_change and not authz.is_sysadmin(context.get("user")):
        # `state` administration is not publishing: refused below a sysadmin,
        # preserved for the sysadmin.
        return _refusal(context, pkg.owner_org)
    return result


@toolkit.chained_auth_function
@toolkit.auth_sysadmins_check
@toolkit.auth_allow_anonymous_access
def package_create(next_auth, context, data_dict):
    """Refuse a dataset that would be stored public.

    At **create** time an absent ``private`` is *not* "keep what is stored":
    ``private`` sits in the schema's ``ignore_missing`` chain
    (``ckan/logic/schema/__init__.py``,
    ``'private': [ignore_missing, boolean_validator,``), so an omitted key falls
    through to the column default, and ``Column('private', types.Boolean,
    default=False)`` (``ckan/model/package.py``) makes that default **public**.
    An omitted key
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
    # Creation is private for everyone, the sysadmin included.
    return _refusal(context, owner_org)


@toolkit.chained_auth_function
@toolkit.auth_sysadmins_check
def bulk_update_public(next_auth, context, data_dict):
    """Refuse the bulk publication door for every caller.

    Measured on the running CKAN 2.12.0 (``/srv/app/src/ckan``, commit
    ``0058b2eb``), ``_bulk_update_dataset`` loops ``_get_action('package_patch')``
    (``ckan/logic/action/update.py``, ``_get_action('package_patch')(``)
    and therefore **does** reach
    ``package_update``. An earlier version of this docstring claimed the
    opposite; it was reading a stale CKAN ``2.12.0a0`` checkout whose loop ran a
    direct ``model.Session.query(...).update(...)``.

    The chain still belongs on this action's own auth, and the correction does
    not change that: it refuses at this action's own auth check, before
    ``_bulk_update_dataset`` runs, so no dataset is patched and the caller gets
    an attributable message of ours rather than core's. It also holds if that
    inner loop ever changes back to a direct write, which the ``package_update``
    chain alone would not see. Core's own auth for this action is a *separate*
    function (``ckan/logic/auth/update.py``,
    ``authorized = authz.has_user_permission_for_group_or_org(``) that checks
    only ``has_user_permission_for_group_or_org(org_id, user, 'update')`` — a
    permission the ``editor`` role does not carry but the ``admin`` role does.

    No caller may use this action: it is a publication path
    with no record and no diff to inspect, so the refusal does not depend on
    core's answer. ``next_auth`` is therefore deliberately not called — core
    refuses an ``editor`` with an empty message, and the plugin's own message is
    what makes the refusal distinguishable and attributable to this rule. The
    message is chosen by fact, and on this route the fact is the payload's
    ``org_id``: an org ``admin`` of that organization and a ``sysadmin`` get
    ``PUBLISH_VIA_FLOW_MSG``; everyone else gets ``PUBLISH_DENIED_MSG``.

    There is no ``auth_allow_anonymous_access`` here on purpose: core's own
    ``bulk_update_public`` does not carry the flag either (unlike
    ``package_update``/``package_create``, whose flag the chain would otherwise
    drop), so re-declaring it would *change* core's anonymous behaviour instead
    of preserving it.
    """
    return _refusal(context, data_dict.get("org_id"))
