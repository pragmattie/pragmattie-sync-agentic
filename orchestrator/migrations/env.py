from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from sdlc.config import get_settings
from sdlc.db import Base
from sdlc.migrations_filter import VERSION_TABLE, include_name

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

CONFIGURE_OPTIONS = {
    "target_metadata": target_metadata,
    "include_name": include_name,
    "version_table": VERSION_TABLE,
}


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONFIGURE_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, **CONFIGURE_OPTIONS)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
