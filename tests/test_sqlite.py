import sqlite3
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.storage.sqlite import SQLiteDatabase


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    with TemporaryDirectory(prefix="interview-agent-test-") as directory:
        yield Path(directory)


def test_sqlite_creates_connects_and_closes_in_temporary_directory(
    temporary_directory: Path,
) -> None:
    database_path = temporary_directory / "nested" / "test.db"
    database = SQLiteDatabase(database_path)

    with database.connection() as connection:
        assert database_path.exists()
        assert connection.execute("SELECT 1").fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_sqlite_validate_connection_uses_temporary_directory(
    temporary_directory: Path,
) -> None:
    database_path = temporary_directory / "validation.db"

    SQLiteDatabase(database_path).validate_connection()

    assert database_path.is_file()


def test_sqlite_closes_connection_after_error(temporary_directory: Path) -> None:
    database = SQLiteDatabase(temporary_directory / "error.db")

    with pytest.raises(RuntimeError, match="expected failure"):
        with database.connection() as connection:
            raise RuntimeError("expected failure")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
