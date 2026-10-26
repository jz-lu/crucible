from crucible import research_reviews  # Register scoped research assessments.
from crucible import proposal_retractions  # Register terminal author withdrawals.
from crucible import proposal_notes  # noqa: F401 — durable scheme-review sources
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from crucible.config import load_settings
from crucible.db import Base
from crucible import research  # Register versioned research tables in metadata.
from crucible import protocol_proposals  # Register private intake records in metadata.
from crucible import scoring_awards  # Register explicit scheme admission credits.
from crucible import machine_auth  # Register scoped agent credentials and browser handoff.
from crucible import generation  # Register non-secret generation outcome evidence.
from crucible import method_sources  # Register immutable uploaded method source bundles.
from crucible import beta_access  # Register invitations, membership and verified mailboxes.
from crucible import notebook  # Register durable notes, immutable revisions and publication decisions.
from crucible import implementation_builds  # Register explicit reviewed builds without changing release pins.
from crucible import github_auth  # Register explicit provider identities and bound browser flows.
from crucible import password_recovery  # Register one-use verified-mailbox recovery.
from crucible import researcher_profiles  # Register optional member-declared profile metadata.

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option("sqlalchemy.url", load_settings().database_url.replace("%", "%%"))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
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
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
