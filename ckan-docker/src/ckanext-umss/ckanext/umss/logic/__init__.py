"""
Shared pieces of this extension's logic layer.
"""
from __future__ import annotations

import ckan.model as model


__all__ = ["caller_id"]


def caller_id(context):
    """The id of the user a context speaks for, or `None`.

    One copy on purpose: the actions attribute rows to this id and the auth
    functions decide by it, so two copies under two names would drift (A2's
    review, R2-004).
    """
    user = context.get("auth_user_obj") or model.User.get(context.get("user"))
    return user.id if user else None
