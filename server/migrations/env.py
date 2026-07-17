from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import models  # noqa: F401
from app.db.base import Base

config = context.config
settings = get_settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql("PRAGMA busy_timeout=5000")
            connection.commit()

        def include_object(
            obj: object,
            name: str | None,
            type_: str,
            reflected: bool,
            compare_to: object | None,
        ) -> bool:
            # Migration 0020 deliberately uses SQLite's non-destructive ADD
            # COLUMN path for these nullable links. Rebuilding populated
            # tables only to add the FK can break existing references;
            # application validation enforces the same invariant.
            if (
                connection.dialect.name == "sqlite"
                and type_ == "foreign_key_constraint"
                and not reflected
            ):
                table = getattr(obj, "table", None)
                columns = getattr(obj, "columns", ())
                column_names = {column.name for column in columns}
                if getattr(table, "name", None) in {
                    "episode_outline_versions",
                    "script_versions",
                } and column_names == {"relationship_graph_version_id"}:
                    return False
            return True

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
