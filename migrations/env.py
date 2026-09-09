from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings
from app.db import Base, connection_args

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings().database_url, poolclass=pool.NullPool, connect_args=connection_args)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
