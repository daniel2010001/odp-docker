"""
Tests for the publication-request store (`ckanext.umss.model`).

Design reference: `design.md` D2 (the store) and D3 (the migration). These tests
prove the store **as the database holds it**: they write through the model,
commit, and re-read after dropping the identity map.

They also run against the table the **migration** created, not one built from
the model metadata. That is not a preference — it is the only way it works.
Measured: `clean_db` reaches `model.repo.rebuild_db()`, and CKAN's `init_db`
replays the *core* migrations only (`ckan/model/__init__.py:212-220,362-387`);
`delete_all` explicitly tolerates a missing extension table ("if custom model
imported without migrations applied, corresponding table can be missing from
DB", `ckan/model/__init__.py:285-292`). So an extension's table exists in tests
only because `migrate_db_for` applies its own migration tree — CKAN's own
fixture for this, and the same mechanism production's `ckan db upgrade` uses.
The side effect is that D2's index is proven **as the migration wrote it**: a
migration that forgot the partial index would fail
`test_a_settled_row_does_not_block_a_new_pending_one`.
"""
import datetime

import pytest
import sqlalchemy as sa

import ckan.model as ckan_model

from ckanext.umss import model as umss_model


pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss"),
    pytest.mark.usefixtures("with_plugins"),
]


# The columns D2 declares, in the order the design lists them.
DECLARED_COLUMNS = (
    "id",
    "dataset_id",
    "requested_visibility",
    "status",
    "requested_by",
    "approved_by",
    "comments",
    "motive",
    "created_at",
    "decided_at",
    "consumed_at",
)

# Every outcome D2 declares, including the `annulled` one the mining added to
# the PRD schema.
STATUSES = ("pending", "approved", "rejected", "cancelled", "annulled")


@pytest.fixture
def store(clean_db, migrate_db_for):
    """The store table, built by this extension's own migration.

    `clean_db` comes first on purpose: it drops the schema, and whatever it
    drops has to be rebuilt afterwards, not before.
    """
    migrate_db_for("umss")


def make_request(**overrides):
    """A row with only the fields D2 makes mandatory, plus the overrides."""
    values = {
        "dataset_id": "dataset-1",
        "requested_visibility": "public",
        "status": "pending",
        "requested_by": "requester-1",
    }
    values.update(overrides)
    return umss_model.PublicationRequest(**values)


def save(*rows):
    """Write rows and read them back the way a later request would."""
    for row in rows:
        ckan_model.Session.add(row)
    ckan_model.Session.commit()
    ckan_model.Session.expire_all()


def read_back():
    return ckan_model.Session.query(umss_model.PublicationRequest).all()


def test_the_store_declares_the_table():
    # `__tablename__` is absent when a model is declared with `__table__` (it is
    # absent on CKAN's own `Activity` too), so the table is the thing to assert.
    assert umss_model.PublicationRequest.__table__.name == "publication_requests"


def test_the_table_declares_exactly_the_columns_the_design_names():
    declared = {
        column.name for column in umss_model.PublicationRequest.__table__.columns
    }
    assert declared == set(DECLARED_COLUMNS)


def test_the_migration_created_the_table(store):
    assert sa.inspect(ckan_model.Session.bind).has_table("publication_requests")


def test_a_row_persists_every_declared_column(store):
    created = datetime.datetime(2026, 10, 7, 12, 0, 0)
    decided = datetime.datetime(2026, 10, 7, 13, 0, 0)
    consumed = datetime.datetime(2026, 10, 7, 14, 0, 0)

    save(
        make_request(
            dataset_id="dataset-42",
            requested_visibility="private",
            status="approved",
            requested_by="requester-1",
            approved_by="approver-1",
            comments="please publish",
            motive="RF-42",
            created_at=created,
            decided_at=decided,
            consumed_at=consumed,
        )
    )

    rows = read_back()
    assert len(rows) == 1
    row = rows[0]
    assert row.dataset_id == "dataset-42"
    assert row.requested_visibility == "private"
    assert row.status == "approved"
    assert row.requested_by == "requester-1"
    assert row.approved_by == "approver-1"
    assert row.comments == "please publish"
    assert row.motive == "RF-42"
    assert row.created_at == created
    assert row.decided_at == decided
    assert row.consumed_at == consumed


def test_a_pending_row_persists_with_only_the_mandatory_fields(store):
    save(make_request())

    row = read_back()[0]
    # `id` and `created_at` are the store's, not the caller's.
    assert row.id
    assert row.created_at is not None
    assert row.approved_by is None
    assert row.comments is None
    assert row.motive is None


def test_a_pending_row_carries_no_decision_and_no_consumption(store):
    save(make_request(status="pending"))

    row = read_back()[0]
    assert row.decided_at is None
    assert row.consumed_at is None


@pytest.mark.parametrize("status", STATUSES)
def test_every_declared_outcome_is_storable(store, status):
    save(make_request(status=status))

    row = read_back()[0]
    assert row.status == status


# ---------------------------------------------------------------------------
# A1.4 — D2's one-pending-per-dataset invariant, triangulated
# ---------------------------------------------------------------------------


def refused_on_commit():
    """Commit, expecting the database to refuse, and leave the session usable."""
    with pytest.raises(sa.exc.IntegrityError):
        ckan_model.Session.commit()
    ckan_model.Session.rollback()


def test_a_second_pending_row_for_the_same_dataset_is_refused(store):
    save(make_request(dataset_id="dataset-1", status="pending"))

    ckan_model.Session.add(make_request(dataset_id="dataset-1", status="pending"))
    refused_on_commit()

    assert len(read_back()) == 1


def test_a_pending_row_for_another_dataset_is_allowed(store):
    save(
        make_request(dataset_id="dataset-1", status="pending"),
        make_request(dataset_id="dataset-2", status="pending"),
    )

    assert {row.dataset_id for row in read_back()} == {"dataset-1", "dataset-2"}


def test_settled_rows_are_not_constrained_by_the_index(store):
    # The index is *partial*. A plain unique index on `dataset_id` would refuse
    # this, and it would also refuse the second request a dataset legitimately
    # needs after the first one is settled.
    save(
        make_request(dataset_id="dataset-1", status="approved"),
        make_request(dataset_id="dataset-1", status="approved"),
        make_request(dataset_id="dataset-1", status="rejected"),
        make_request(dataset_id="dataset-1", status="cancelled"),
        make_request(dataset_id="dataset-1", status="annulled"),
    )

    assert len(read_back()) == len(STATUSES)


def test_a_settled_row_does_not_block_a_new_pending_one(store):
    for status in ("approved", "rejected", "cancelled", "annulled"):
        save(make_request(dataset_id="dataset-1", status=status))

    save(make_request(dataset_id="dataset-1", status="pending"))

    pending = [row for row in read_back() if row.status == "pending"]
    assert len(pending) == 1
