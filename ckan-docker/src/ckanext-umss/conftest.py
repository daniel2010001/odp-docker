"""
Guard this extension's test session against non-test targets.

`ckanext.umss.tests.target_guard` refuses to run when the effective CKAN
configuration points the suite at a development database or at a Solr core a
running stack serves. Checking at session start covers what
`ckan-docker/bin/test-umss` cannot: someone typing `pytest` directly, which is
how this project destroyed its development database once already.

This hook runs before CKAN's own `pytest_sessionstart`, which calls `make_app()`
and opens the configured database in the process, and therefore long before
`clean_db` drops every table in it.
"""

import os

import pytest


def pytest_sessionstart(session):
    # `ckan.common.config` is unusable here: CKAN fills it in its own session
    # start, which runs after this hook. Resolve the configuration the same way
    # `update_config()` would. A missing or broken ini raises the same error
    # CKAN's own session start raises a moment later.
    from ckan.cli import load_config

    from ckanext.umss.tests.target_guard import unsafe_targets, with_env_overrides

    configured = load_config(session.config.option.ckan_ini)
    problems = unsafe_targets(with_env_overrides(configured, os.environ))
    if problems:
        pytest.exit(_refusal(problems), returncode=pytest.ExitCode.USAGE_ERROR)


def _refusal(problems):
    listed = "\n".join(f"  {problem}" for problem in problems)
    return (
        "Refusing to run: this configuration does not point the suite at test-only targets.\n"
        f"{listed}\n"
        "\n"
        "`clean_db` drops every table in the configured database, and the suite indexes into\n"
        "the Solr core it is pointed at. Inside `ckan-dev` the container's `CKAN_*` variables\n"
        "override `test.ini` (`ckan/config/environment.py`, `CONFIG_FROM_ENV_VARS`), so the\n"
        "ini alone does not protect the development database.\n"
        "\n"
        "Run the suite through the repository runner instead:\n"
        "  ckan-docker/bin/test-umss [pytest arguments...]\n"
    )
