# -*- coding: utf-8 -*-

"""create publication_requests

Revision ID: 0001
Revises:
Create Date: 2026-10-07

The DDL below mirrors `ckanext/umss/model.py` column for column, including the
partial unique index that carries D2's invariant: at most one `pending` request
per dataset. The model declares no server-side defaults — `id` and `created_at`
are Python-side — so neither does this script.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = u"0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        u"publication_requests",
        sa.Column(u"id", sa.UnicodeText, primary_key=True),
        sa.Column(u"dataset_id", sa.UnicodeText, nullable=False),
        sa.Column(u"requested_visibility", sa.UnicodeText, nullable=False),
        sa.Column(u"status", sa.UnicodeText, nullable=False),
        sa.Column(u"requested_by", sa.UnicodeText, nullable=False),
        sa.Column(u"approved_by", sa.UnicodeText),
        sa.Column(u"comments", sa.UnicodeText),
        sa.Column(u"motive", sa.UnicodeText),
        sa.Column(u"created_at", sa.DateTime, nullable=False),
        sa.Column(u"decided_at", sa.DateTime),
        sa.Column(u"consumed_at", sa.DateTime),
        # The declared domains, written out rather than imported from the model:
        # a migration is a statement about one point in time and must not change
        # meaning when the model moves. What keeps the two in step is the tests,
        # which insert every value the *model* declares and a value it does not
        # (`tests/test_publication_store.py`, A1.5).
        sa.CheckConstraint(
            u"status IN ('pending', 'approved', 'rejected', 'cancelled', 'annulled')",
            name=u"ck_publication_requests_status",
        ),
        sa.CheckConstraint(
            u"requested_visibility IN ('public', 'private')",
            name=u"ck_publication_requests_visibility",
        ),
    )
    op.create_index(
        u"idx_publication_requests_one_pending_per_dataset",
        u"publication_requests",
        [u"dataset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade():
    op.drop_index(
        u"idx_publication_requests_one_pending_per_dataset",
        table_name=u"publication_requests",
    )
    op.drop_table(u"publication_requests")
