"""
The `umss_chart` resource view: a chart configuration the publisher saves and
the visitor only reads (contract v1, `odp/odd/tasks/csv-charts.md`).

What these tests pin is the round trip, because that is what failed when this
was first attempted on CKAN 2.10: every config key handed to
`resource_view_create` comes back from `resource_view_show`. CKAN 2.12 stores
every key that is not a `resource_view` column in `config`
(`ckan/lib/dictization/model_save.py:650-662`) and merges `config` back into
the top level of the dictized view (`ckan/lib/dictization/model_dictize.py:684-685`),
so the keys are read beside `title`, not under a `config` key.

They also pin the **cross-key** shape rules (`x_step` against `x_bucket`,
`y_field` against `y_agg`, and no `_`-prefixed column on either axis) at save
time. Those run as the schema's `__after` hook, so the save path and the
`umss_chart_data` editor path share one definition of a valid configuration.
The DataStore rules — does the column exist, is its type the one the bucket
needs — are **not** here: saving a view must not query the DataStore, and
`test_chart_data.py` owns them.
"""

import pytest

import ckan.plugins.toolkit as toolkit
from ckan.tests import factories, helpers

pytestmark = [
    pytest.mark.ckan_config("ckan.plugins", "umss"),
    pytest.mark.usefixtures("clean_db", "with_plugins"),
]

FULL_CONFIG = {
    "version": 1,
    "chart_type": "bar",
    "x_field": "turno",
    "x_bucket": "number_step",
    "x_step": 5,
    "x_fold_case": False,
    "y_agg": "sum",
    "y_field": "salario",
    "include_null": False,
}


def _create_view(**config):
    resource = factories.Resource()
    return helpers.call_action(
        "resource_view_create",
        resource_id=resource["id"],
        view_type="umss_chart",
        title="Salario por turno",
        description="La suma del salario de cada turno",
        **config,
    )


def test_every_config_key_survives_create_and_show():
    created = _create_view(**FULL_CONFIG)

    shown = helpers.call_action("resource_view_show", id=created["id"])

    assert shown["view_type"] == "umss_chart"
    assert shown["title"] == "Salario por turno"
    assert shown["description"] == "La suma del salario de cada turno"
    assert {key: shown.get(key) for key in FULL_CONFIG} == FULL_CONFIG


def test_the_view_is_listed_for_its_resource_with_its_config():
    created = _create_view(**FULL_CONFIG)

    listed = helpers.call_action("resource_view_list", id=created["resource_id"])

    assert [view["id"] for view in listed] == [created["id"]]
    assert {key: listed[0].get(key) for key in FULL_CONFIG} == FULL_CONFIG


def test_omitted_flags_are_saved_with_their_defaults():
    config = {key: value for key, value in FULL_CONFIG.items()
              if key not in ("x_fold_case", "include_null")}

    created = _create_view(**config)
    shown = helpers.call_action("resource_view_show", id=created["id"])

    assert shown["x_fold_case"] is False
    assert shown["include_null"] is True


def test_form_strings_are_stored_as_their_typed_values():
    """The API also takes form-encoded strings. What is stored is typed, so a
    reader never has to guess whether `"false"` means false."""
    created = _create_view(**{
        **FULL_CONFIG,
        "version": "1",
        "x_step": "2.5",
        "x_fold_case": "false",
        "include_null": "true",
    })
    shown = helpers.call_action("resource_view_show", id=created["id"])

    assert shown["version"] == 1
    assert shown["x_step"] == 2.5
    assert shown["x_fold_case"] is False
    assert shown["include_null"] is True


def test_an_undeclared_key_is_refused_not_dropped():
    """The hypothesis for the 2.10 failure was a silent drop. On 2.12 an
    undeclared key is a validation error, and nothing is saved."""
    resource = factories.Resource()

    with pytest.raises(toolkit.ValidationError) as caught:
        helpers.call_action(
            "resource_view_create",
            resource_id=resource["id"],
            view_type="umss_chart",
            title="t",
            **FULL_CONFIG,
            colour="red",
        )

    assert "__extras" in caught.value.error_dict
    assert helpers.call_action("resource_view_list", id=resource["id"]) == []


# ---------------------------------------------------------------------------
# The cross-key shape rules, enforced on save by the schema's `__after` hook
#
# The per-key rules of the contract's table cannot express these: whether
# `x_step` is required depends on `x_bucket`, and whether `y_field` is required
# depends on `y_agg`. They run at save time so a stored view can never carry a
# configuration `umss_chart_data` would refuse, and the key the error lands on
# is the culprit, never the pair.
# ---------------------------------------------------------------------------


def _refusal(**config):
    """The error dict of a view the shape rules refuse, and the proof that
    nothing was saved: a half-validated view must not exist."""
    resource = factories.Resource()

    with pytest.raises(toolkit.ValidationError) as caught:
        helpers.call_action(
            "resource_view_create",
            resource_id=resource["id"],
            view_type="umss_chart",
            title="t",
            **config,
        )

    assert helpers.call_action("resource_view_list", id=resource["id"]) == []
    return caught.value.error_dict


def test_number_step_requires_a_step():
    config = {key: value for key, value in FULL_CONFIG.items() if key != "x_step"}

    assert "x_step" in _refusal(**config)


def test_a_step_is_forbidden_without_number_step():
    """`x_step` is meaningful only for `number_step`; stored with any other
    bucket it is a configuration the reader would have to guess about."""
    assert "x_step" in _refusal(**{**FULL_CONFIG, "x_bucket": "value"})


def test_count_forbids_a_y_field():
    """`count` counts rows: a `y_field` beside it would silently change what
    the publisher asked for."""
    assert "y_field" in _refusal(**{**FULL_CONFIG, "y_agg": "count"})


def test_every_other_aggregate_requires_a_y_field():
    config = {key: value for key, value in FULL_CONFIG.items() if key != "y_field"}

    for aggregate in ("count_distinct", "sum", "avg", "min", "max"):
        assert "y_field" in _refusal(**{**config, "y_agg": aggregate}), aggregate


def test_count_without_a_y_field_is_saved():
    """The other half of the rule: the shape it forbids is the one it must also
    allow, or the rule is only refusing."""
    config = {key: value for key, value in FULL_CONFIG.items() if key != "y_field"}

    created = _create_view(**{**config, "y_agg": "count"})
    shown = helpers.call_action("resource_view_show", id=created["id"])

    assert shown["y_agg"] == "count"
    assert "y_field" not in shown or shown["y_field"] in (None, "")


def test_x_bucket_is_required():
    """L12: `x_bucket` is required and has no default. There is no sensible
    one — a chart of raw values and a chart of yearly sums are different
    charts — so a view saved without it would be a view nobody can draw."""
    config = {key: value for key, value in FULL_CONFIG.items() if key != "x_bucket"}

    assert "x_bucket" in _refusal(**config)


def test_the_internal_id_is_never_charted():
    """L12: `_id` is the DataStore's row key and `_full_text` its search
    column; neither is a field a publisher chose, so neither can be an axis.
    The error lands on the key that named it, not on the pair."""
    assert "x_field" in _refusal(**{**FULL_CONFIG, "x_field": "_id"})
    assert "y_field" in _refusal(**{**FULL_CONFIG, "y_field": "_id"})
    assert "y_field" in _refusal(**{**FULL_CONFIG, "y_field": "_full_text"})
