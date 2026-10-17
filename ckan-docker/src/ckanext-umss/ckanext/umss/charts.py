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

VIEW_TYPE = "umss_chart"
CONTRACT_VERSION = 1

CHART_TYPES = ("bar", "line", "pie")
X_BUCKETS = ("value", "number_step", "year", "quarter", "month")
Y_AGGS = ("count", "count_distinct", "sum", "avg", "min", "max")


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


def view_config_schema():
    """The per-key rules of the contract's configuration table."""
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
    }
