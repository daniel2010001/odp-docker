"""
Alembic environment for `ckanext-umss`, copied from CKAN's own example
(`ckanext/example_database_migrations/migration/example_database_migrations/env.py`).

The one line that matters for coexistence: `version_table` is derived from this
file's parent directory, so the extension keeps its own `umss_alembic_version`
table and never touches the core `alembic_version`.

`target_metadata` stays `None`, exactly as in CKAN's example: autogenerate is
not used here, and the DDL of `versions/0001_add_publication_requests.py` is
mirrored by hand against `ckanext/umss/model.py`. What keeps the two honest is
not tooling but the store tests, which run against the table the *migration*
created (Phase A1.4).
"""
from __future__ import with_statement

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

target_metadata = None

name = os.path.basename(os.path.dirname(__file__))


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though an
    Engine is acceptable here as well. By skipping the Engine creation we don't
    even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the script output.
    """
    url = config.get_main_option(u"sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=u'{}_alembic_version'.format(name),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a connection
    with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix=u"sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=u"{}_alembic_version".format(name),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
