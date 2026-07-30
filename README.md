# Interview Agent

一个面向 C++ 后端秋招准备的本地 AI Agent 项目。

项目计划基于个人知识库、简历和真实项目资料，提供面试问答、项目理解、技术学习和面试复盘能力。项目使用现有 LLM API，不进行模型训练。

## 当前阶段

当前版本为 **v0.2.4**，已完成 **Phase 1E：本地语义检索 Tool**。

已实现：

- Python 模块化单体工程骨架；
- FastAPI 服务与 `GET /health`；
- 环境变量配置和标准日志；
- SQLite 连接基础设施；
- 配置允许目录内的 Markdown 递归发现和 UTF-8 只读加载；
- Markdown 来源路径、相对路径和正文的最小数据模型；
- 路径规范化、越界拦截、稳定排序和有界读取；
- 按 ATX 标题层级和段落确定性切分 Markdown；
- 每个片段保留来源、标题路径、文档内序号和原文行号；
- 原样分离 Front Matter，并保持正文在原文件中的行号；
- 为文档和片段生成稳定 ID 与 SHA-256 指纹；
- 确定性判断新增、修改、未变化和删除文档；
- SQLite 保存文档和片段索引状态，不保存原始正文或绝对路径；
- 与供应商无关的 Embedding 接口、批处理和返回向量校验；
- FastEmbed + `BAAI/bge-small-zh-v1.5` 本地中文语义向量；
- 文档向量与查询向量使用独立接口；
- Chroma 本地向量持久化、按文档替换/删除和数据源过滤；
- Embedding 模型、维度和索引格式配置指纹；
- 向量成功后才推进 SQLite 状态的幂等增量同步；
- 返回相对路径、标题层级、原文行号、内容和分数的 `search_chunks`；
- 受限、只读的 `search_notes` Tool；
- 弱证据过滤、Top-K、查询长度和返回正文总预算；
- SQLite Tool 调用追踪，不保存问题或笔记正文；
- pytest 基础测试。

尚未实现：

- DeepSeek 或其他 LLM API；
- Front Matter 字段值的语义解析；
- 面向回答生成的完整 RAG 上下文组装；
- 其他两个初始 Tool、Agent Router、LLM Tool Calling 和面试问答；
- Web 前端。

## 快速开始

要求 Python 3.11 或更高版本。以下命令适用于 PowerShell。

创建环境并安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

启动应用：

```powershell
.\.venv\Scripts\python -m interview_agent.main
```

运行测试：

```powershell
.\.venv\Scripts\python -m pytest
```

应用默认监听 `http://127.0.0.1:8000`，健康检查地址为 `http://127.0.0.1:8000/health`。

如需修改本地配置，可复制 `.env.example` 为 `.env`。不要提交包含本机配置或密钥的 `.env`。

## Markdown 只读加载、增量向量索引与检索

先在 `.env` 中配置 Markdown 源目录及允许目录。`ALLOWED_DATA_DIRECTORIES` 使用 JSON 数组；源目录可以是允许目录本身或其子目录：

```dotenv
MARKDOWN_SOURCE_DIRECTORY=knowledge/interview
ALLOWED_DATA_DIRECTORIES=["knowledge"]
MARKDOWN_MAX_FILE_SIZE_BYTES=2097152
MARKDOWN_MAX_TOTAL_SIZE_BYTES=20971520
MARKDOWN_CHUNK_MAX_CHARACTERS=1200
VECTOR_STORE_PATH=vector_index
VECTOR_COLLECTION_NAME=interview_agent_chunks
EMBEDDING_BATCH_SIZE=64
EMBEDDING_MODEL_NAME=BAAI/bge-small-zh-v1.5
EMBEDDING_CACHE_DIRECTORY=embedding_models
EMBEDDING_LOCAL_FILES_ONLY=false
SEARCH_NOTES_MIN_SCORE=0.45
SEARCH_NOTES_MAX_TOTAL_CHARACTERS=6000
```

Phase 1D 对 Chroma 和 FAISS 做了同机最小验证。两者都能在当前 Windows/Python 环境中完成向量查询、删除和重启恢复；最终只采用 Chroma，因为它原生保存正文与引用元数据、支持元数据过滤和按 ID 更新，避免再为 FAISS 维护一套 ID 映射、元数据侧车和过滤逻辑。

Phase 1E 使用 FastEmbed 在本机运行 `BAAI/bge-small-zh-v1.5`。首次实际生成向量时会下载约 90MB 的公开模型文件到 `EMBEDDING_CACHE_DIRECTORY`，不会上传 Vault 内容；缓存完整后可将 `EMBEDDING_LOCAL_FILES_ONLY=true`，强制只使用本地文件。

当前阶段提供 Python 内部 Tool，不新增 HTTP 接口。调用方显式传入配置，加载器会规范化源目录和每个文件的真实路径，只递归读取 `.md` 文件，并按相对路径稳定返回：

```python
from interview_agent.core.config import get_settings
from interview_agent.retrieval import (
    build_index_plan,
    FastEmbedEmbeddingProvider,
    load_markdown_documents,
    prepare_index_documents,
    synchronize_vector_index,
)
from interview_agent.storage import (
    ChromaVectorStore,
    SQLiteDatabase,
    SQLiteIndexStateStore,
    SQLiteToolTraceStore,
)
from interview_agent.tools import SearchNotesRequest, SearchNotesTool

settings = get_settings()
documents = load_markdown_documents(
    settings.markdown_source_directory,
    settings.allowed_data_directories,
    max_file_size_bytes=settings.markdown_max_file_size_bytes,
    max_total_size_bytes=settings.markdown_max_total_size_bytes,
)
current_documents = prepare_index_documents(
    documents,
    max_chunk_characters=settings.markdown_chunk_max_characters,
    source_namespace="notes",
)

database = SQLiteDatabase(settings.database_path)
state_store = SQLiteIndexStateStore(database)
trace_store = SQLiteToolTraceStore(database)
state_store.initialize()
trace_store.initialize()
plan = build_index_plan(current_documents, state_store.load_document_states())

embedding_provider = FastEmbedEmbeddingProvider(
    model_name=settings.embedding_model_name,
    cache_directory=settings.embedding_cache_directory,
    local_files_only=settings.embedding_local_files_only,
)

with ChromaVectorStore(
    settings.vector_store_path,
    collection_name=settings.vector_collection_name,
) as vector_store:
    report = synchronize_vector_index(
        plan,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        state_store=state_store,
        batch_size=settings.embedding_batch_size,
    )
    search_notes = SearchNotesTool(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        state_store=state_store,
        trace_store=trace_store,
        min_score=settings.search_notes_min_score,
        max_total_characters=settings.search_notes_max_total_characters,
    )
    response = search_notes.execute(
        SearchNotesRequest(
            query="智能指针解决了什么问题？",
            top_k=5,
        )
    )
```

每篇文档包含绝对规范化的 `source_path`、相对数据源的 `relative_path` 和 UTF-8 `content`。Front Matter 会从检索正文中分离并原样保留；正文片段仍使用原文件中从 1 开始的真实行号。切分只识别代码围栏外的 `#` 到 `######` ATX 标题；短内容优先保持段落完整，超长内容才按行和字符继续切分。

`prepare_index_documents` 会生成稳定文档 ID、片段 ID、原文指纹和索引指纹。`build_index_plan` 将当前文件与 SQLite 状态比较，返回 `added`、`modified`、`unchanged` 和 `deleted` 四组结果。相对路径或数据源命名空间改变会视为删除旧文档并新增新文档；正文、Front Matter、切分配置或定位元数据改变会视为修改。

`synchronize_vector_index` 只对新增或修改文档调用 Embedding；未变化文档不会产生 Embedding 调用，删除文档会清理对应向量。模型名称、维度或向量索引格式变化时会强制完整重建。流程先生成并写入全部向量，再在同一个 SQLite 事务中保存文档状态和向量配置；任何前序失败都不会提前推进 SQLite，重试同一计划是幂等的。

`search_chunks` 先校验当前 Embedding 配置与 SQLite、Chroma 中的索引配置一致，再生成一个查询向量。结果包含稳定片段 ID、数据源命名空间、相对路径、标题层级、原文行号、片段指纹、正文和余弦相似度分数，不暴露本机绝对路径。

`search_notes` 是 Agent 后续会调用的稳定 Tool 边界。它固定检索 `notes` 命名空间，不接受任意文件路径；问题最长 500 字符，`top_k` 限制为 1 到 10，并按 `SEARCH_NOTES_MIN_SCORE` 拒绝弱证据。返回正文还受总字符预算限制，截断时会显式设置 `content_truncated`。无合格证据会返回 `no_results`，不会把向量数据库强制返回的最近片段伪装成可靠依据。

每次 `search_notes` 调用生成 `trace_id` 和 `tool_call_id`。SQLite 只记录工具名、参数长度摘要、耗时、状态、错误类别和实际返回的片段 ID，不保存问题正文、笔记正文或绝对路径。索引未就绪、Embedding 超时、存储失败和无结果都有稳定状态。

SQLite 只保存数据源命名空间、相对路径、指纹、标题路径、行号和向量配置，不保存 Markdown 正文及本机绝对路径。Chroma 在本地保存片段正文、向量和检索元数据，默认目录 `vector_index/` 已被 Git 忽略。`ChromaVectorStore` 应通过 `with` 使用或显式调用 `close()`，这样 Windows 才能及时释放持久化文件。

`SEARCH_NOTES_MIN_SCORE=0.45` 是 Phase 1E 的保守起点，不是经过完整评测后的最终阈值。Phase 2 才会通过固定问题集系统调整召回、阈值或排序策略。自动化测试使用确定性替身，不下载模型、调用网络或读取真实个人知识库。

源目录越界、符号链接解析后越界、扫描失败、Front Matter 未闭合、UTF-8 解码失败或内容超过上限都会抛出明确异常，不会静默跳过失败文件。

## 项目文档

- [PROJECT_SPEC.md](PROJECT_SPEC.md)：产品范围、架构和开发路线；
- [AGENTS.md](AGENTS.md)：开发原则、模块边界和验收要求。
