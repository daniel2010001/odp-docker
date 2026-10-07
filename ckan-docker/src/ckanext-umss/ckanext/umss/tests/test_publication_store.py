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
The side effect is that D2's index, and the CheckConstraints that carry the two
declared domains, are proven **as the migration wrote them**: a migration that
forgot the partial index would fail
`test_a_settled_row_does_not_block_a_new_pending_one`, and one whose status set
disagreed with the model's `STATUSES` would fail either
`test_every_declared_outcome_is_storable` or
`test_the_migration_rejects_a_status_outside_the_declared_set`.
"""
import datetime
import re
import uuid

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

# The columns D2 declares as mandatory — the ones the migration writes with
# `nullable=False`. `id` is the primary key and carries a default instead, so it
# is absent here on purpose.
NOT_NULL_COLUMNS = (
    "dataset_id",
    "requested_visibility",
    "status",
    "requested_by",
    "created_at",
)


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


@pytest.mark.parametrize("status", umss_model.STATUSES)
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
    # needs after the first one is settled. The count comes from the rows this
    # test inserts, not from `STATUSES`: `pending` is not among them.
    settled = ("approved", "approved", "rejected", "cancelled", "annulled")
    save(*(make_request(dataset_id="dataset-1", status=s) for s in settled))

    assert len(read_back()) == len(settled)


def test_a_settled_row_does_not_block_a_new_pending_one(store):
    for status in ("approved", "rejected", "cancelled", "annulled"):
        save(make_request(dataset_id="dataset-1", status=status))

    save(make_request(dataset_id="dataset-1", status="pending"))

    pending = [row for row in read_back() if row.status == "pending"]
    assert len(pending) == 1


# ---------------------------------------------------------------------------
# A1.5 — the constraints the *migration* wrote, not the ones the model declares
# ---------------------------------------------------------------------------


def insert_raw(**overrides):
    """Insert one row with raw SQL.

    Raw, not through the model: a Python-side default fills a column an ORM
    insert leaves out, and that is exactly what these tests must not allow.
    """
    row = {
        "id": str(uuid.uuid4()),
        "dataset_id": "dataset-1",
        "requested_visibility": "public",
        "status": "pending",
        "requested_by": "requester-1",
        "created_at": datetime.datetime(2026, 10, 7, 12, 0, 0),
    }
    row.update(overrides)
    ckan_model.Session.execute(
        sa.text(
            "insert into publication_requests ({columns}) values ({values})".format(
                columns=", ".join(row),
                values=", ".join(":" + name for name in row),
            )
        ),
        row,
    )


def refused_by_the_database(**overrides):
    with pytest.raises(sa.exc.IntegrityError):
        insert_raw(**overrides)
    ckan_model.Session.rollback()


@pytest.mark.parametrize("column", NOT_NULL_COLUMNS)
def test_the_migration_rejects_null_in_a_mandatory_column(store, column):
    refused_by_the_database(**{column: None})


def test_the_migration_rejects_a_status_outside_the_declared_set(store):
    # The behavioural half only. Rejection cannot pin the set: a constraint
    # *wider* than the model's declaration would still refuse this value, so the
    # set itself is pinned by `test_the_migration_pins_status_to_the_declared_set`
    # below, which reads the constraint out of the catalog.
    refused_by_the_database(status="banana")


def test_the_migration_rejects_a_visibility_outside_the_declared_set(store):
    refused_by_the_database(requested_visibility="maybe")


def constraint_values(name):
    """The value set a live CheckConstraint allows, read from the catalog.

    Postgres renders `x IN ('a', 'b')` as `x = ANY (ARRAY['a'::text,
    'b'::text])`, so the quoted values are what there is to read. This is the
    only way to pin the *set*: probing a value can prove it is refused, never
    that no extra value is allowed. The regex is tied to that rendering, which
    is why it is asserted against `pg_get_constraintdef` rather than assumed.
    """
    definition = ckan_model.Session.execute(
        sa.text(
            "select pg_get_constraintdef(oid) from pg_constraint"
            " where conname = :name"
            " and conrelid = 'publication_requests'::regclass"
        ),
        {"name": name},
    ).scalar()
    assert definition, "no such constraint: {}".format(name)
    return set(re.findall(r"'([a-z_]+)'::text", definition))


def test_the_migration_pins_status_to_the_declared_set(store):
    assert constraint_values("ck_publication_requests_status") == set(
        umss_model.STATUSES
    )


def test_the_migration_pins_visibility_to_the_declared_set(store):
    assert constraint_values("ck_publication_requests_visibility") == set(
        umss_model.VISIBILITIES
    )


def test_the_migration_can_be_reversed(store, migrate_db_for):
    migrate_db_for("umss", "base", forward=False)
    assert not sa.inspect(ckan_model.Session.bind).has_table("publication_requests")

    # Re-applied on purpose: `clean_db` will not rebuild it for the tests that
    # follow in this session, because CKAN's `init_db` replays the core
    # migrations only.
    migrate_db_for("umss")
    assert sa.inspect(ckan_model.Session.bind).has_table("publication_requests")
