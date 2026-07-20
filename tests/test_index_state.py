"""验证 SQLite 增量索引状态的写入、更新、删除和重启恢复。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import (
    build_index_plan,
    load_markdown_documents,
    prepare_index_documents,
)
from interview_agent.storage import SQLiteDatabase, SQLiteIndexStateStore


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """所有数据库和 Markdown 都位于自动清理的临时目录。"""
    with TemporaryDirectory(prefix="interview-agent-index-state-test-") as directory:
        yield Path(directory)


def _prepare_from_source(source_directory: Path):
    """执行测试用的真实加载、Front Matter 分离、切分和指纹流水线。"""
    documents = load_markdown_documents(
        source_directory, [source_directory.parent]
    )
    return prepare_index_documents(documents, max_chunk_characters=1000)


def test_initializes_applies_and_recovers_state_after_restart(
    temporary_directory: Path,
) -> None:
    source_directory = temporary_directory / "allowed" / "notes"
    source_directory.mkdir(parents=True)
    (source_directory / "a.md").write_text("# A\n正文", encoding="utf-8")
    (source_directory / "b.md").write_text(
        "---\ntype: note\n---\n# B\n正文", encoding="utf-8"
    )
    database_path = temporary_directory / "state.db"

    store = SQLiteIndexStateStore(SQLiteDatabase(database_path))
    store.initialize()
    current = _prepare_from_source(source_directory)
    first_plan = build_index_plan(current, store.load_document_states())

    assert len(first_plan.added) == 2
    assert first_plan.change_count == 2
    store.apply_plan(first_plan)

    # 新建 Store 对象模拟应用重启，状态应从同一个 SQLite 文件恢复。
    restarted = SQLiteIndexStateStore(SQLiteDatabase(database_path))
    restarted.initialize()
    states = restarted.load_document_states()
    chunks = restarted.load_chunk_states()

    assert len(states) == 2
    assert sum(state.chunk_count for state in states) == len(chunks)
    assert [state.relative_path.as_posix() for state in states] == ["a.md", "b.md"]
    assert [state.front_matter_present for state in states] == [False, True]

    second_plan = build_index_plan(current, states)
    assert len(second_plan.unchanged) == 2
    assert second_plan.change_count == 0

    # 状态表只保存索引元数据，不保存原始正文或本机绝对路径。
    with SQLiteDatabase(database_path).connection() as connection:
        document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(index_documents)")
        }
        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(index_chunks)")
        }
    assert "source_path" not in document_columns
    assert "content" not in chunk_columns


def test_modified_document_replaces_chunks_and_deleted_document_cascades(
    temporary_directory: Path,
) -> None:
    source_directory = temporary_directory / "allowed" / "notes"
    source_directory.mkdir(parents=True)
    first_path = source_directory / "a.md"
    deleted_path = source_directory / "b.md"
    first_path.write_text("# A\n旧正文\n## 旧章节\n内容", encoding="utf-8")
    deleted_path.write_text("# B\n将删除", encoding="utf-8")

    store = SQLiteIndexStateStore(
        SQLiteDatabase(temporary_directory / "state.db")
    )
    store.initialize()
    initial = _prepare_from_source(source_directory)
    store.apply_plan(build_index_plan(initial, ()))
    deleted_document_id = next(
        item.document_id for item in initial if item.relative_path == Path("b.md")
    )

    first_path.write_text("# A\n新正文", encoding="utf-8")
    deleted_path.unlink()
    current = _prepare_from_source(source_directory)
    plan = build_index_plan(current, store.load_document_states())

    assert len(plan.modified) == 1
    assert len(plan.deleted) == 1
    assert plan.deleted[0].document_id == deleted_document_id
    store.apply_plan(plan)

    states = store.load_document_states()
    assert [state.relative_path.as_posix() for state in states] == ["a.md"]
    assert len(store.load_chunk_states(deleted_document_id)) == 0
    assert len(store.load_chunk_states(states[0].document_id)) == states[0].chunk_count


def test_end_to_end_incremental_cycle_is_unchanged_after_apply(
    temporary_directory: Path,
) -> None:
    source_directory = temporary_directory / "allowed" / "notes"
    source_directory.mkdir(parents=True)
    note_path = source_directory / "note.md"
    note_path.write_text("---\ntype: note\n---\n# 标题\n正文", encoding="utf-8")

    store = SQLiteIndexStateStore(
        SQLiteDatabase(temporary_directory / "state.db")
    )
    store.initialize()

    current = _prepare_from_source(source_directory)
    initial_plan = build_index_plan(current, store.load_document_states())
    store.apply_plan(initial_plan)

    unchanged_plan = build_index_plan(
        _prepare_from_source(source_directory), store.load_document_states()
    )
    assert unchanged_plan.change_count == 0
    assert len(unchanged_plan.unchanged) == 1

    note_path.write_text("---\ntype: note\n---\n# 标题\n修改后", encoding="utf-8")
    changed_plan = build_index_plan(
        _prepare_from_source(source_directory), store.load_document_states()
    )
    assert len(changed_plan.modified) == 1
    store.apply_plan(changed_plan)

    final_plan = build_index_plan(
        _prepare_from_source(source_directory), store.load_document_states()
    )
    assert final_plan.change_count == 0
    assert len(final_plan.unchanged) == 1
