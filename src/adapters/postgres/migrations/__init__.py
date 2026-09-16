"""Apply reversible metadata migrations on a caller-owned transaction."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection


def migrate(connection: Connection, revision: str) -> None:
    configuration = Config()
    configuration.set_main_option("script_location", str(Path(__file__).parent))
    configuration.attributes["connection"] = connection
    if revision == "base":
        command.downgrade(configuration, revision)
    else:
        command.upgrade(configuration, revision)
