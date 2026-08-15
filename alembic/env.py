"""Alembic environment (addendum §4).

Reads the connection string from ``DATABASE_URL`` (Supabase/Render Postgres in prod) and uses the
ORM models' metadata as the autogenerate target, so migrations are generated from
``apps/api/db/models.py``.

The URL is read through ``Settings`` and the engine is built by ``db/engine.py`` — the same two
objects the application uses. Migrations connecting differently from the application is how a
deploy discovers, on the day it matters, that the URI it pasted resolves to a driver that is not
installed, or that the pooler it is behind does not tolerate prepared statements.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

# Import models so every table registers on Base.metadata before autogenerate.
from apps.api.core.config import Settings
from apps.api.db import models  # noqa: F401
from apps.api.db.base import Base
from apps.api.db.engine import create_db_engine, normalize_database_url
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The environment wins over the alembic.ini default (which is only for local autogenerate).
# `Settings` rather than `os.environ` so a placeholder still counts as unset here too.
settings = Settings()
database_url = (
    normalize_database_url(settings.database_url.get_secret_value())
    if settings.database_url is not None
    else None
)
if database_url:
    # Alembic stores the URL in a ConfigParser, which interpolates `%`. Generated passwords
    # contain one often enough that this is worth doing rather than debugging once.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

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
    # When DATABASE_URL is set, connect exactly as the application does — including the driver
    # rewrite and, behind a transaction pooler, the disabled prepared statements that DDL run in
    # one long session would otherwise trip over.
    connectable = (
        create_db_engine(database_url, settings.pool_mode())
        if database_url
        else engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
