"""验证 SQLite 连接创建、外键、关闭和异常处理。"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.storage.sqlite import SQLiteDatabase


# fixture 在每个测试前提供独立目录，测试结束后 TemporaryDirectory 自动删除它。
@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    with TemporaryDirectory(prefix="interview-agent-test-") as directory:
        yield Path(directory)


def test_sqlite_creates_connects_and_closes_in_temporary_directory(
    temporary_directory: Path,
) -> None:
    # 使用测试专用临时目录，绝不会创建正式的 data/interview_agent.db。
    database_path = temporary_directory / "nested" / "test.db"
    database = SQLiteDatabase(database_path)

    with database.connection() as connection:
        # 进入 with 后，父目录和数据库文件都应已经创建。
        assert database_path.exists()
        assert connection.execute("SELECT 1").fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)

    # 离开 with 后连接必须关闭；继续使用已关闭连接会抛 ProgrammingError。
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_sqlite_validate_connection_uses_temporary_directory(
    temporary_directory: Path,
) -> None:
    database_path = temporary_directory / "validation.db"

    SQLiteDatabase(database_path).validate_connection()

    # validate_connection 会真正打开一次 SQLite，因此临时数据库文件应存在。
    assert database_path.is_file()


def test_sqlite_closes_connection_after_error(temporary_directory: Path) -> None:
    database = SQLiteDatabase(temporary_directory / "error.db")

    # 主动制造异常，验证 connection() 会保留原异常并执行回滚和关闭。
    with pytest.raises(RuntimeError, match="expected failure"):
        with database.connection() as connection:
            raise RuntimeError("expected failure")

    # 即使 with 内发生异常，finally 仍应关闭连接。
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
