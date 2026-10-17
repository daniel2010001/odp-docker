"""
`umss_chart_data`: the server-side grouping the `umss_chart` view reads.

Contract v1 (`odp/odd/tasks/csv-charts.md`, section "El contrato entre el portal
y `ckanext-umss` (versión 1)"), S7's scope, and the O2 design in
`odd/tasks/csv-charts-umss.md`. Two call forms, one answer shape:

* **visitor** — `{"view_id": "..."}` reads the saved configuration and needs the
  authorization `resource_view_show` needs;
* **editor** — `{"resource_id": "...", "config": {...}}` needs
  `resource_update`, so a draft can be previewed before anyone saves it.

Everything below runs against a real DataStore table built by
`datastore_create`: the promise is the SQL that comes out, and a mock would only
promise the SQL this file imagined.

Two notes on the fixtures, because both are easy to get wrong:

* `clean_datastore` is copied from `ckanext/datastore/tests/conftest.py`.
  `clean_db` empties CKAN's own database and never touches the DataStore's, so a
  table built by one test would otherwise survive into the next.
* `helpers.call_action` sets `ignore_auth`, which short-circuits
  `authz.is_authorized` before any auth function runs — this extension's
  `package_update` wall included. So the shape, bucket and aggregate tests use
  it (the SQL is what is under test there), and every authorization test uses
  `call_as`, which passes only the caller.
"""
import pytest
from psycopg2 import sql

import ckan.model as ckan_model
import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers

import ckanext.datastore.tests.helpers as test_helpers
from ckanext.datastore.backend.postgres import get_write_engine


pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss datastore"),
    pytest.mark.usefixtures("clean_db", "with_plugins"),
]


# Five rows that separate every case the contract names, with no accidental
# agreement between them:
#
#   turno    1, 1, 2, None, 3          a numeric column with a null
#   salario  10.5, 20, 30, 5, None     a second numeric column, null elsewhere
#   letra    a, b, b, c, None          text, no case folding to do
#   ciudad   La Paz, la paz, LA PAZ,   text, three spellings of one city
#            Cochabamba, None
#   nota     Mañana, " Mañana", "",    text, where L17's blank is not a value
#            "   ", None
#   fecha    2024-01-15, 2024-02-10,   timestamps spanning two years, three
#            2024-04-01, None,         quarters and four months
#            2025-07-04
FIELDS = [
    {"id": "turno", "type": "int4"},
    {"id": "salario", "type": "numeric"},
    {"id": "letra", "type": "text"},
    {"id": "ciudad", "type": "text"},
    {"id": "nota", "type": "text"},
    {"id": "fecha", "type": "timestamp"},
]

RECORDS = [
    {"turno": 1, "salario": 10.5, "letra": "a", "ciudad": "La Paz",
     "nota": "Mañana", "fecha": "2024-01-15"},
    {"turno": 1, "salario": 20, "letra": "b", "ciudad": "la paz",
     "nota": " Mañana", "fecha": "2024-02-10"},
    {"turno": 2, "salario": 30, "letra": "b", "ciudad": "LA PAZ",
     "nota": "", "fecha": "2024-04-01"},
    {"turno": None, "salario": 5, "letra": "c", "ciudad": "Cochabamba",
     "nota": "   ", "fecha": None},
    {"turno": 3, "salario": None, "letra": None, "ciudad": None,
     "nota": None, "fecha": "2025-07-04"},
]

# The smallest configuration the contract allows: `count` counts rows, so it
# carries no `y_field`, and `value` carries no `x_step`.
BASE = {
    "version": 1,
    "chart_type": "bar",
    "x_field": "letra",
    "x_bucket": "value",
    "y_agg": "count",
}

#: L18: the only keys `umss_chart_data` may answer a `ValidationError` on. A
#: consumer matches on the key, so an error on a key outside this set is a
#: contract break and not a detail — which is why one test below walks every
#: error the suite triggers and checks its key against this set.
ALLOWED_ERROR_KEYS = frozenset((
    "version",
    "chart_type",
    "x_field",
    "x_bucket",
    "x_step",
    "x_fold_case",
    "y_agg",
    "y_field",
    "include_null",
    "query",
    "view_id",
    "resource_id",
    "config",
))


@pytest.fixture
def clean_datastore(clean_db, clean_index):
    """Empty the DataStore database, the way
    `ckanext/datastore/tests/conftest.py` does.

    The table dictionary is a view over `pg_class`, so a table left behind is a
    resource that `datastore_info` still answers for.
    """
    engine = test_helpers.db.get_write_engine()
    test_helpers.rebuild_all_dbs(
        test_helpers.orm.scoped_session(
            test_helpers.orm.sessionmaker(bind=engine)
        )
    )


@pytest.fixture
def scene(clean_datastore):
    """One organization with an editor and a member, plus a **public** and a
    **private** dataset in it. The datasets are built with `ignore_auth`
    (`helpers.call_action`), so the public one is genuinely public: the
    publication wall would refuse to create it otherwise."""
    org = factories.Organization()
    editor = factories.User()
    member = factories.User()
    for user, capacity in ((editor, "editor"), (member, "member")):
        helpers.call_action(
            "member_create",
            id=org["id"],
            object=user["id"],
            object_type="user",
            capacity=capacity,
        )
    return {
        "org": org,
        "editor": editor,
        "member": member,
        "public": factories.Dataset(owner_org=org["id"], private=False),
        "private": factories.Dataset(owner_org=org["id"], private=True),
    }


def make_table(scene, fields=None, records=None, dataset="public"):
    """A DataStore table on a fresh resource of the scene's dataset, built by
    the action a publisher would use.

    The resource is created first and the table built onto it, the way CKAN's
    own datastore tests do it: the `resource={...}` shorthand makes the plugin
    answer `url_for('datastore.dump')` for the new resource, which needs a
    request this suite does not have.
    """
    resource = a_resource(scene, dataset=dataset)
    helpers.call_action(
        "datastore_create",
        resource_id=resource["id"],
        force=True,
        fields=FIELDS if fields is None else fields,
        records=RECORDS if records is None else records,
    )
    return resource["id"]


def a_resource(scene, dataset="public"):
    """A resource with no DataStore table behind it.

    Enough for everything the **shape** rules refuse: those run before the
    action asks the DataStore anything, which is the point of their being shape
    rules.
    """
    return factories.Resource(package_id=scene[dataset]["id"])


def config(**overrides):
    return {**BASE, **overrides}


def ask(resource_id, **overrides):
    """The **editor** form, as the editor's browser calls it."""
    return helpers.call_action(
        "umss_chart_data", resource_id=resource_id, config=config(**overrides)
    )


def groups(answer):
    return [(group["key"], group["value"]) for group in answer["groups"]]


def assert_allowed(error_dict):
    """L18's closed set, applied to one error dict as it is produced.

    Every assertion about an error key in this file goes through here or
    through `test_every_error_the_action_answers_is_keyed_on_a_contract_key`,
    so a new error path cannot quietly invent a key: the suite fails where the
    error is produced, not when a reviewer notices the key.
    """
    outside = set(error_dict) - ALLOWED_ERROR_KEYS
    assert not outside, "error keyed outside the contract's set: %s" % sorted(
        outside)
    return error_dict


def refusal(resource_id, **overrides):
    """The error dict of an editor call the action refuses."""
    with pytest.raises(toolkit.ValidationError) as caught:
        ask(resource_id, **overrides)
    return assert_allowed(caught.value.error_dict)


def call_as(user, action, **data):
    """Call an action the way the API controller does: a context with only the
    caller. Never `helpers.call_action`, which sets `ignore_auth`."""
    return toolkit.get_action(action)({"user": user and user["name"]}, data)


def execute_on_write_engine(statement, parameters=None):
    """Run a `psycopg2.sql` statement on a write connection.

    The composition is `psycopg2.sql`'s (the same one the action uses), so the
    statement is executed by a psycopg2 cursor and not by SQLAlchemy, whose
    text layer speaks a different spelling. `sqlalchemy.sql` has no `SQL` at
    all: the two modules share the name `sql` and only one of them can be
    imported, which is exactly how this helper came to exist.
    """
    with get_write_engine().begin() as connection:
        driver = connection.connection.driver_connection
        with driver.cursor() as cursor:
            cursor.execute(statement, parameters)


# ---------------------------------------------------------------------------
# The answer's frame
# ---------------------------------------------------------------------------


def test_the_answer_carries_the_contract_version_the_config_and_the_row_count(
        scene):
    resource_id = make_table(scene)

    answer = ask(resource_id)

    assert answer["version"] == 1
    assert answer["truncated"] is False
    assert answer["total_rows"] == len(RECORDS)


def test_the_config_in_the_answer_is_the_normalized_one(scene):
    """The defaults are applied where the caller can see them: a reader never
    has to guess what an omitted `x_fold_case` meant."""
    resource_id = make_table(scene)

    answer = ask(resource_id, x_field="ciudad")

    assert answer["config"] == {
        "version": 1,
        "chart_type": "bar",
        "x_field": "ciudad",
        "x_bucket": "value",
        "x_fold_case": False,
        "y_agg": "count",
        "include_null": True,
    }


def test_total_rows_counts_the_table_not_the_groups(scene):
    """Five rows fall into four groups; `total_rows` is five."""
    resource_id = make_table(scene)

    answer = ask(resource_id)

    assert len(answer["groups"]) == 4
    assert answer["total_rows"] == 5


# ---------------------------------------------------------------------------
# X: the buckets
# ---------------------------------------------------------------------------


def test_a_value_bucket_returns_the_value_itself(scene):
    resource_id = make_table(scene)

    assert groups(ask(resource_id)) == [
        ("a", 1),
        ("b", 2),
        ("c", 1),
        (None, 1),
    ]


def test_a_value_bucket_on_a_timestamp_returns_the_iso_string(scene):
    resource_id = make_table(scene)

    assert groups(ask(resource_id, x_field="fecha")) == [
        ("2024-01-15T00:00:00", 1),
        ("2024-02-10T00:00:00", 1),
        ("2024-04-01T00:00:00", 1),
        ("2025-07-04T00:00:00", 1),
        (None, 1),
    ]


def test_a_number_step_bucket_returns_the_lower_bound_of_the_range(scene):
    """`[k, k + x_step)`: 1 floors to 0 with a step of 2, and 2 and 3 floor to
    2. The null is a group of its own, last."""
    resource_id = make_table(scene)

    assert groups(ask(resource_id, x_field="turno", x_bucket="number_step",
                      x_step=2)) == [
        (0, 2),
        (2, 2),
        (None, 1),
    ]


def test_a_fractional_step_comes_back_as_a_float(scene):
    """`x_step` travels as a `Decimal`, so the bound is exact, and an integral
    key is an `int` while `2.5`'s multiples are floats."""
    resource_id = make_table(scene)

    assert groups(ask(resource_id, x_field="salario", x_bucket="number_step",
                      x_step=2.5)) == [
        (5.0, 1),
        (10.0, 1),
        (20.0, 1),
        (30.0, 1),
        (None, 1),
    ]


@pytest.mark.parametrize("bucket, expected", [
    ("year", [("2024", 3), ("2025", 1), (None, 1)]),
    ("quarter", [("2024-Q1", 2), ("2024-Q2", 1), ("2025-Q3", 1), (None, 1)]),
    ("month", [("2024-01", 1), ("2024-02", 1), ("2024-04", 1),
               ("2025-07", 1), (None, 1)]),
], ids=["year", "quarter", "month"])
def test_a_date_bucket_returns_the_formatted_key(scene, bucket, expected):
    resource_id = make_table(scene)

    assert groups(ask(resource_id, x_field="fecha", x_bucket=bucket)) == expected


def test_the_null_group_is_last_and_can_be_left_out(scene):
    """`include_null: true` makes the null rows a group of their own, and the
    order puts it last; `false` drops those rows from the groups without
    touching `total_rows`."""
    resource_id = make_table(scene)

    with_null = ask(resource_id)
    without_null = ask(resource_id, include_null=False)

    assert groups(with_null)[-1] == (None, 1)
    assert groups(without_null) == [("a", 1), ("b", 2), ("c", 1)]
    assert without_null["total_rows"] == 5


def test_x_fold_case_lowercases_the_key(scene):
    resource_id = make_table(scene)

    assert groups(ask(resource_id, x_field="ciudad", x_fold_case=True)) == [
        ("cochabamba", 1),
        ("la paz", 3),
        (None, 1),
    ]


def test_without_x_fold_case_the_keys_are_raw(scene):
    """No unification is done by default: three spellings are three groups,
    and the portal is the one that decides how to label them."""
    resource_id = make_table(scene)

    answer = ask(resource_id, x_field="ciudad")

    assert sorted(
        (key for key, _ in groups(answer) if key is not None)
    ) == sorted(["La Paz", "la paz", "LA PAZ", "Cochabamba"])
    assert {value for key, value in groups(answer) if key is not None} == {1}


def test_text_keys_order_by_code_point_not_by_the_column_collation(scene):
    """L7: text groups order by code point, so the **action** imposes the
    order — not the column's collation, and not the database's locale.

    Two facts are pinned by one order:

    * uppercase sorts before lowercase: `'A'` (65) before `'b'` (98);
    * `'Á'` (193) sorts after `'z'` (122), not beside `'A'`.

    The column is given the `en-US-x-icu` collation on purpose, because that is
    the order the portal does not want (`A, Á, b, z`), and a test that cannot
    fail pins nothing: this cluster's own collation is already code-point —
    `datcollate` says `en_US.utf8`, that locale is not installed, and Postgres
    falls back to `C`-like ordering — so a plain column could not tell the two
    apart. The null group stays last either way.
    """
    resource_id = make_table(
        scene,
        fields=[{"id": "letra", "type": "text"}],
        records=[
            {"letra": "b"},
            {"letra": "A"},
            {"letra": "Á"},
            {"letra": "z"},
            {"letra": None},
        ],
    )
    execute_on_write_engine(
        sql.SQL(
            "ALTER TABLE {table} ALTER COLUMN {column}"
            " TYPE text COLLATE {collation}"
        ).format(
            table=sql.Identifier(resource_id),
            column=sql.Identifier("letra"),
            collation=sql.Identifier("en-US-x-icu"),
        )
    )

    assert groups(ask(resource_id)) == [
        ("A", 1),
        ("b", 1),
        ("z", 1),
        ("Á", 1),
        (None, 1),
    ]


# ---------------------------------------------------------------------------
# Y: the aggregates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("aggregate, y_field, expected", [
    ("count", None, [("a", 1), ("b", 2), ("c", 1), (None, 1)]),
    ("count_distinct", "salario",
     [("a", 1), ("b", 2), ("c", 1), (None, 0)]),
    ("sum", "salario", [("a", 10.5), ("b", 50), ("c", 5), (None, None)]),
    ("avg", "salario", [("a", 10.5), ("b", 25), ("c", 5), (None, None)]),
    ("min", "salario", [("a", 10.5), ("b", 20), ("c", 5), (None, None)]),
    ("max", "salario", [("a", 10.5), ("b", 30), ("c", 5), (None, None)]),
], ids=["count", "count_distinct", "sum", "avg", "min", "max"])
def test_every_aggregate(scene, aggregate, y_field, expected):
    """`count` counts rows; `count_distinct` counts distinct non-null values
    (`0` for the group whose only value is null); the four numeric aggregates
    answer `null` for a group with nothing to aggregate — which is SQL's
    answer, not a substitute for it."""
    resource_id = make_table(scene)

    overrides = {"y_agg": aggregate}
    if y_field:
        overrides["y_field"] = y_field

    assert groups(ask(resource_id, **overrides)) == expected


def test_count_distinct_takes_a_non_numeric_field(scene):
    """The contract admits any type here, text included.

    The last group is `0`, not `1`: its only row has `ciudad` NULL, and
    `count(DISTINCT y)` counts non-null values (L6 extends that to blanks, not
    just to NULL). A `count_distinct` that answered 1 there would be counting
    the absence of a value as a value.
    """
    resource_id = make_table(scene)

    assert groups(ask(resource_id, y_agg="count_distinct",
                      y_field="ciudad")) == [
        ("a", 1),
        ("b", 2),
        ("c", 1),
        (None, 0),
    ]


# ---------------------------------------------------------------------------
# A text value with nothing in it is no data (L17)
#
# `''` and `'   '` are how a spreadsheet spells an empty cell, and they mean
# the same thing a missing value means: the null group. The expression is
# `NULLIF(btrim(col), '')`, which also trims what survives, so `' Mañana'` and
# `'Mañana'` are one group and not two.
# ---------------------------------------------------------------------------


def test_a_blank_text_key_is_the_null_group_and_padded_values_merge(scene):
    """One test for the three facts: `''` and `'   '` land in the null group,
    the null group is last, and the padded value merges with the bare one."""
    resource_id = make_table(scene)

    answer = ask(resource_id, x_field="nota")

    assert groups(answer) == [("Mañana", 2), (None, 3)]
    assert answer["total_rows"] == 5


def test_include_null_false_drops_blank_text(scene):
    """A blank is filtered by the same expression that groups it."""
    resource_id = make_table(scene)

    answer = ask(resource_id, x_field="nota", include_null=False)

    assert groups(answer) == [("Mañana", 2)]
    assert answer["total_rows"] == 5


def test_x_fold_case_folds_the_trimmed_key(scene):
    """The order of the expressions: trim and blank out first, fold second."""
    resource_id = make_table(scene)

    answer = ask(resource_id, x_field="nota", x_fold_case=True)

    assert groups(answer) == [("mañana", 2), (None, 3)]


def test_count_distinct_does_not_count_a_blank_text(scene):
    """`''` is not a value to count. Without `NULLIF(btrim(y), '')` the `b`
    group would answer 2 (`' Mañana'` and `''`) and `c` would answer 1."""
    resource_id = make_table(scene)

    answer = ask(resource_id, y_agg="count_distinct", y_field="nota")

    assert groups(answer) == [("a", 1), ("b", 1), ("c", 0), (None, 0)]


# ---------------------------------------------------------------------------
# The caps: 500 groups, and a statement timeout
# ---------------------------------------------------------------------------

GROUP_CAP = 500


def test_more_than_five_hundred_groups_are_cut_and_declared_truncated(scene):
    """501 groups would mean the portal is asked to draw a chart nobody can
    read; the answer says so instead of lying about it."""
    resource_id = make_table(
        scene,
        fields=[{"id": "n", "type": "int4"}],
        records=[{"n": number} for number in range(GROUP_CAP + 100)],
    )

    answer = ask(resource_id, x_field="n")

    assert len(answer["groups"]) == GROUP_CAP
    assert answer["truncated"] is True
    assert answer["total_rows"] == GROUP_CAP + 100
    assert [key for key, _ in groups(answer)] == list(range(GROUP_CAP))


def test_five_hundred_groups_are_not_truncated(scene):
    """The boundary the cap is read at: exactly 500 groups fit."""
    resource_id = make_table(
        scene,
        fields=[{"id": "n", "type": "int4"}],
        records=[{"n": number} for number in range(GROUP_CAP)],
    )

    answer = ask(resource_id, x_field="n")

    assert len(answer["groups"]) == GROUP_CAP
    assert answer["truncated"] is False


def test_a_cancelled_query_is_a_validation_error_keyed_on_query(
        scene, ckan_config):
    """`ckanext.umss.chart.timeout_ms` is the cap, and a cap that expires is
    the caller's problem to see, not a 500.

    The same table answers with a generous cap immediately afterwards, so the
    error is the timeout and not a broken table.
    """
    rows = 300_000
    resource_id = make_table(
        scene,
        fields=[{"id": "n", "type": "int4"}, {"id": "v", "type": "numeric"}],
        records=[],
    )
    execute_on_write_engine(
        sql.SQL(
            "INSERT INTO {table} (_id, _full_text, n, v)"
            " SELECT g, '', g % 1000, g FROM generate_series(1, {rows}) g"
        ).format(table=sql.Identifier(resource_id), rows=sql.Literal(rows))
    )

    ckan_config["ckanext.umss.chart.timeout_ms"] = "1"
    assert "query" in refusal(resource_id, x_field="n")

    ckan_config["ckanext.umss.chart.timeout_ms"] = "600000"
    answer = ask(resource_id, x_field="n")

    assert answer["total_rows"] == rows
    assert answer["truncated"] is True


# ---------------------------------------------------------------------------
# The two call forms
# ---------------------------------------------------------------------------


def saved_view(resource_id, **overrides):
    return helpers.call_action(
        "resource_view_create",
        resource_id=resource_id,
        view_type="umss_chart",
        title="Salario por turno",
        **config(**overrides),
    )


def test_the_visitor_form_reads_the_saved_configuration(scene):
    """The visitor passes a view id and nothing else. What is grouped is what
    the publisher saved, with the defaults the save applied."""
    resource_id = make_table(scene)
    view = saved_view(resource_id, x_field="turno", x_bucket="number_step",
                      x_step=2, y_agg="sum", y_field="salario",
                      include_null=False)

    answer = call_as(None, "umss_chart_data", view_id=view["id"])

    assert answer["config"] == config(
        x_field="turno", x_bucket="number_step", x_step=2, y_agg="sum",
        y_field="salario", include_null=False, x_fold_case=False,
    )
    assert groups(answer) == [(0, 30.5), (2, 30)]
    assert answer["total_rows"] == 5


def test_the_editor_form_does_not_need_a_saved_view(scene):
    """Configuring is a preview: nothing is stored, and no view exists."""
    resource_id = make_table(scene)

    answer = ask(resource_id, x_field="turno", x_bucket="number_step", x_step=2)

    assert helpers.call_action("resource_view_list", id=resource_id) == []
    assert groups(answer) == [(0, 2), (2, 2), (None, 1)]


@pytest.mark.parametrize("data, keys", [
    ({}, {"view_id", "resource_id"}),
    ({"view_id": "00000000-0000-0000-0000-000000000000",
      "resource_id": "00000000-0000-0000-0000-000000000000",
      "config": BASE}, {"view_id", "resource_id"}),
], ids=["neither", "both"])
def test_the_call_form_is_one_of_the_two(scene, data, keys):
    with pytest.raises(toolkit.ValidationError) as caught:
        helpers.call_action("umss_chart_data", **data)

    assert keys <= set(assert_allowed(caught.value.error_dict))


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_an_anonymous_visitor_reads_a_view_of_a_public_resource(scene):
    resource_id = make_table(scene)
    view = saved_view(resource_id)

    answer = call_as(None, "umss_chart_data", view_id=view["id"])

    assert answer["groups"]


def test_an_anonymous_visitor_cannot_read_a_view_of_a_private_resource(scene):
    resource_id = make_table(scene, dataset="private")
    view = saved_view(resource_id)

    with pytest.raises(toolkit.NotAuthorized):
        call_as(None, "umss_chart_data", view_id=view["id"])


def test_an_organization_editor_may_configure(scene):
    """The editor form's capacity is `resource_update`, and this extension
    chains that to the publication wall: the wall has to let an ordinary
    organization editor through, or no chart could ever be configured."""
    resource_id = make_table(scene)

    answer = call_as(scene["editor"], "umss_chart_data",
                     resource_id=resource_id, config=config())

    assert groups(answer) == [("a", 1), ("b", 2), ("c", 1), (None, 1)]


def test_an_organization_member_may_not_configure(scene):
    """A member can read and manage the organization's membership; configuring
    a chart is an edit of the resource."""
    resource_id = make_table(scene)

    with pytest.raises(toolkit.NotAuthorized):
        call_as(scene["member"], "umss_chart_data",
                resource_id=resource_id, config=config())


def test_an_anonymous_caller_may_not_configure_even_a_public_resource(scene):
    """The public dataset is readable by everyone; configuring it is not."""
    resource_id = make_table(scene)

    with pytest.raises(toolkit.NotAuthorized):
        call_as(None, "umss_chart_data",
                resource_id=resource_id, config=config())


# ---------------------------------------------------------------------------
# The errors, each on its culprit key
# ---------------------------------------------------------------------------


def test_a_missing_view_is_not_found(scene):
    with pytest.raises(toolkit.ObjectNotFound):
        call_as(None, "umss_chart_data",
                view_id="00000000-0000-0000-0000-000000000000")


def test_a_view_of_another_type_is_refused_by_view_id(scene):
    """`umss_chart_data` answers for its own view type. Another type's config
    means nothing here, and the key says which argument was wrong."""
    resource_id = make_table(scene)
    view = saved_view(resource_id)
    row = ckan_model.ResourceView.get(view["id"])
    row.view_type = "other_view"
    ckan_model.Session.commit()

    with pytest.raises(toolkit.ValidationError) as caught:
        call_as(None, "umss_chart_data", view_id=view["id"])

    assert "view_id" in assert_allowed(caught.value.error_dict)


def test_a_missing_resource_is_not_found(scene):
    with pytest.raises(toolkit.ObjectNotFound):
        ask("00000000-0000-0000-0000-000000000000")


def test_a_resource_without_a_datastore_table_is_not_found(scene):
    """The same answer `datastore_search` gives for such a resource: a chart
    needs a table, and its absence is a missing thing, not a bad request."""
    resource = factories.Resource(package_id=scene["public"]["id"])

    with pytest.raises(toolkit.ObjectNotFound):
        ask(resource["id"])


def test_a_config_that_is_not_an_object_is_refused_by_config(scene):
    resource = a_resource(scene)

    with pytest.raises(toolkit.ValidationError) as caught:
        helpers.call_action("umss_chart_data", resource_id=resource["id"],
                            config="surprise")

    assert "config" in assert_allowed(caught.value.error_dict)


def test_an_undeclared_config_key_is_refused_under_config(scene):
    """L18: the editor's undeclared key is refused under `config` and not by
    its own name. The contract fixes the set of error keys a consumer may
    match on, and a key the caller invented cannot be one of them; the key's
    name survives in the message, one message per key."""
    resource = a_resource(scene)

    assert refusal(resource["id"], colour="red")["config"] == [
        "Unexpected key: colour"]
    assert refusal(resource["id"], size=3, colour="red")["config"] == [
        "Unexpected key: colour",
        "Unexpected key: size",
    ]


def test_the_shape_rules_run_on_the_editor_config_too(scene):
    """One definition of a valid configuration: the same `__after` hook the
    save path runs, reached by the editor before anything is drawn."""
    resource = a_resource(scene)
    draft = config(x_field="turno", x_bucket="number_step")

    with pytest.raises(toolkit.ValidationError) as caught:
        helpers.call_action("umss_chart_data", resource_id=resource["id"],
                            config=draft)

    assert "x_step" in assert_allowed(caught.value.error_dict)
    assert "y_field" in refusal(resource["id"], y_agg="sum")


def test_x_bucket_is_required_in_the_editor_config(scene):
    """L12: `x_bucket` is required, with no default, on this path too."""
    resource = a_resource(scene)
    draft = {key: value for key, value in config().items() if key != "x_bucket"}

    with pytest.raises(toolkit.ValidationError) as caught:
        helpers.call_action("umss_chart_data", resource_id=resource["id"],
                            config=draft)

    assert "x_bucket" in assert_allowed(caught.value.error_dict)


def test_the_internal_id_is_never_charted_by_the_editor_either(scene):
    """L12: the rule is a shape rule, so the preview refuses `_id` before it
    asks the DataStore whether the column exists — it exists, and it is still
    not chartable."""
    resource = a_resource(scene)

    assert "x_field" in refusal(resource["id"], x_field="_id")
    assert "y_field" in refusal(resource["id"], y_agg="sum", y_field="_id")


def test_x_field_has_to_exist_in_the_table(scene):
    resource_id = make_table(scene)

    assert "x_field" in refusal(resource_id, x_field="nope")


def test_an_underscore_field_is_never_a_field(scene):
    """`_id` is the DataStore's own row key, and every `_`-prefixed column is
    the backend's. Neither is a column a publisher charted — a shape rule, so
    no table is needed to refuse it."""
    resource = a_resource(scene)

    assert "x_field" in refusal(resource["id"], x_field="_id")
    assert "y_field" in refusal(resource["id"], y_agg="sum",
                                y_field="_full_text")


def test_a_number_step_needs_a_numeric_column(scene):
    resource_id = make_table(scene)

    assert "x_bucket" in refusal(resource_id, x_field="letra",
                                 x_bucket="number_step", x_step=1)


def test_a_date_bucket_needs_a_timestamp_column(scene):
    resource_id = make_table(scene)

    for bucket in ("year", "quarter", "month"):
        assert "x_bucket" in refusal(resource_id, x_field="turno",
                                     x_bucket=bucket), bucket


def test_fold_case_needs_a_text_column(scene):
    """`lower()` on a number is not a labelling choice, it is a mistake."""
    resource_id = make_table(scene)

    assert "x_fold_case" in refusal(resource_id, x_field="turno",
                                    x_fold_case=True)
    assert "x_fold_case" in refusal(resource_id, x_field="ciudad",
                                    x_bucket="year", x_fold_case=True)


def test_the_numeric_aggregates_need_a_numeric_y_field(scene):
    resource_id = make_table(scene)

    for aggregate in ("sum", "avg", "min", "max"):
        assert "y_field" in refusal(resource_id, y_agg=aggregate,
                                    y_field="letra"), aggregate


def test_y_field_has_to_exist_in_the_table(scene):
    resource_id = make_table(scene)

    assert "y_field" in refusal(resource_id, y_agg="count_distinct",
                                y_field="nope")


def test_every_error_the_action_answers_is_keyed_on_a_contract_key(scene):
    """L18: the action's `ValidationError` keys come from one closed set.

    Every case this file triggers is walked here through the same channel, so
    the set is checked where the errors are produced and not only in whichever
    test happens to hit one. The cancelled query is the exception: it needs a
    300k-row table, so its own test walks it — and goes through `refusal()`,
    which applies this same assertion.

    `ObjectNotFound` and `NotAuthorized` are absent on purpose: they are not
    `ValidationError`s, and the contract keys nothing on them. So is `__extras`:
    that is core's key on `resource_view_create`, and this test is about the
    action.
    """
    resource_id = make_table(scene)

    def keyed(**overrides):
        return lambda: helpers.call_action(
            "umss_chart_data",
            resource_id=resource_id,
            config={**config(), **overrides},
        )

    def without(name):
        return lambda: helpers.call_action(
            "umss_chart_data",
            resource_id=resource_id,
            config={key: value for key, value in config().items()
                    if key != name},
        )

    missing_id = "00000000-0000-0000-0000-000000000000"
    cases = [
        ("neither form",
         lambda: helpers.call_action("umss_chart_data"),
         {"view_id", "resource_id"}),
        ("both forms",
         lambda: helpers.call_action(
             "umss_chart_data", view_id=missing_id,
             resource_id=missing_id, config=config()),
         {"view_id", "resource_id"}),
        ("a config that is not an object",
         lambda: helpers.call_action(
             "umss_chart_data", resource_id=resource_id, config="surprise"),
         {"config"}),
        ("an undeclared config key", keyed(colour="red"), {"config"}),
        ("a missing version", without("version"), {"version"}),
        ("a bad chart_type", keyed(chart_type="donut"), {"chart_type"}),
        ("a missing x_field", without("x_field"), {"x_field"}),
        ("an internal x_field", keyed(x_field="_id"), {"x_field"}),
        ("a missing x_bucket", without("x_bucket"), {"x_bucket"}),
        ("a bad x_bucket", keyed(x_bucket="monotonic"), {"x_bucket"}),
        ("a step without number_step", keyed(x_step=5), {"x_step"}),
        ("number_step without a step",
         keyed(x_field="turno", x_bucket="number_step"), {"x_step"}),
        ("a zero step",
         keyed(x_field="turno", x_bucket="number_step", x_step=0),
         {"x_step"}),
        ("a missing y_agg", without("y_agg"), {"y_agg"}),
        ("a bad y_agg", keyed(y_agg="median"), {"y_agg"}),
        ("a y_field with count", keyed(y_field="salario"), {"y_field"}),
        ("sum without a y_field", keyed(y_agg="sum"), {"y_field"}),
        ("an internal y_field",
         keyed(y_agg="sum", y_field="_id"), {"y_field"}),
        ("an x_field that is not a column", keyed(x_field="nope"),
         {"x_field"}),
        ("a y_field that is not a column",
         keyed(y_agg="count_distinct", y_field="nope"), {"y_field"}),
        ("number_step on a text column",
         keyed(x_field="letra", x_bucket="number_step", x_step=1),
         {"x_bucket"}),
        ("a date bucket on a text column",
         keyed(x_field="letra", x_bucket="year"), {"x_bucket"}),
        ("fold_case on a number",
         keyed(x_field="turno", x_fold_case=True), {"x_fold_case"}),
        ("sum on a text column",
         keyed(y_agg="sum", y_field="letra"), {"y_field"}),
    ]

    for label, call, expected in cases:
        with pytest.raises(toolkit.ValidationError) as caught:
            call()
        keys = set(assert_allowed(caught.value.error_dict))
        assert keys == expected, "%s: %s" % (label, keys)

    # L18's own change, spelled out: the undeclared key is reported under
    # `config`, one message per key, and the caller's names live in the text.
    with pytest.raises(toolkit.ValidationError) as caught:
        keyed(size=3, colour="red")()

    assert caught.value.error_dict["config"] == [
        "Unexpected key: colour",
        "Unexpected key: size",
    ]


# ---------------------------------------------------------------------------
# The type families, measured end to end
# ---------------------------------------------------------------------------

#: The type spellings a publisher's import can declare, and the `typname`
#: `datastore_info` reports for each. The DataStore does not normalise the
#: declaration — the column is created with the string as given and the type is
#: read back from the column's oid (`postgres.py`, `_get_type`) — so `int`,
#: `bigint`, `float` and `double precision` all answer a plain `typname`.
TYPE_SPELLINGS = (
    ("i", "int", "int4"),
    ("i4", "int4", "int4"),
    ("i8", "int8", "int8"),
    ("big", "bigint", "int8"),
    ("f", "float", "float8"),
    ("f8", "float8", "float8"),
    ("dp", "double precision", "float8"),
    ("num", "numeric", "numeric"),
    ("ts", "timestamp", "timestamp"),
    ("tstz", "timestamptz", "timestamptz"),
    ("d", "date", "date"),
)

#: The reported names above that hold dates. Spelled out here rather than read
#: from the action, so the rows are built without asking the code under test
#: what it thinks a date is.
DATE_TYPENAMES = frozenset(("timestamp", "timestamptz", "date"))


def _type_records():
    """Three rows in two groups, with three distinct numeric values and three
    distinct dates, so every aggregate below has something to say."""
    rows = (
        (1, 10, "2024-01-15"),
        (2, 20, "2025-07-04"),
        (1, 30, "2024-12-31"),
    )
    records = []
    for group, number, moment in rows:
        record = {"g": group}
        for field, _declared, reported in TYPE_SPELLINGS:
            record[field] = moment if reported in DATE_TYPENAMES else number
        records.append(record)
    return records


def test_the_type_families_are_the_typnames_datastore_reports(scene):
    """Every spelling in `TYPE_SPELLINGS` becomes a column, `datastore_info`
    reports its `typname`, and each reported name is accepted by the bucket and
    the aggregate the contract's families say it should be.

    `int` is deliberately **not** in a family. The backend hardcodes `_id` as
    `int` (`postgres.py`, `_get_fields_types`: `{'id': '_id', 'type': 'int'}`)
    and `datastore_info` filters `_`-prefixed columns out, so `int` never names
    a column a publisher can chart. A family that accepted it would be a family
    for a type this database never reports.
    """
    from ckanext.umss.logic.action import chart

    fields = [{"id": "g", "type": "int4"}] + [
        {"id": field, "type": declared}
        for field, declared, _reported in TYPE_SPELLINGS
    ]
    resource_id = make_table(scene, fields=fields, records=_type_records())

    reported = {
        field["id"]: field["type"]
        for field in helpers.call_action(
            "datastore_info",
            resource_id=resource_id,
            include_meta=False,
            include_fields_schema=False,
        )["fields"]
    }

    assert reported == {
        "g": "int4",
        **{field: name for field, _declared, name in TYPE_SPELLINGS},
    }
    assert "int" not in reported.values()
    assert "int" not in chart.NUMERIC_TYPES

    for field, declared, name in TYPE_SPELLINGS:
        if name in chart.TIMESTAMP_TYPES:
            answer = ask(resource_id, x_field=field, x_bucket="year")
            assert groups(answer) == [("2024", 2), ("2025", 1)], declared
            continue

        assert name in chart.NUMERIC_TYPES, "%s (%s) is in no family" % (
            name, declared)

        answer = ask(resource_id, x_field=field, x_bucket="number_step",
                     x_step=20)
        # A `float8` column answers float bounds; the bound is the assertion,
        # not whether JSON spells it `0` or `0.0`.
        assert [key for key, _ in groups(answer)] == [0, 20], declared
        assert [value for _, value in groups(answer)] == [1, 2], declared

        answer = ask(resource_id, x_field="g", y_agg="sum", y_field=field)
        assert groups(answer) == [(1, 40), (2, 20)], declared


# ---------------------------------------------------------------------------
# Security: the identifiers
# ---------------------------------------------------------------------------


def test_a_hostile_column_name_is_an_identifier_and_changes_nothing(scene):
    """The contract's own example: a column named like a SQL injection.

    The DataStore refuses a `"` in a field name (`is_valid_field_name`,
    `ckanext/datastore/helpers.py`), so this name can only reach a table by
    being renamed into place — which is exactly the point: the name is data
    from the database, and it has to be quoted, never pasted into the SQL.
    """
    hostile = 'x") ; drop table t; --'
    resource_id = make_table(
        scene,
        fields=[{"id": "nombre", "type": "text"}],
        records=[{"nombre": "a"}, {"nombre": "b"}],
    )
    execute_on_write_engine(
        sql.SQL("ALTER TABLE {table} RENAME COLUMN {old} TO {new}").format(
            table=sql.Identifier(resource_id),
            old=sql.Identifier("nombre"),
            new=sql.Identifier(hostile),
        )
    )

    answer = ask(resource_id, x_field=hostile, y_agg="count")

    assert groups(answer) == [("a", 1), ("b", 1)]
    assert helpers.call_action(
        "datastore_search", resource_id=resource_id
    )["total"] == 2


def test_the_query_runs_on_the_datastore_read_engine(scene, monkeypatch):
    """The grouping is a read, so it is sent to the role that can only read.

    What this proves is the call: the SQL goes through
    `ckanext.datastore.backend.postgres.get_read_engine`. That the role itself
    cannot write is the database's grant, not this test's.
    """
    from ckanext.umss.logic.action import chart

    resource_id = make_table(scene)
    engines = []
    real = chart.get_read_engine

    def spy():
        engine = real()
        engines.append(engine)
        return engine

    monkeypatch.setattr(chart, "get_read_engine", spy)

    ask(resource_id)

    assert len(engines) == 1
