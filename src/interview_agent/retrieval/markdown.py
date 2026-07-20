"""在明确允许的目录内发现并只读加载 Markdown 文档。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# 默认上限同时在 Settings 中提供给正常应用使用；这里的默认值保证直接调用也有边界。
DEFAULT_MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    """一份已加载 Markdown 文档及其可定位来源。"""

    # source_path 是规范化后的绝对真实路径，供内部安全校验和错误定位使用。
    source_path: Path
    # relative_path 相对于配置的数据源，后续可用于引用展示，避免暴露绝对路径。
    relative_path: Path
    # loader 初次返回完整原文；Front Matter 分离后这里只保存可检索正文。
    content: str

    # Front Matter 以包含 --- 边界的原始文本保留，但不会进入后续检索片段。
    front_matter: str | None = None

    # content 第一行在原文件中的 1-based 行号；未分离时从第 1 行开始。
    content_start_line: int = 1


# 下面的异常都继承同一个基类，调用方既可以统一处理，也可以按失败类型分别处理。
class MarkdownLoadError(RuntimeError):
    """所有 Markdown 加载失败的公共基类。"""


class MarkdownPathError(MarkdownLoadError):
    """数据源或文件路径无效、越界或无法规范化。"""


class MarkdownDiscoveryError(MarkdownLoadError):
    """递归发现 Markdown 文件时失败。"""


class MarkdownReadError(MarkdownLoadError):
    """单个 Markdown 文件无法读取或无法按 UTF-8 解码。"""

    def __init__(self, source_path: Path, message: str) -> None:
        super().__init__(message)
        self.source_path = source_path


class MarkdownSizeError(MarkdownLoadError):
    """单文件或本次加载的内容超过配置上限。"""


def load_markdown_documents(
    source_directory: str | Path,
    allowed_directories: Iterable[str | Path],
    *,
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    max_total_size_bytes: int = DEFAULT_MAX_TOTAL_SIZE_BYTES,
) -> list[MarkdownDocument]:
    """从允许的数据源递归加载 Markdown，按相对路径稳定排序。"""
    # 即使调用方绕过 Settings 直接调用，也不能传入无效读取上限。
    _validate_byte_limit("max_file_size_bytes", max_file_size_bytes)
    _validate_byte_limit("max_total_size_bytes", max_total_size_bytes)

    # 先把允许目录迭代器转换为规范化元组，后续每个路径校验都复用同一白名单。
    normalized_allowed_directories = tuple(
        _resolve_directory(path, label="Allowed data directory")
        for path in allowed_directories
    )
    if not normalized_allowed_directories:
        raise MarkdownPathError("At least one allowed data directory is required")

    # 数据源目录必须真实存在；resolve 也会消除 ..、~ 和符号链接带来的歧义。
    normalized_source_directory = _resolve_directory(
        source_directory, label="Markdown source directory"
    )
    # 只配置数据源还不够，它必须位于至少一个显式允许目录内。
    if not _is_within_any(
        normalized_source_directory, normalized_allowed_directories
    ):
        raise MarkdownPathError(
            "Markdown source directory is outside the allowed data directories: "
            f"{normalized_source_directory}"
        )

    # 发现阶段先得到稳定排序的路径，再依次读取，保证重复运行的返回顺序一致。
    markdown_paths = _discover_markdown_paths(
        normalized_source_directory, normalized_allowed_directories
    )
    documents: list[MarkdownDocument] = []
    total_size_bytes = 0

    for relative_path, source_path in markdown_paths:
        # 单文件读取自身有上限；随后累加实际字节数，检查整批上限。
        content_bytes = _read_file_bytes(source_path, max_file_size_bytes)
        total_size_bytes += len(content_bytes)
        if total_size_bytes > max_total_size_bytes:
            raise MarkdownSizeError(
                "Markdown load exceeds max_total_size_bytes "
                f"({max_total_size_bytes}) while reading: {source_path}"
            )

        # strict UTF-8 不会使用替换字符掩盖损坏内容，编码错误会带文件路径返回。
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MarkdownReadError(
                source_path,
                f"Markdown file is not valid UTF-8: {source_path}",
            ) from error

        # 只有路径、大小和编码全部通过后，文档才会进入最终结果。
        documents.append(
            MarkdownDocument(
                source_path=source_path,
                relative_path=relative_path,
                content=content,
            )
        )

    return documents


def _resolve_directory(path: str | Path, *, label: str) -> Path:
    """把目录规范化为绝对真实路径，并确认它存在且确为目录。"""
    # expanduser 处理用户目录写法；strict=True 确保不存在的配置立即报错。
    candidate = Path(path).expanduser()
    try:
        normalized = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise MarkdownPathError(f"{label} cannot be resolved: {candidate}") from error

    if not normalized.is_dir():
        raise MarkdownPathError(f"{label} is not a directory: {normalized}")
    return normalized


def _discover_markdown_paths(
    source_directory: Path, allowed_directories: tuple[Path, ...]
) -> list[tuple[Path, Path]]:
    """发现普通 Markdown 文件，并再次校验每个规范化文件路径。"""
    discovered: list[tuple[Path, Path]] = []
    try:
        # rglob 负责递归遍历；后面的后缀和 is_file 判断排除其他格式及同名目录。
        candidates = source_directory.rglob("*")
        for candidate in candidates:
            if candidate.suffix.lower() != ".md" or not candidate.is_file():
                continue

            # 文件本身也必须解析真实路径，不能只信任它表面上位于数据源内。
            try:
                normalized_file = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise MarkdownPathError(
                    f"Markdown file path cannot be resolved: {candidate}"
                ) from error

            # 第一层限制文件不能通过符号链接离开本次数据源。
            if not normalized_file.is_relative_to(source_directory):
                raise MarkdownPathError(
                    "Markdown file resolves outside the source directory: "
                    f"{candidate} -> {normalized_file}"
                )
            # 第二层再次核对白名单，形成“源目录 + 允许目录”双重边界。
            if not _is_within_any(normalized_file, allowed_directories):
                raise MarkdownPathError(
                    "Markdown file resolves outside the allowed data directories: "
                    f"{candidate} -> {normalized_file}"
                )

            # 保存发现时的相对路径，便于用户定位原文，也用于稳定排序。
            relative_path = candidate.relative_to(source_directory)
            discovered.append((relative_path, normalized_file))
    except MarkdownLoadError:
        raise
    except (OSError, RuntimeError) as error:
        raise MarkdownDiscoveryError(
            f"Failed to discover Markdown files under: {source_directory}"
        ) from error

    # 先按大小写无关形式排序，再用原始字符串打破平局，使结果完全确定。
    return sorted(
        discovered,
        key=lambda item: (
            item[0].as_posix().casefold(),
            item[0].as_posix(),
        ),
    )


def _read_file_bytes(source_path: Path, max_file_size_bytes: int) -> bytes:
    """有界读取一个文件；读取失败时保留明确的文件路径。"""
    try:
        with source_path.open("rb") as file:
            # 多读 1 字节即可判断是否越界，不需要先把超大文件全部载入内存。
            content = file.read(max_file_size_bytes + 1)
    except OSError as error:
        raise MarkdownReadError(
            source_path, f"Failed to read Markdown file: {source_path}"
        ) from error

    if len(content) > max_file_size_bytes:
        raise MarkdownSizeError(
            "Markdown file exceeds max_file_size_bytes "
            f"({max_file_size_bytes}): {source_path}"
        )
    return content


def _is_within_any(path: Path, directories: tuple[Path, ...]) -> bool:
    """判断规范化路径是否等于或位于任一允许目录内。"""
    # is_relative_to 使用路径组件判断，不会把 notes-old 误当成 notes 的子目录。
    return any(path.is_relative_to(directory) for directory in directories)


def _validate_byte_limit(name: str, value: int) -> None:
    """拒绝无效的读取上限，避免关闭安全边界。"""
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
