"""Minimal SQLite connection infrastructure."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SQLiteDatabase:
    """Create and validate SQLite connections for one database path."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a foreign-key-enabled connection and always close it."""
        connection: sqlite3.Connection | None = None
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path)
            connection.execute("PRAGMA foreign_keys = ON")
            self._verify_foreign_keys(connection)
            yield connection
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def validate_connection(self) -> None:
        """Open the database and verify that a basic query succeeds."""
        with self.connection() as connection:
            result = connection.execute("SELECT 1").fetchone()
            if result != (1,):
                raise sqlite3.DatabaseError("SQLite connection validation failed")

    @staticmethod
    def _verify_foreign_keys(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA foreign_keys").fetchone()
        if result != (1,):
            raise sqlite3.DatabaseError("SQLite foreign key enforcement is disabled")
