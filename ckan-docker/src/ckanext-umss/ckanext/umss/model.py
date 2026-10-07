"""
The publication-request store: `design.md` D2.

One row per publication request, carrying the PRD schema
(`PRD.md:336`) plus the two things the mining found missing: the `annulled`
outcome and the `RF-42` motive.

Its one invariant is the partial unique index: **at most one `pending` request
per dataset**, while a dataset keeps any number of settled rows. The index is
partial on purpose — a plain unique index on `dataset_id` would forbid the
second request a dataset will legitimately need after the first one is decided.

The table is created by `migration/umss/versions/0001_add_publication_requests.py`,
in tests as well as in production. CKAN's harness does not build an extension's
table from model metadata: `clean_db` reaches `rebuild_db`, whose `init_db`
replays the *core* migrations only, and `delete_all` explicitly tolerates a
missing extension table. So the store tests apply this extension's own tree
through CKAN's `migrate_db_for` fixture, and they run against the table the
migration built — which is what keeps this model and that migration from
drifting apart.
"""
from __future__ import annotations

import datetime

import sqlalchemy as sa

import ckan.model.domain_object as domain_object
import ckan.model.types as _types
from ckan.model.base import BaseModel


__all__ = [
    "PublicationRequest",
    "STATUSES",
    "VISIBILITIES",
    "MOTIVE_DATASET_DELETED",
    "MOTIVE_PUBLISHED_BY_ANOTHER_PATH",
]


# The five outcomes D2 declares. `annulled` is the mining's addition: it marks a
# request that a direct admin action made moot, and it is not `cancelled`
# because the requester did not withdraw it.
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
CANCELLED = "cancelled"
ANNULLED = "annulled"

STATUSES = (PENDING, APPROVED, REJECTED, CANCELLED, ANNULLED)

# The two visibilities a request may ask for (D2).
VISIBILITIES = ("public", "private")

# The two triggers that annul a pending request whose object is gone, in this
# cut (A2.7), as stable tokens: `motive` is the column that records why. English,
# lowercase, one token each, so the contract can name them and the portal does
# not have to guess.
MOTIVE_DATASET_DELETED = "dataset_deleted"
MOTIVE_PUBLISHED_BY_ANOTHER_PATH = "published_by_another_path"


def one_of(column, values):
    """The SQL of a membership constraint, derived from the declared values.

    Derived rather than hand-written so the constraint and the tuple it
    constrains cannot drift apart.
    """
    return "{} IN ({})".format(column, ", ".join("'{}'".format(v) for v in values))


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
        # The two declared domains, enforced by the database and not only by the
        # actions: a status or a visibility outside its set is a bug that should
        # fail loudly where the row is written. A1's review marked the absence of
        # the `status` one (R3-STATUS-UNENFORCED).
        sa.CheckConstraint(
            one_of("status", STATUSES), name="ck_publication_requests_status"
        ),
        sa.CheckConstraint(
            one_of("requested_visibility", VISIBILITIES),
            name="ck_publication_requests_visibility",
        ),
        # D2's invariant, as a partial index: one `pending` per dataset, and no
        # constraint at all on settled rows.
        sa.Index(
            "idx_publication_requests_one_pending_per_dataset",
            "dataset_id",
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        ),
    )
