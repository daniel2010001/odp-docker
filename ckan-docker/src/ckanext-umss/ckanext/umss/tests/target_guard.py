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

The database is read from the driver, not from the URL path. ``make_url()``
answers with the path segment, and a query parameter can override it: measured
against psycopg2 in this stack, ``.../db/ckan_test?database=ckandb`` opens
``ckandb``. ``create_connect_args()`` is what the driver itself resolves, so
asking it is the only way to know which database a run will really drop.
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
    """The database the driver will open, as the driver itself resolves it.

    The URL path is not the answer: ``create_connect_args()`` merges the URL
    query over it, so ``.../db/ckan_test?database=ckandb`` opens ``ckandb``. Two
    spellings of the same setting are not an answer either: psycopg2 rejects the
    pair, and a driver that tolerated it would pick one of them. An unknown
    driver, an unparseable URL and both cases above yield an empty name, and an
    empty name fails the check.
    """
    try:
        url = make_url(value)
        _, connect_args = url.get_dialect()().create_connect_args(url)
    except ArgumentError:
        return ""
    spelled = {connect_args.get("database"), connect_args.get("dbname")}
    spelled.discard(None)
    if len(spelled) != 1:
        return ""
    return spelled.pop()


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
