"""
The `umss_chart` resource view's configuration (contract v1, owned by the
portal: `odp/odd/tasks/csv-charts.md`, section "El contrato entre el portal y
`ckanext-umss`").

The configuration is flat and scalar on purpose. `resource_view_create` stores
every key that is not a `resource_view` column in the view's `config`, but
only after validating the call against the view plugin's `schema`, and the
default view schema refuses anything undeclared (`'__extras': [empty]`,
`ckan/logic/schema/__init__.py:752`). So every key of the contract is declared
here, and this schema is the single definition of what a saved chart holds.
"""

import decimal
import math

import ckan.plugins.toolkit as toolkit
from ckan.lib.navl.dictization_functions import missing

VIEW_TYPE = "umss_chart"
CONTRACT_VERSION = 1

CHART_TYPES = ("bar", "line", "pie")
X_BUCKETS = ("value", "number_step", "year", "quarter", "month")
Y_AGGS = ("count", "count_distinct", "sum", "avg", "min", "max")

#: The two keys whose per-key rules cannot say what the contract says: whether
#: they are required is a fact about another key, so the rules that read both
#: live in `cross_key_rules` below.
X_STEP = "x_step"
Y_FIELD = "y_field"

#: The bucket whose ranges the step sizes.
NUMBER_STEP = "number_step"

#: The aggregate that counts rows, and so takes no `y_field`.
COUNT = "count"

#: No field whose name starts with this is charted. It covers `_id`, the
#: DataStore's own row key, and `_full_text`, which the backend keeps for
#: search — internal columns, not columns a publisher chose (L12).
INTERNAL_PREFIX = "_"

#: A value that is not a value. `missing` is navl's sentinel for a key the
#: caller did not send, and a key that failed its own validators is just as
#: undecided: `_settled` answers this for both, so the cross-key rules never
#: judge a key twice.
_UNDECIDED = object()


def positive_number(value):
    """A number greater than 0, returned as an `int` when it is integral and
    as a `float` otherwise, so a form string `"5"` is stored as `5`."""
    if isinstance(value, bool):
        raise toolkit.Invalid(toolkit._("Must be a number greater than 0"))
    try:
        number = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        raise toolkit.Invalid(toolkit._("Must be a number greater than 0"))
    if not number.is_finite() or number <= 0 or not math.isfinite(float(number)):
        raise toolkit.Invalid(toolkit._("Must be a number greater than 0"))
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _settled(data, errors, name):
    """The converted value of a declared key, or `_UNDECIDED`.

    A key the caller left out is not a value, and neither is a key that failed
    its own validators — that key already has its error, and a second one from
    a rule that never saw a value would only name the blame twice.
    """
    if errors.get((name,)):
        return _UNDECIDED
    value = data.get((name,))
    if value is missing or value is None:
        return _UNDECIDED
    return value


def _present(data, name):
    """Whether the key carries anything at all.

    Deliberately ignorant of the key's own validators: `x_step` with a bad
    value has an error already, and a cross-key rule that said "a number_step
    bucket needs a step" on top of it would describe the request wrongly. A
    key whose value failed is present, and its message is its own.
    """
    return (name,) in data and data.get((name,)) is not missing


def _fail(errors, name, message):
    """Key an error on the culprit, the way the contract asks: a bad pair is
    reported on the key whose value is wrong, never on the pair."""
    errors.setdefault((name,), []).append(message)


def cross_key_rules(key, data, errors, context):
    """The contract's rules that span keys, as the schema's `__after` hook.

    navl runs `__after` validators with the whole converted dict, which is what
    makes them usable here and why they are not per-key validators. Being part
    of `view_config_schema()` means the same rules run at save time
    (`resource_view_create`/`_update` merge this schema) and on the editor call
    to `umss_chart_data`, so a saved view and a live preview can never disagree
    about what a valid configuration is.

    These are the **shape** rules only. Whether a named column exists in the
    table, and whether its type is the one the bucket needs, cannot be answered
    without the DataStore, and saving a view must not query it.

    What is here, and why each one cannot be a per-key rule:

    * `x_step` is required with `number_step` and forbidden with every other
      bucket;
    * `y_field` is forbidden with `count` and required with every other
      aggregate;
    * neither `x_field` nor `y_field` may name an internal `_`-column (L12).
      That one reads a single key, but it is a vocabulary rule about what a
      field **is**, so it belongs with the others rather than in one of the
      field's own validators.
    """
    x_bucket = _settled(data, errors, "x_bucket")
    if x_bucket is not _UNDECIDED:
        step = _present(data, X_STEP)
        if x_bucket == NUMBER_STEP and not step:
            _fail(errors, X_STEP, toolkit._(
                "A number_step bucket needs a step"))
        elif x_bucket != NUMBER_STEP and step:
            _fail(errors, X_STEP, toolkit._(
                "A step belongs to the number_step bucket only"))

    y_agg = _settled(data, errors, "y_agg")
    if y_agg is not _UNDECIDED:
        y_field = _present(data, Y_FIELD)
        if y_agg == COUNT and y_field:
            _fail(errors, Y_FIELD, toolkit._(
                "Counting rows takes no field to count"))
        elif y_agg != COUNT and not y_field:
            _fail(errors, Y_FIELD, toolkit._(
                "The %s aggregate needs a field to aggregate") % y_agg)

    for name in ("x_field", Y_FIELD):
        value = _settled(data, errors, name)
        if value is not _UNDECIDED and value.startswith(INTERNAL_PREFIX):
            _fail(errors, name, toolkit._(
                "%(name)s is the DataStore's own column, not a field to chart"
            ) % {"name": value})


def view_config_schema():
    """The contract's configuration table: the per-key rules, plus the rules
    that no single key can carry (`cross_key_rules`)."""
    get = toolkit.get_validator
    not_empty = get("not_empty")
    ignore_missing = get("ignore_missing")
    unicode_safe = get("unicode_safe")
    boolean_validator = get("boolean_validator")
    one_of = get("one_of")
    default = get("default")
    return {
        "version": [not_empty, get("int_validator"), one_of([CONTRACT_VERSION])],
        "chart_type": [not_empty, unicode_safe, one_of(list(CHART_TYPES))],
        "x_field": [not_empty, unicode_safe],
        "x_bucket": [not_empty, unicode_safe, one_of(list(X_BUCKETS))],
        "x_step": [ignore_missing, positive_number],
        "x_fold_case": [default(False), boolean_validator],
        "y_agg": [not_empty, unicode_safe, one_of(list(Y_AGGS))],
        "y_field": [ignore_missing, unicode_safe],
        "include_null": [default(True), boolean_validator],
        "__after": [cross_key_rules],
    }


def validate_config(config):
    """A chart configuration, normalized, or a `ValidationError` keyed on every
    culprit.

    This is the editor path's way through the same schema a saved view goes
    through (`resource_view_create` merges `view_config_schema()` into the
    default view schema), so the two paths cannot drift. One difference: the
    save path leans on CKAN's own `'__extras': [empty]` to refuse an undeclared
    key, and this call is not going through that schema, so an undeclared key is
    refused here — under `config`, one message per key (L18). `config` is the
    only key the editor's object may be reported on: a consumer matching on the
    error key must not have to know a key the caller invented.
    """
    if not isinstance(config, dict):
        raise toolkit.ValidationError({
            "config": [toolkit._("The configuration must be an object")],
        })

    schema = view_config_schema()
    declared = {name for name in schema if not name.startswith("__")}
    undeclared = sorted(set(config) - declared)
    if undeclared:
        raise toolkit.ValidationError({
            "config": [
                toolkit._("Unexpected key: %s") % name for name in undeclared
            ],
        })

    data, errors = toolkit.navl_validate(dict(config), schema, {"ignore_auth": True})
    if errors:
        raise toolkit.ValidationError(errors)
    return data
