"""
Refuse to run this suite against anything that is not test-scoped.

``clean_db`` does not truncate: it reflects the live schema and drops every
table (``ckan/model/__init__.py``, ``Repository.clean_db``). Pointed at a
development database it therefore destroys it, schema included.

Inside ``ckan-dev`` the container's ``CKAN_*`` variables win over any ini,
because ``update_config()`` applies ``CONFIG_FROM_ENV_VARS`` after the ini
(``ckan/config/environment.py``). ``ckan-docker/bin/test-umss`` compensates for
that by deriving test URLs and exporting them over the container's own values;
this module is what protects every other way of starting pytest.

The checks read parsed URLs, not strings. A guard that looks for ``_test``
anywhere in the value accepts ``...@db/ckandb?application_name=ckan_test`` and
lets the run reach the development database.
"""

import re
from typing import Any, Mapping

from ckan.config.environment import CONFIG_FROM_ENV_VARS
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Settings holding a database URL, and whether an unset value is unsafe.
# Without `sqlalchemy.url` there is no known-safe answer. The datastore URLs are
# unused unless a test enables the `datastore` plugin, but a configured one is
# still checked: it becomes a hazard the day a test does enable it.
DATABASE_SETTINGS = (
    ("sqlalchemy.url", True),
    ("ckan.datastore.write_url", False),
    ("ckan.datastore.read_url", False),
)

SOLR_SETTING = "solr_url"

# Suffix every test-scoped database name and Solr core carries.
TEST_SUFFIX = "_test"

_NOT_SET = "not set"


def unsafe_targets(config: Mapping[str, Any]) -> list[str]:
    """Describe every configured target that is not test-scoped.

    Returns an empty list when the whole configuration is safe. Each message
    names the setting and the offending value with the password redacted: this
    output reaches terminals and CI logs, so it must never carry a credential.
    """
    problems = []

    for setting, mandatory in DATABASE_SETTINGS:
        value = _clean(config.get(setting))
        if not value:
            if mandatory:
                problems.append(f"{setting} = {_NOT_SET}")
            continue
        if not _database_name(value).endswith(TEST_SUFFIX):
            problems.append(f"{setting} = {_redact(value)}")

    solr = _clean(config.get(SOLR_SETTING))
    if not solr:
        problems.append(f"{SOLR_SETTING} = {_NOT_SET}")
    elif not _last_path_segment(solr).endswith(TEST_SUFFIX):
        problems.append(f"{SOLR_SETTING} = {_redact(solr)}")

    return problems


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _without_query(value: str) -> str:
    return re.split(r"[?#]", value, maxsplit=1)[0]


def _last_path_segment(value: str) -> str:
    return _without_query(value).rstrip("/").rsplit("/", 1)[-1]


def _database_name(value: str) -> str:
    """The database a URL points at, read from its path.

    Parsing is what keeps a query string from masquerading as a test target. An
    unparseable URL, or one with no database at all, yields an empty name and
    therefore fails the check.
    """
    try:
        return make_url(_without_query(value)).database or ""
    except ArgumentError:
        return ""


def _redact(value: str) -> str:
    try:
        return make_url(value).render_as_string(hide_password=True)
    except ArgumentError:
        return re.sub(r"://([^/@:]+):[^/@]*@", r"://\1:***@", value)


def with_env_overrides(
    settings: Mapping[str, Any], environ: Mapping[str, str]
) -> dict[str, Any]:
    """The configuration CKAN will really run with.

    ``update_config()`` applies ``CONFIG_FROM_ENV_VARS`` after the ini, so inside
    ``ckan-dev`` the container's ``CKAN_*`` variables win over ``test.ini``.
    Reusing CKAN's own mapping keeps this from drifting; an empty value does not
    override, matching the ``if from_env:`` test in ``update_config()``.
    """
    effective = dict(settings)
    for setting, env_var in CONFIG_FROM_ENV_VARS.items():
        from_env = environ.get(env_var)
        if from_env:
            effective[setting] = from_env
    return effective
