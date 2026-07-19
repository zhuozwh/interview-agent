"""只负责 SQLite 连接生命周期的最小基础设施。"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SQLiteDatabase:
    """为指定数据库路径创建和验证 SQLite 连接。"""

    def __init__(self, database_path: str | Path) -> None:
        # 无论调用者传字符串还是 Path，内部都统一使用 Path 处理路径。
        self.database_path = Path(database_path)

    # contextmanager 让调用者可以使用 with；作用类似用 RAII 管理连接资源。
    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """提供启用外键的连接，并保证退出 with 时关闭连接。"""
        # 先设为 None，确保连接创建失败时 finally 也能安全判断。
        connection: sqlite3.Connection | None = None
        try:
            # parents=True 递归创建父目录；exist_ok=True 表示目录已存在也不报错。
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path)

            # SQLite 的外键约束需要对每个新连接显式开启。
            connection.execute("PRAGMA foreign_keys = ON")
            self._verify_foreign_keys(connection)

            # yield 把连接交给 with 代码块；代码块结束后从这里继续执行。
            yield connection
            connection.commit()
        except Exception:
            # with 内任何异常都会回滚，然后用 raise 原样继续抛出该异常。
            if connection is not None:
                connection.rollback()
            raise
        finally:
            # finally 无论成功或异常都会执行，因此连接一定会被关闭。
            if connection is not None:
                connection.close()

    def validate_connection(self) -> None:
        """打开数据库，并用 SELECT 1 验证基本查询可以执行。"""
        with self.connection() as connection:
            result = connection.execute("SELECT 1").fetchone()
            if result != (1,):
                raise sqlite3.DatabaseError("SQLite connection validation failed")

    @staticmethod
    def _verify_foreign_keys(connection: sqlite3.Connection) -> None:
        """读取 PRAGMA，确认当前连接的外键约束确实已经开启。"""
        result = connection.execute("PRAGMA foreign_keys").fetchone()
        if result != (1,):
            raise sqlite3.DatabaseError("SQLite foreign key enforcement is disabled")
