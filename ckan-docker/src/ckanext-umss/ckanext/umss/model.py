"""
The publication-request store: `design.md` D2.

One row per publication request, carrying the PRD schema
(`PRD.md:336`) plus the two things the mining found missing: the `annulled`
outcome and the `RF-42` motive.

Its one invariant is the partial unique index: **at most one `pending` request
per dataset**, while a dataset keeps any number of settled rows. The index is
partial on purpose — a plain unique index on `dataset_id` would forbid the
second request a dataset will legitimately need after the first one is decided.

The table is created by `migration/umss/versions/0001_add_publication_requests.py`
outside tests (D3). Tests get it from this metadata through `clean_db`, which is
why the store tests cannot prove the migration ran.
"""
from __future__ import annotations

import datetime

import sqlalchemy as sa

import ckan.model.domain_object as domain_object
import ckan.model.types as _types
from ckan.model.base import BaseModel


__all__ = ["PublicationRequest", "STATUSES"]


# The five outcomes D2 declares. `annulled` is the mining's addition: it marks a
# request that a direct admin action made moot, and it is not `cancelled`
# because the requester did not withdraw it.
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
CANCELLED = "cancelled"
ANNULLED = "annulled"

STATUSES = (PENDING, APPROVED, REJECTED, CANCELLED, ANNULLED)


class PublicationRequest(domain_object.DomainObject, BaseModel):
    """A request to publish a dataset, and the record of who decided it."""

    __table__ = sa.Table(
        "publication_requests",
        BaseModel.metadata,
        sa.Column("id", sa.UnicodeText, primary_key=True, default=_types.make_uuid),
        sa.Column("dataset_id", sa.UnicodeText, nullable=False),
        sa.Column("requested_visibility", sa.UnicodeText, nullable=False),
        sa.Column("status", sa.UnicodeText, nullable=False),
        sa.Column("requested_by", sa.UnicodeText, nullable=False),
        sa.Column("approved_by", sa.UnicodeText),
        sa.Column("comments", sa.UnicodeText),
        sa.Column("motive", sa.UnicodeText),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            default=datetime.datetime.now,
        ),
        sa.Column("decided_at", sa.DateTime),
        sa.Column("consumed_at", sa.DateTime),
        # D2's invariant, as a partial index: one `pending` per dataset, and no
        # constraint at all on settled rows.
        sa.Index(
            "idx_publication_requests_one_pending_per_dataset",
            "dataset_id",
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        ),
    )
