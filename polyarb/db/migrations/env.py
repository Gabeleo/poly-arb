"""Alembic environment configuration.

Reads DATABASE_URL via the engine factory so that ``alembic upgrade head``
works against whatever database is configured — SQLite locally, PostgreSQL
in production.
"""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from polyarb.db.models import metadata

config = context.config

# Use DATABASE_URL from environment if set; otherwise keep the URL from alembic.ini / caller
if "DATABASE_URL" in os.environ:
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

target_metadata = metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connect to the database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
