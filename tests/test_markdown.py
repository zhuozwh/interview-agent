"""验证 Markdown 数据源的路径边界、发现、读取和错误行为。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import (
    MarkdownDiscoveryError,
    MarkdownDocument,
    MarkdownPathError,
    MarkdownReadError,
    MarkdownSizeError,
    load_markdown_documents,
)


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """为每个测试提供自动清理且与账户无关的临时目录。"""
    # 不使用 pytest tmp_path，避免 Windows 跨账户运行时出现目录权限问题。
    with TemporaryDirectory(prefix="interview-agent-markdown-test-") as directory:
        yield Path(directory)


def test_loads_only_markdown_recursively_as_utf8_in_stable_order(
    temporary_directory: Path,
) -> None:
    # 构造一个两层模拟 Vault，混合可读取和必须忽略的扩展名。
    allowed_directory = temporary_directory / "allowed"
    source_directory = allowed_directory / "notes"
    nested_directory = source_directory / "nested"
    nested_directory.mkdir(parents=True)

    (source_directory / "z-last.md").write_text("最后", encoding="utf-8")
    (nested_directory / "A-first.md").write_text("你好，Markdown", encoding="utf-8")
    (nested_directory / "b-second.MD").write_text("第二份", encoding="utf-8")
    (nested_directory / "ignored.txt").write_text("not Markdown", encoding="utf-8")
    (source_directory / "ignored.markdown").write_text("ignored", encoding="utf-8")
    (source_directory / "ignored.md.bak").write_text("ignored", encoding="utf-8")

    # 执行真实加载函数，而不是使用替身。
    documents = load_markdown_documents(source_directory, [allowed_directory])

    # 同时验证 UTF-8 正文、相对路径、绝对来源路径和稳定顺序。
    assert documents == [
        MarkdownDocument(
            source_path=(nested_directory / "A-first.md").resolve(),
            relative_path=Path("nested/A-first.md"),
            content="你好，Markdown",
        ),
        MarkdownDocument(
            source_path=(nested_directory / "b-second.MD").resolve(),
            relative_path=Path("nested/b-second.MD"),
            content="第二份",
        ),
        MarkdownDocument(
            source_path=(source_directory / "z-last.md").resolve(),
            relative_path=Path("z-last.md"),
            content="最后",
        ),
    ]


def test_normalizes_source_and_allowed_directory_paths(
    temporary_directory: Path,
) -> None:
    # 路径中故意加入 child/.. 和 .，验证加载器使用规范化结果而非原字符串。
    allowed_directory = temporary_directory / "allowed"
    source_directory = allowed_directory / "notes"
    source_directory.mkdir(parents=True)
    document_path = source_directory / "note.md"
    document_path.write_text("normalized", encoding="utf-8")

    unnormalized_source = source_directory / "child" / ".."
    unnormalized_allowed = allowed_directory / "."

    documents = load_markdown_documents(unnormalized_source, [unnormalized_allowed])

    assert documents[0].source_path == document_path.resolve()
    assert documents[0].relative_path == Path("note.md")


def test_rejects_source_outside_allowed_directory(
    temporary_directory: Path,
) -> None:
    # 即使源目录真实存在，只要不在白名单内也必须拒绝。
    allowed_directory = temporary_directory / "allowed"
    source_directory = temporary_directory / "outside"
    allowed_directory.mkdir()
    source_directory.mkdir()

    with pytest.raises(
        MarkdownPathError, match="outside the allowed data directories"
    ):
        load_markdown_documents(source_directory, [allowed_directory])


def test_rejects_markdown_path_that_resolves_outside_source(
    temporary_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows 普通账户通常不能创建符号链接，因此通过 resolve 替身模拟越界结果。
    source_directory = temporary_directory / "allowed" / "notes"
    outside_file = temporary_directory / "outside.md"
    source_directory.mkdir(parents=True)
    outside_file.write_text("outside", encoding="utf-8")
    linked_file = source_directory / "linked.md"
    linked_file.write_text("link placeholder", encoding="utf-8")
    original_resolve = Path.resolve

    def resolve_outside_for_link(
        path: Path, strict: bool = False
    ) -> Path:
        if path == linked_file:
            return original_resolve(outside_file, strict=strict)
        return original_resolve(path, strict=strict)

    # 模拟符号链接的真实路径解析结果，避免测试依赖 Windows 管理员权限。
    monkeypatch.setattr(Path, "resolve", resolve_outside_for_link)

    with pytest.raises(MarkdownPathError, match="outside the source directory"):
        load_markdown_documents(
            source_directory, [temporary_directory / "allowed"]
        )


def test_rejects_missing_or_non_directory_source(
    temporary_directory: Path,
) -> None:
    # 缺失路径和普通文件都不能作为递归数据源目录。
    allowed_directory = temporary_directory / "allowed"
    allowed_directory.mkdir()

    with pytest.raises(MarkdownPathError, match="cannot be resolved"):
        load_markdown_documents(
            allowed_directory / "missing", [allowed_directory]
        )

    file_path = allowed_directory / "file.md"
    file_path.write_text("content", encoding="utf-8")
    with pytest.raises(MarkdownPathError, match="is not a directory"):
        load_markdown_documents(file_path, [allowed_directory])


def test_requires_at_least_one_allowed_directory(
    temporary_directory: Path,
) -> None:
    # 调用层同样保留白名单非空检查，避免绕过 Settings 后失去安全边界。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()

    with pytest.raises(MarkdownPathError, match="At least one"):
        load_markdown_documents(source_directory, [])


def test_invalid_utf8_returns_clear_file_error(
    temporary_directory: Path,
) -> None:
    # 写入不合法字节，确认加载器不会用替换字符伪装成正常正文。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()
    invalid_file = source_directory / "invalid.md"
    invalid_file.write_bytes(b"\xff\xfe")

    with pytest.raises(MarkdownReadError, match="not valid UTF-8") as error_info:
        load_markdown_documents(source_directory, [temporary_directory])

    assert error_info.value.source_path == invalid_file.resolve()
    assert isinstance(error_info.value.__cause__, UnicodeDecodeError)


def test_single_file_read_failure_is_not_silently_ignored(
    temporary_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 用 monkeypatch 稳定模拟权限错误，不依赖本机文件权限设置。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()
    readable_file = source_directory / "readable.md"
    failing_file = source_directory / "unreadable.md"
    readable_file.write_text("readable", encoding="utf-8")
    failing_file.write_text("unreadable", encoding="utf-8")
    original_open = Path.open

    def fail_for_one_file(path: Path, *args: object, **kwargs: object):
        if path == failing_file.resolve():
            raise PermissionError("simulated permission error")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_for_one_file)

    with pytest.raises(MarkdownReadError, match="Failed to read") as error_info:
        load_markdown_documents(source_directory, [temporary_directory])

    assert error_info.value.source_path == failing_file.resolve()
    assert isinstance(error_info.value.__cause__, PermissionError)


def test_discovery_failure_returns_clear_source_error(
    temporary_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 单独模拟目录遍历失败，验证它与单文件读取失败使用不同异常类型。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()

    def fail_discovery(path: Path, pattern: str):
        raise PermissionError(f"cannot scan {path} with {pattern}")

    monkeypatch.setattr(Path, "rglob", fail_discovery)

    with pytest.raises(
        MarkdownDiscoveryError, match="Failed to discover Markdown files"
    ) as error_info:
        load_markdown_documents(source_directory, [temporary_directory])

    assert isinstance(error_info.value.__cause__, PermissionError)


def test_enforces_file_and_total_size_limits(temporary_directory: Path) -> None:
    # 两个 4 字节文件既能触发单文件上限，也能触发累计上限。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()
    (source_directory / "a.md").write_text("1234", encoding="utf-8")
    (source_directory / "b.md").write_text("5678", encoding="utf-8")

    with pytest.raises(MarkdownSizeError, match="max_file_size_bytes"):
        load_markdown_documents(
            source_directory,
            [temporary_directory],
            max_file_size_bytes=3,
        )

    with pytest.raises(MarkdownSizeError, match="max_total_size_bytes"):
        load_markdown_documents(
            source_directory,
            [temporary_directory],
            max_file_size_bytes=4,
            max_total_size_bytes=7,
        )


@pytest.mark.parametrize("limit_name", ["file", "total"])
def test_rejects_non_positive_loader_size_limits(
    temporary_directory: Path, limit_name: str
) -> None:
    # 直接调用加载器时，0 也不能被理解成“无限制”。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()
    arguments = (
        {"max_file_size_bytes": 0}
        if limit_name == "file"
        else {"max_total_size_bytes": 0}
    )

    with pytest.raises(ValueError, match="must be greater than zero"):
        load_markdown_documents(
            source_directory,
            [temporary_directory],
            **arguments,
        )


def test_empty_source_returns_empty_list(temporary_directory: Path) -> None:
    # 合法但没有 Markdown 的目录不是错误，应返回稳定的空列表。
    source_directory = temporary_directory / "notes"
    source_directory.mkdir()

    assert load_markdown_documents(
        source_directory, [temporary_directory]
    ) == []
