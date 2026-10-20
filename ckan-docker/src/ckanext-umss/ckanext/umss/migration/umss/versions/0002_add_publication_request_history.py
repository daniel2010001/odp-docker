# -*- coding: utf-8 -*-

"""add the request history columns to publication_requests

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20

The portal shows every request of a dataset with when it was made, when it was
resolved, by whom, and why. 0001 could not answer two of those: one `comments`
column held the requester's note until a decider overwrote it, and nothing
recorded who cancelled or annulled a request. Three nullable columns close
that, additively — `comments` keeps its meaning for every existing consumer:

* `request_comment`: the requester's note, as `publication_request_create`
  received it.
* `decision_comment`: the decider's comment, as `publication_request_decide`
  received it.
* `resolved_by`: the user id that took the row out of `pending`, whatever the
  outcome (the decider, the canceller, or whoever deleted the dataset).

The backfill writes only what a 0001 row can attribute. No decision ran on a
`pending`, `cancelled` or `annulled` row, so its `comments` is the requester's.
A rejection carries the decider's comment (required since 2026-10-07,
b98d823), so a `rejected` row's `comments` is the decider's; one made on
2026-10-06 without a comment would be misfiled, and no store measured holds one. An `approved` row's `comments` is either (an
approval without a comment keeps the requester's note), so it is not copied.
`approved_by` is the decider of both decided outcomes. Who cancelled or
annulled an old row was never stored, and stays unknown.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = u"0002"
down_revision = u"0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        u"publication_requests", sa.Column(u"request_comment", sa.UnicodeText)
    )
    op.add_column(
        u"publication_requests", sa.Column(u"decision_comment", sa.UnicodeText)
    )
    op.add_column(u"publication_requests", sa.Column(u"resolved_by", sa.UnicodeText))
    # Status literals written out rather than imported, like 0001's constraint:
    # a migration must not change meaning when the model moves.
    op.execute(
        u"update publication_requests set request_comment = comments"
        u" where status in ('pending', 'cancelled', 'annulled')"
    )
    op.execute(
        u"update publication_requests set decision_comment = comments"
        u" where status = 'rejected'"
    )
    op.execute(
        u"update publication_requests set resolved_by = approved_by"
        u" where status in ('approved', 'rejected')"
    )


def downgrade():
    # `comments` and `approved_by` still hold everything 0001 knew, so dropping
    # the three columns loses only what was recorded after 0002.
    op.drop_column(u"publication_requests", u"resolved_by")
    op.drop_column(u"publication_requests", u"decision_comment")
    op.drop_column(u"publication_requests", u"request_comment")
