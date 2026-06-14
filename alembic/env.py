"""Alembic environment (addendum §4).

Reads the connection string from ``DATABASE_URL`` (Render Postgres in prod) and uses the ORM models'
metadata as the autogenerate target, so migrations are generated from ``apps/api/db/models.py``.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

# Import models so every table registers on Base.metadata before autogenerate.
from apps.api.db import models  # noqa: F401
from apps.api.db.base import Base
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Env var wins over the alembic.ini default (which is only for local autogenerate).
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
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
