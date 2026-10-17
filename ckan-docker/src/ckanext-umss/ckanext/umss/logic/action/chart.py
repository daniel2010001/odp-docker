"""
`umss_chart_data`: the grouping the `umss_chart` view reads.

Contract v1 (`odp/odd/tasks/csv-charts.md`, "La acción `umss_chart_data`") and
the O2 design in `odd/tasks/csv-charts-umss.md`. Two call forms and one answer:

* **visitor** — `{"view_id": "<id>"}`, reads the configuration the publisher
  saved and needs what `resource_view_show` needs (see `logic/auth/chart.py`);
* **editor** — `{"resource_id": "<id>", "config": {…}}`, needs
  `resource_update`, so a draft can be drawn before anybody saves it.

What makes this a plugin action rather than a `datastore_search_sql` the
browser composes: the visitor runs **only** configurations a publisher saved,
and the SQL is built here from a whitelist, never from the caller's text.

The layers, in the order a call meets them:

1. **the call form** (`_call_form`) — exactly one of the two, or a
   `ValidationError` keyed on both names;
2. **authorization** — `check_access('umss_chart_data')`, which delegates to
   `resource_view_show` or `resource_update`;
3. **the shape rules** — `ckanext.umss.charts.validate_config`, the same schema
   a saved view is validated against, so the save path and this one cannot
   disagree;
4. **the DataStore rules** — do the named columns exist, and are their types
   the ones the bucket and the aggregate need (`_datastore_errors`);
5. **the query** — composed with `psycopg2.sql`, run on the DataStore's *read*
   engine in one transaction with a `statement_timeout` (`_read`).

Why 4 and 5 are separate from 3: saving a view must not query the DataStore, so
the "does this column exist" rules can only live here. That split is a decision
the portal accepted (L3 in the feature document), and it is why a saved view can
exist that `umss_chart_data` then refuses with `{"x_field": [...]}` — the
publisher's data changed under the view, and the refusal names the column.

The SQL surface is the contract's whitelist: `date_trunc`, `floor`, `count`,
`sum`, `avg`, `min`, `max`, plus two functions the contract's list omits while
its own rules need them — `lower` for `x_fold_case`, and `btrim` for L17's
"a text value with nothing in it is no data" (`NULLIF(btrim(col), '')`). Both
are reported to the portal rather than added to the contract here. Text keys
also carry `COLLATE "C"` (L7), which is an operator and not a function, and it
is applied only to the `value` bucket on a text column.
"""
from __future__ import annotations

import datetime
import decimal
import logging
import math

import ckan.model as model
import ckan.plugins.toolkit as toolkit
import psycopg2
from psycopg2 import sql

from ckanext.datastore.backend.postgres import get_read_engine

from ckanext.umss import charts


log = logging.getLogger(__name__)

__all__ = ["umss_chart_data"]


#: At most this many groups in an answer (contract v1). One extra row is
#: fetched, and its presence is the whole of `truncated`.
GROUP_CAP = 500

#: The cap on the query itself, in milliseconds, and the setting that moves it.
TIMEOUT_SETTING = "ckanext.umss.chart.timeout_ms"
DEFAULT_TIMEOUT_MS = 5000

#: `statement_timeout`'s SQLSTATE for a cancelled statement.
QUERY_CANCELED = "57014"

#: The type families the contract reasons in, as `datastore_info` reports them
#: (postgres `typname`). Timestamp includes `date`, and numeric includes the
#: integer types: `sum` of an `int4` is as reasonable as `sum` of a `numeric`.
NUMERIC_TYPES = frozenset(
    ("numeric", "int2", "int4", "int8", "float4", "float8")
)
TIMESTAMP_TYPES = frozenset(("timestamp", "timestamptz", "date"))
TEXT_TYPES = frozenset(("text", "varchar"))
#: The numeric types that can hold a non-finite value. An integer cannot, and
#: comparing one with `'NaN'` is an error, so they are left alone.
NON_FINITE_TYPES = frozenset(("numeric", "float4", "float8"))
NON_FINITE = ("NaN", "Infinity", "-Infinity")

#: Buckets truncating a timestamp, and the unit `date_trunc` takes for each.
DATE_UNITS = {"year": "year", "quarter": "quarter", "month": "month"}

#: L7: text keys order by code point. The collation is named here and applied
#: as an `sql.Identifier` (`COLLATE "C"`), and only where it can matter — the
#: `value` bucket on a text column. Without it, the order is whatever the
#: column's collation says, which on an `en_US` database puts `'Á'` beside
#: `'A'` and before `'b'`; a portal drawing those groups left to right would be
#: showing a locale's opinion, not an order it can explain.
TEXT_COLLATION = "C"

#: The aggregates that need a numeric column to work on. `count` counts rows and
#: `count_distinct` counts values, so neither is here.
NUMERIC_AGGS = frozenset(("sum", "avg", "min", "max"))

#: The aggregate expressions, one template per `y_agg`, so no caller text ever
#: reaches the SQL.
AGGREGATE_SQL = {
    "count_distinct": sql.SQL("count(DISTINCT {column})"),
    "sum": sql.SQL("sum({column})"),
    "avg": sql.SQL("avg({column})"),
    "min": sql.SQL("min({column})"),
    "max": sql.SQL("max({column})"),
}

#: How a truncated timestamp becomes the contract's key: `"2024"`, `"2024-Q1"`,
#: `"2024-01"`.
DATE_KEY_FORMATS = {
    "year": lambda moment: "%04d" % moment.year,
    "quarter": lambda moment: "%04d-Q%d" % (moment.year, (moment.month - 1) // 3 + 1),
    "month": lambda moment: "%04d-%02d" % (moment.year, moment.month),
}


# ---------------------------------------------------------------------------
# The call forms
# ---------------------------------------------------------------------------


def _call_form(data_dict):
    """`"view"` or `"editor"`, or a `ValidationError`.

    The form is read from the arguments, not from the caller's capacity: a
    visitor and an editor send different requests, and a request that is both
    or neither is malformed whoever sends it.
    """
    has_view = data_dict.get("view_id") not in (None, "")
    has_resource = data_dict.get("resource_id") not in (None, "")

    if has_view and has_resource:
        message = toolkit._(
            "Pass view_id, or resource_id with a config, not both"
        )
        raise toolkit.ValidationError({
            "view_id": [message],
            "resource_id": [message],
        })
    if not has_view and not has_resource:
        raise toolkit.ValidationError({
            "view_id": [toolkit._("Missing value: pass view_id")],
            "resource_id": [
                toolkit._("Missing value: pass resource_id with a config")
            ],
        })
    return "view" if has_view else "editor"


def _saved_chart(view_id):
    """The saved configuration of a `umss_chart` view, normalized, and the
    resource it belongs to.

    Authorization already happened: the auth function dispatches the visitor
    form to `resource_view_show` (see `logic/auth/chart.py`), which is the
    capacity the contract names for this form. A caller that skipped
    authorization (`ignore_auth`) still gets the two `NotFound` answers below,
    because they are about the world and not about the caller.
    """
    view = model.ResourceView.get(view_id)
    if view is None:
        raise toolkit.ObjectNotFound(
            toolkit._("Resource view not found: %s") % view_id
        )
    resource = model.Resource.get(view.resource_id)
    if resource is None:
        raise toolkit.ObjectNotFound(
            toolkit._("Resource not found: %s") % view.resource_id
        )

    if view.view_type != charts.VIEW_TYPE:
        raise toolkit.ValidationError({
            "view_id": [toolkit._(
                "This view is a %(found)s, not a %(wanted)s"
            ) % {"found": view.view_type, "wanted": charts.VIEW_TYPE}],
        })

    return charts.validate_config(view.config or {}), resource.id


def _draft_chart(resource_id, config):
    """The editor's configuration, normalized, after proving the resource it is
    for exists.

    `helpers.call_action` and any other `ignore_auth` caller skip the auth
    function, so the existence check cannot be left to `resource_update`'s
    authorization alone.
    """
    if model.Resource.get(resource_id) is None:
        raise toolkit.ObjectNotFound(
            toolkit._("Resource not found: %s") % resource_id
        )
    return charts.validate_config(config)


# ---------------------------------------------------------------------------
# The DataStore rules
# ---------------------------------------------------------------------------


def _datastore_fields(resource_id):
    """The table's fields, by `datastore_info`.

    Authorized already: our own check ran, and this call is not the place to
    run a second, weaker one. `ignore_auth` is what says so. A resource with no
    DataStore table is `ObjectNotFound` there, which is what `datastore_search`
    answers for the same resource.
    """
    info = toolkit.get_action("datastore_info")(
        {"ignore_auth": True},
        {
            "resource_id": resource_id,
            "include_meta": False,
            "include_fields_schema": False,
        },
    )
    return {field["id"]: field["type"] for field in info["fields"]}


def _field_type(name, fields):
    """A column's DataStore type, or `None` for something that is not a column.

    A `_`-prefixed name is refused earlier by the shape rule in
    `charts.cross_key_rules` (L12), and it is refused again here on purpose:
    `datastore_info` does not report those columns, and a function that builds
    SQL must not depend on a check it cannot see having run. `_id` is the
    DataStore's row key and `_full_text` its search column; neither is a column
    a publisher charted.
    """
    if not name or name.startswith(charts.INTERNAL_PREFIX):
        return None
    return fields.get(name)


def _datastore_errors(config, fields):
    """The rules that need the table, keyed on their culprit.

    A shape problem is a bad request; one of these is a request the table
    cannot answer — a column renamed away, a text column asked for a sum. Both
    are `ValidationError`, and both name one key: `x_field`/`y_field` for a
    column that is not there, `x_bucket`/`x_fold_case`/`y_field` for a column
    of the wrong type.
    """
    errors = {}

    def fail(name, message):
        errors.setdefault(name, []).append(message)

    x_type = _field_type(config["x_field"], fields)
    if x_type is None:
        fail("x_field", toolkit._(
            "%(name)s is not a field of this resource's DataStore table"
        ) % {"name": config["x_field"]})
    else:
        bucket = config["x_bucket"]
        if bucket == charts.NUMBER_STEP and x_type not in NUMERIC_TYPES:
            fail("x_bucket", toolkit._(
                "The number_step bucket needs a numeric column, and %(name)s "
                "is %(type)s"
            ) % {"name": config["x_field"], "type": x_type})
        if bucket in DATE_UNITS and x_type not in TIMESTAMP_TYPES:
            fail("x_bucket", toolkit._(
                "The %(bucket)s bucket needs a timestamp column, and %(name)s "
                "is %(type)s"
            ) % {"bucket": bucket, "name": config["x_field"], "type": x_type})
        if config["x_fold_case"] and (
            bucket != "value" or x_type not in TEXT_TYPES
        ):
            fail("x_fold_case", toolkit._(
                "Folding case is for the value bucket on a text column, and "
                "%(name)s is %(type)s with the %(bucket)s bucket"
            ) % {
                "name": config["x_field"],
                "type": x_type,
                "bucket": config["x_bucket"],
            })

    if config["y_agg"] != charts.COUNT:
        y_type = _field_type(config["y_field"], fields)
        if y_type is None:
            fail("y_field", toolkit._(
                "%(name)s is not a field of this resource's DataStore table"
            ) % {"name": config["y_field"]})
        elif config["y_agg"] in NUMERIC_AGGS and y_type not in NUMERIC_TYPES:
            fail("y_field", toolkit._(
                "The %(aggregate)s aggregate needs a numeric column, and "
                "%(name)s is %(type)s"
            ) % {
                "aggregate": config["y_agg"],
                "name": config["y_field"],
                "type": y_type,
            })

    return errors


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------


def _no_blank(value):
    """A text value with nothing in it, read as no data (L17).

    `btrim` is here for the same reason `lower` is: the rule cannot be
expressed without it, and the contract's function list does not name it. It
also trims the values that survive, which is the point — `' Mañana'` and
`'Mañana'` are one group, not two. The column comes in as a Composable and the
empty string is a `Literal`, so nothing in the SQL text is hand-quoted.
    """
    return sql.SQL("NULLIF(btrim({value}), {blank})").format(
        value=value, blank=sql.Literal("")
    )


def _no_non_finite(value):
    """A non-finite number, read as no data (L23).

    `NaN`, `Infinity` and `-Infinity` would otherwise be groups of their own
    whose keys render `null`, and `-Infinity` would sort first. As no data they
    join the one null group, last, under the same rule as a blank text. `CASE`
    is syntax, not a function. Postgres compares `NaN` equal to itself, so `IN`
    finds it.
    """
    return sql.SQL("CASE WHEN {value} IN ({non_finite}) THEN NULL ELSE {value} END").format(
        value=value,
        non_finite=sql.SQL(", ").join(sql.Literal(v) for v in NON_FINITE),
    )


def _x_values(config, x_type):
    """The X column as a value, with a text column's blanks read as no data.

    Only `value` ever meets a text column — `number_step` and the date buckets
    are refused on text by `_datastore_errors` — so this is the one
    normalisation those buckets need, and the null group is where a blank ends
    up: last, or gone under `include_null: false`.
    """
    column = sql.Identifier(config["x_field"])
    if x_type in TEXT_TYPES:
        return _no_blank(column)
    if x_type in NON_FINITE_TYPES:
        return _no_non_finite(column)
    return column


def _key_expression(config, x_type):
    """The X side: the group's key, as SQL.

    The column is an `sql.Identifier`, so a name that reads like SQL is a name.
    The step is a parameter, not a fragment: it comes back to Python as a
    `Decimal`, so the range bounds are exact and not a float's approximation.

    A text key carries `COLLATE "C"` outside whatever else was done to it, so
    grouping and ordering answer code-point order whatever the column's own
    collation is (L7). Only the `value` bucket ever meets a text column:
    `number_step` and the date buckets refuse it in `_datastore_errors`.
    """
    value = _x_values(config, x_type)
    bucket = config["x_bucket"]

    if bucket == "value":
        if config["x_fold_case"]:
            value = sql.SQL("lower({value})").format(value=value)
        if x_type in TEXT_TYPES:
            value = sql.SQL("{value} COLLATE {collation}").format(
                value=value, collation=sql.Identifier(TEXT_COLLATION)
            )
        return value
    if bucket == charts.NUMBER_STEP:
        return sql.SQL("floor({value} / {step}) * {step}").format(
            value=value, step=sql.Placeholder("step")
        )
    return sql.SQL("date_trunc({unit}, {value})").format(
        unit=sql.Literal(DATE_UNITS[bucket]), value=value
    )


def _value_expression(config, y_type):
    """The Y side: one aggregate over the whole group.

    `count_distinct` over text reads a blank as no value, the same way the X
    key does, so it does not count `''` as a distinct answer to "how many
    different values are here".
    """
    aggregate = config["y_agg"]
    if aggregate == charts.COUNT:
        return sql.SQL("count(*)")

    column = sql.Identifier(config["y_field"])
    if aggregate == "count_distinct" and y_type in TEXT_TYPES:
        column = _no_blank(column)
    return AGGREGATE_SQL[aggregate].format(column=column)


def _group_statement(resource_id, config, fields):
    """The grouped read, capped at one row past the contract's group cap.

    `GROUP BY 1 ... ORDER BY 1` groups and orders by the key expression itself,
    so a key is ordered by what it is — a number as a number, a truncated
    timestamp as a timestamp — and not by how it will be labelled. `NULLS LAST`
    is the contract's "el null al final".

    The column types are read here, not guessed: whether a column is text is
    what decides if a blank string is data.
    """
    x_type = fields.get(config["x_field"])
    y_type = fields.get(config.get("y_field"))

    where = sql.SQL("")
    if not config["include_null"]:
        where = sql.SQL(" WHERE {value} IS NOT NULL").format(
            value=_x_values(config, x_type)
        )

    return sql.SQL(
        "SELECT {key}, {value} FROM {table}{where}"
        " GROUP BY 1 ORDER BY 1 ASC NULLS LAST LIMIT {limit}"
    ).format(
        key=_key_expression(config, x_type),
        value=_value_expression(config, y_type),
        # The DataStore names a resource's table after the resource id.
        table=sql.Identifier(resource_id),
        where=where,
        limit=sql.Literal(GROUP_CAP + 1),
    )


def _parameters(config):
    if config["x_bucket"] == charts.NUMBER_STEP:
        return {"step": decimal.Decimal(str(config["x_step"]))}
    return {}


def _timeout_ms():
    """The cap on the query, in milliseconds, and never off.

    The contract's cap cannot be disabled from configuration (O3 finding 1):
    `statement_timeout = 0` means "no limit" to Postgres, a negative value is a
    server error (`InvalidParameterValue`, SQLSTATE 22023) and a non-numeric one
    is a `ValueError` here — a 500 for what is a bad setting. So the setting is
    read as "an integer >= 1, or the default", and a value that cannot be a cap
    falls back to the default and says so once per request.
    """
    configured = toolkit.config.get(TIMEOUT_SETTING, DEFAULT_TIMEOUT_MS)
    try:
        timeout_ms = int(str(configured).strip())
    except (TypeError, ValueError):
        timeout_ms = 0
    if timeout_ms >= 1:
        return timeout_ms

    log.warning(
        "%s is %r, which is not an integer >= 1: the query cap stays at %d ms",
        TIMEOUT_SETTING, configured, DEFAULT_TIMEOUT_MS,
    )
    return DEFAULT_TIMEOUT_MS


def _read(resource_id, statement, parameters):
    """Run the grouped read and the row count on the DataStore's read engine.

    One transaction, so `SET LOCAL` covers both statements and neither can see
    a table another connection is halfway through writing.

    The read engine is the `datastore_ro` role: this action reads, and giving
    it the write engine would make a query bug a write bug. `statement_timeout`
    is the contract's cap; a cancelled statement is `57014` and answers a
    `ValidationError` keyed `query`, because it is the request that could not
    be answered, not a server failure.

    The statements are dialected to the driver directly (a psycopg2 cursor on
    the connection SQLAlchemy opened) because the composition is
    `psycopg2.sql`'s: `as_string` and `%(step)s` are psycopg2's spellings of a
    query, and SQLAlchemy's own text layer speaks a different one.
    """
    total_statement = sql.SQL("SELECT count(*) FROM {table}").format(
        table=sql.Identifier(resource_id)
    )
    timeout_ms = _timeout_ms()
    # `SET` takes a literal and never a bind parameter. It is composed as a
    # `Literal` over an `int`, so what reaches the server is a number.
    timeout_statement = sql.SQL("SET LOCAL statement_timeout = {ms}").format(
        ms=sql.Literal(int(timeout_ms))
    )

    try:
        with get_read_engine().begin() as connection:
            driver = connection.connection.driver_connection
            with driver.cursor() as cursor:
                cursor.execute(timeout_statement)
                cursor.execute(statement, parameters)
                rows = cursor.fetchall()
                cursor.execute(total_statement)
                total_rows = cursor.fetchone()[0]
    except psycopg2.Error as error:
        if error.pgcode == QUERY_CANCELED:
            raise toolkit.ValidationError({
                "query": [toolkit._(
                    "The query took longer than %(ms)s ms and was cancelled"
                ) % {"ms": timeout_ms}],
            })
        raise

    return rows, total_rows


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def _json_value(value):
    """A number as JSON can carry it: an `int` when it is integral, a `float`
    when it is not, and a non-finite number as `null` (O3 finding 2).

    `sum`/`avg`/`min`/`max` over a `numeric` come back as `Decimal`, which no
    JSON encoder knows; `count` and the integer columns come back as `int`
    already and are left alone. `NaN` and `+/-Infinity` are numbers JSON has no
    spelling for — `Decimal('Infinity')` used to raise `OverflowError` in
    `int(...)`, and a `float8` column carries both — so they become `null`,
    which is the only "no value" JSON has.
    """
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _json_key(config, value):
    """The group's key, as the contract spells it: the value itself, the lower
    bound of the range, `"2024"`, `"2024-Q1"`, `"2024-01"`, an ISO timestamp,
    or `null` — and the portal does the labelling."""
    if value is None:
        return None
    formatter = DATE_KEY_FORMATS.get(config["x_bucket"])
    if formatter is not None:
        return formatter(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return _json_value(value)


def _answer(resource_id, config, fields):
    statement = _group_statement(resource_id, config, fields)
    rows, total_rows = _read(resource_id, statement, _parameters(config))

    truncated = len(rows) > GROUP_CAP
    if truncated:
        rows = rows[:GROUP_CAP]

    return {
        "version": charts.CONTRACT_VERSION,
        "config": config,
        "groups": [
            {"key": _json_key(config, row[0]), "value": _json_value(row[1])}
            for row in rows
        ],
        "truncated": truncated,
        # The table's rows, not the rows that reached a group.
        "total_rows": total_rows,
    }


# ---------------------------------------------------------------------------
# The action
# ---------------------------------------------------------------------------


def umss_chart_data(context, data_dict):
    """Group a DataStore table the way a `umss_chart` view says to.

    :param view_id: the id of a saved `umss_chart` resource view (visitor form)
    :param resource_id: the resource to group (editor form, with `config`)
    :param config: a chart configuration, validated but not saved (editor form)
    """
    data_dict = data_dict or {}
    form = _call_form(data_dict)

    # One auth function reads the same two forms and delegates to
    # `resource_view_show` or `resource_update`; see `logic/auth/chart.py`.
    toolkit.check_access("umss_chart_data", context, data_dict)

    if form == "view":
        config, resource_id = _saved_chart(data_dict["view_id"])
    else:
        resource_id = data_dict["resource_id"]
        config = _draft_chart(resource_id, data_dict.get("config"))

    fields = _datastore_fields(resource_id)
    errors = _datastore_errors(config, fields)
    if errors:
        raise toolkit.ValidationError(errors)

    return _answer(resource_id, config, fields)
