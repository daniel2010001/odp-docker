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
