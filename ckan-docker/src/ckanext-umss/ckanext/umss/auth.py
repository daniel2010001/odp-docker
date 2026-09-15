"""Publication-lifecycle authorization guard.

A dataset becomes public when its stored ``private`` value flips to ``False``,
and only an organization ``admin`` (or a ``sysadmin``) may flip it. This module
enforces that inside CKAN's authorization layer, so the refusal happens before
validation and before persistence, and it applies to the API, CKAN's own web UI
and the portal alike.

The two functions below are *chained* onto core (`toolkit.chained_auth_function`)
rather than replacing it. Core's ``package_update`` auth is not trivial — owner-org
capacity, the unowned-dataset config path, optional collaborator fallback and
``_check_group_auth`` — and re-implementing it would mean re-implementing its
bugs. Chaining runs the core decision first and adds one predicate on top.

What that covers, because three actions delegate to ``package_update``:
``package_patch``, ``package_change_state`` and ``package_delete`` all authorize
by calling it, and ``package_revise`` passes the fully revised dict, which
carries ``id``.

Three properties keep this safe rather than clever:

1. **A missing key means "no request".** ``package_delete``, ``resource_create``
   and metadata-only edits pass ``private``/``state`` absent or unchanged, so
   they keep working. Only an explicit, interpretable diff is refused.
2. **Unresolvable packages defer.** If the stored row cannot be loaded, the core
   result is returned. That is not a hole: the core path also needs the package
   to write anything, so a request this module cannot resolve cannot publish
   either.
3. **Values core cannot interpret defer, but only where deferring is safe.**
   ``boolean_validator`` is a *total* function (see `_as_bool`), so a ``private``
   value is never deferred on the strength of "core will validate it" — core
   would coerce it to public instead. Values that make core raise are deferred.

``state`` is deliberately **not** guarded at create time: CKAN silently drops it
for every non-sysadmin, so a ``403`` there would deny a request CKAN was never
going to honour.

Sysadmin access is preserved for free. ``authz.is_authorized`` returns success
for a sysadmin *before* calling any registered auth function, unless that
function carries ``auth_sysadmins_check``. Neither function here sets that flag.

``auth_allow_anonymous_access`` is declared for a subtler reason: CKAN builds a
chained function as ``functools.partial(func, prev_func)`` and copies only
``func``'s attributes onto it, so core's own ``auth_allow_anonymous_access`` is
lost the moment the chain is installed. Without the flag, ``is_authorized``
would refuse every anonymous caller before consulting core — a behaviour change
this change did not ask for, invisible under the default configuration (anonymous
dataset creation is off) and wrong under one that enables it. Declaring the flag
lets core keep deciding for anonymous callers, which is the pre-change behaviour
with the pre-change message.
"""
from typing import Any, Optional

import ckan.authz as authz
import ckan.model as model
import ckan.plugins.toolkit as toolkit

PUBLISH_DENIED_MSG = toolkit._(
    'Only an organization administrator can publish a dataset'
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


def _as_bool(value: Any) -> Optional[bool]:
    """The value CKAN will store for a requested ``private``.

    This mirrors ``ckan.logic.validators.boolean_validator`` exactly, because the
    guard's decision has to agree with what persistence will do afterwards.
    That validator is total: it returns ``False`` for ``None``, for the empty
    string and for every string outside ``{'true','yes','t','y','1'}``, and it
    only raises for values without a ``.lower()``. So ``'banana'`` does not
    produce a validation error — it produces a **public** dataset, and the guard
    has to treat it as a publication attempt.

    Returns ``None`` for the values core would raise on. Those are safe to defer:
    the exception aborts the request, so nothing is written.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in _BOOLEAN_TRUE
    return None


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
    """Refuse a visibility or state transition requested by a non-approver."""
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
        return result
    return {"success": False, "msg": PUBLISH_DENIED_MSG}


@toolkit.chained_auth_function
@toolkit.auth_allow_anonymous_access
def package_create(next_auth, context, data_dict):
    """Refuse a dataset that would be stored public when the caller may not publish.

    At **create** time an absent ``private`` is *not* "keep what is stored":
    ``private`` sits in the schema's ``ignore_missing`` chain
    (``ckan/logic/schema.py:160-161``), so an omitted key falls through to the
    column default, and ``Column('private', types.Boolean, default=False)``
    (``ckan/model/package.py:75``) makes that default **public**. An omitted key
    is therefore a publication attempt, exactly like an explicit ``False``. Only
    an explicit value that core reads as ``True`` may defer here. This asymmetry
    with ``package_update`` — where omission leaves the stored value untouched —
    is why create needs its own shape instead of reusing the update one.
    """
    result = next_auth(context, data_dict)
    if not result.get("success"):
        return result

    owner_org = data_dict.get("owner_org")
    if not owner_org:
        # An unowned dataset cannot be private, so no publication is at stake.
        return result

    if "private" in data_dict:
        wanted_private = _as_bool(data_dict["private"])
        if wanted_private is True:
            return result
        if wanted_private is None:
            # Core's own validator raises on this value; nothing gets created.
            return result
    # An absent key, or any value core reads as public, is a publication attempt.

    if _is_approver(context, owner_org):
        return result
    return {"success": False, "msg": PUBLISH_DENIED_MSG}
