"""
Tests for ``target_guard``: the collection-time refusal that keeps this suite off
anything that is not a test-scoped database or Solr core.

The unit tests below cover the predicate. One test covers the wiring: a predicate
nobody calls protects nothing, so it starts a real pytest session (in a
subprocess) against a non-test database and requires that session to abort.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ckanext.umss.tests.target_guard import (
    _database_name,
    unsafe_targets,
    with_env_overrides,
)

TEST_DB = "postgresql://ckandbuser:ckandbpassword@db/ckan_test"
TEST_DATASTORE_WRITE = "postgresql://ckandbuser:ckandbpassword@db/datastore_test"
TEST_DATASTORE_READ = "postgresql://datastore_ro:datastore@db/datastore_test"
TEST_SOLR = "http://solr:8983/solr/ckan_test"

# Root of the extension checkout, so the wiring test can invoke the same
# `pytest --ckan-ini=test.ini` an operator would type.
EXTENSION_ROOT = Path(__file__).resolve().parents[3]


def config(**overrides):
    settings = {
        "sqlalchemy.url": TEST_DB,
        "ckan.datastore.write_url": TEST_DATASTORE_WRITE,
        "ckan.datastore.read_url": TEST_DATASTORE_READ,
        "solr_url": TEST_SOLR,
    }
    settings.update(overrides)
    return settings


def test_a_test_scoped_configuration_is_accepted():
    assert unsafe_targets(config()) == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("sqlalchemy.url", "postgresql://ckandbuser:ckandbpassword@db/ckandb"),
        (
            "ckan.datastore.write_url",
            "postgresql://ckandbuser:ckandbpassword@db/datastore",
        ),
        ("ckan.datastore.read_url", "postgresql://datastore_ro:datastore@db/datastore"),
        ("solr_url", "http://solr:8983/solr/ckan"),
    ],
)
def test_a_dev_target_is_rejected(key, value):
    problems = unsafe_targets(config(**{key: value}))
    assert len(problems) == 1
    assert key in problems[0]


@pytest.mark.parametrize(
    "key,value",
    [
        (
            "sqlalchemy.url",
            "postgresql://ckandbuser:ckandbpassword@db/ckandb?name=ckan_test",
        ),
        ("solr_url", "http://solr:8983/solr/ckan?name=ckan_test"),
    ],
)
def test_a_test_name_hidden_in_the_query_string_does_not_pass(key, value):
    """`_test` anywhere in the string is not the same as a test target.

    A string-containment guard accepted `...@db/ckandb?application_name=ckan_test`
    and let a run reach the development database. The guard asks the driver which
    database the URL resolves to, so the trick cannot work.
    """
    assert len(unsafe_targets(config(**{key: value}))) == 1


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_database_url_is_rejected(value):
    """Unknown is not safe: an unset database URL must fail closed."""
    problems = unsafe_targets(config(**{"sqlalchemy.url": value}))
    assert len(problems) == 1
    assert "sqlalchemy.url" in problems[0]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_solr_url_is_rejected(value):
    assert len(unsafe_targets(config(**{"solr_url": value}))) == 1


def test_a_database_url_without_a_database_name_is_rejected():
    problems = unsafe_targets(
        config(**{"sqlalchemy.url": "postgresql://ckandbuser@db"})
    )
    assert len(problems) == 1


def test_datastore_urls_may_be_absent():
    """The suite does not enable `datastore`, so an unset datastore is not a hazard.

    A datastore URL that *is* configured still has to be test-scoped: it becomes a
    hazard the day a test enables the plugin.
    """
    settings = config()
    del settings["ckan.datastore.write_url"]
    del settings["ckan.datastore.read_url"]
    assert unsafe_targets(settings) == []


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://ckandbuser:total-secret@db/ckandb",
        "postgresql://ckandbuser:total%2Dsecret@db/ckandb",
        "postgresql://ckandbuser:p@ss@db/ckandb",
        "http://pusher:total-secret@solr:8983/solr/ckan",
        "ckandbuser:total-secret@db/ckandb",
    ],
)
def test_the_refusal_never_prints_the_url_or_its_password(url):
    """The strongest invariant available: no URL reaches the refusal at all.

    Redacting a URL is not safe in general. `make_url` splits
    `postgresql://u:p@ss@db/ckandb` at the first `@`, so the rest of the password
    is part of the *host* and SQLAlchemy's own `hide_password=True` rendering
    prints it. The refusal reports the resolved name instead, and this test pins
    that no `://` can appear.
    """
    problems = unsafe_targets(config(**{"sqlalchemy.url": url, "solr_url": url}))
    report = " ".join(problems)
    assert "total-secret" not in report
    assert "total%2Dsecret" not in report
    assert "p@ss" not in report
    assert "://" not in report


def test_any_failure_to_translate_a_url_fails_closed(monkeypatch):
    """The guard refuses; it never crashes the session it protects.

    An unknown driver is already an `ArgumentError`, so it falls through the
    `except`. This pins the wider contract: whatever the driver machinery raises,
    the answer is an empty name and a refusal line rather than an exception.
    """
    import ckanext.umss.tests.target_guard as guard

    def explode(value):
        raise RuntimeError("driver machinery exploded")

    monkeypatch.setattr(guard, "make_url", explode)
    url = "postgresql://ckandbuser:ckandbpassword@db/ckan_test"
    assert guard._database_name(url) == ""
    problems = guard.unsafe_targets(config())
    assert [problem.split(":")[0] for problem in problems] == [
        "sqlalchemy.url",
        "ckan.datastore.write_url",
        "ckan.datastore.read_url",
    ]


def test_the_guard_is_wired_into_collection():
    """A real pytest session must abort before it can reach the database.

    The probe URL deliberately points at a database that does not exist rather
    than at the development one. If this guard ever regresses, the point of this
    test is that the run continues — and a continuing run must not be able to
    drop tables in a real database as a side effect of the test failing.
    """
    env = dict(
        os.environ,
        CKAN_SQLALCHEMY_URL="postgresql://ckandbuser:ckandbpassword@db/guard_probe",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--ckan-ini=test.ini",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=EXTENSION_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "sqlalchemy.url" in output, output
    assert "guard_probe" in output, output
    assert "bin/test-umss" in output, output


def test_the_environment_wins_over_the_ini():
    """This is the incident in one assertion.

    `test.ini` asks for `ckan_test`; inside `ckan-dev` the container's
    `CKAN_SQLALCHEMY_URL` and `CKAN_SOLR_URL` say otherwise, and the run has to
    be refused.
    """
    effective = with_env_overrides(
        config(),
        {
            "CKAN_SQLALCHEMY_URL": "postgresql://ckandbuser:ckandbpassword@db/ckandb",
            "CKAN_SOLR_URL": "http://solr:8983/solr/ckan",
        },
    )
    problems = unsafe_targets(effective)
    assert [problem.split(":")[0] for problem in problems] == [
        "sqlalchemy.url",
        "solr_url",
    ]


def test_an_empty_environment_variable_does_not_override():
    """`update_config()` only overrides on a truthy value."""
    effective = with_env_overrides(config(), {"CKAN_SQLALCHEMY_URL": ""})
    assert effective["sqlalchemy.url"] == TEST_DB


def test_settings_without_an_environment_variable_keep_their_ini_value():
    assert with_env_overrides(config(), {}) == config()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://ckandbuser:ckandbpassword@db/ckan_test?database=ckandb",
        "postgresql://ckandbuser:ckandbpassword@db/ckan_test?dbname=ckandb",
    ],
)
def test_a_query_parameter_cannot_redirect_the_database(url):
    """The measured bypass: the path said `ckan_test`, the connection opened `ckandb`.

    Under psycopg2, `...?database=ckandb` connected to `ckandb`, and
    `...?dbname=ckandb` left two spellings of the setting, which psycopg2 rejects.
    Neither may pass: `clean_db` drops every table in the database that is really
    opened, not in the one the path names.
    """
    problems = unsafe_targets(config(**{"sqlalchemy.url": url}))
    assert len(problems) == 1
    assert "sqlalchemy.url" in problems[0]


def test_an_innocent_query_parameter_is_accepted():
    """`application_name` is a real libpq parameter and redirects nothing."""
    url = "postgresql://ckandbuser:ckandbpassword@db/ckan_test?application_name=umss"
    assert unsafe_targets(config(**{"sqlalchemy.url": url})) == []


def test_the_database_name_comes_from_the_driver_translation():
    """White-box: the value below is the database `clean_db` would drop tables in."""
    assert _database_name("postgresql://u:p@db/ckan_test") == "ckan_test"
    assert _database_name("postgresql://u:p@db/ckan_test?database=ckandb") == "ckandb"
    assert _database_name("postgresql://u:p@db/ckan_test?dbname=ckandb") == ""
