"""Real ephemeral Compose dependencies; credentials never leave the test session."""

import os
import subprocess
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import URL, Engine, create_engine, text

from adapters.postgres.migrations import migrate


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[URL]:
    credential = uuid4().hex
    environment = os.environ | {"TEST_POSTGRES_PASSWORD": credential}
    command = [
        "docker",
        "compose",
        "-f",
        "tests/integration/compose.yml",
        "-p",
        f"grounded-test-{uuid4().hex[:12]}",
    ]
    try:
        subprocess.run(
            command + ["up", "-d", "--wait"],
            env=environment,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            command + ["port", "postgres", "5432"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        port = int(result.stdout.strip().rsplit(":", 1)[1])
        yield URL.create(
            "postgresql+psycopg",
            username="ingestion_test",
            password=credential,
            host="127.0.0.1",
            port=port,
            database="ingestion_test",
        )
    finally:
        subprocess.run(
            command + ["down", "--volumes"],
            env=environment,
            check=True,
            capture_output=True,
        )


@pytest.fixture
def database(postgres_url: URL) -> Iterator[Engine]:
    schema = f"test_{uuid4().hex}"
    admin = create_engine(postgres_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        postgres_url,
        connect_args={
            "options": f"-csearch_path={schema}",
            "connect_timeout": 3,
        },
    )
    try:
        with engine.begin() as connection:
            migrate(connection, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
