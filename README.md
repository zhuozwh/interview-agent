# Interview Agent

一个面向 C++ 后端秋招准备的本地 AI Agent 项目。

项目计划基于个人知识库、简历和真实项目资料，提供面试问答、项目理解、技术学习和面试复盘能力。项目使用现有 LLM API，不进行模型训练。

## 当前阶段

当前版本为 **v0.2.7**，已完成 **Phase 1H：单 Tool Agent 最小闭环**。

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
- 500 字符模型输入安全边界，超限时明确失败而不是静默截断；
- 面向短问题检索长文档的中文 BGE 查询指令；
- 文档向量与查询向量使用独立接口；
- Chroma 本地向量持久化、按文档替换/删除和数据源过滤；
- Embedding 模型、维度和索引格式配置指纹；
- 向量成功后才推进 SQLite 状态的幂等增量同步；
- 返回相对路径、标题层级、原文行号、内容和分数的 `search_chunks`；
- 受限、只读的 `search_notes` Tool；
- 弱证据过滤、Top-K、查询长度和返回正文总预算；
- SQLite Tool 调用追踪，不保存问题或笔记正文；
- 可显式启用的真实模型固定正例与硬负例验收；
- 检索证据的稳定去重、引用编号、完整上下文预算和防注入 JSON 包装；
- OpenAI-compatible 非流式 LLM 客户端、有限安全重试和严格响应校验；
- LLM 超时、认证、限流、请求、连接、服务和响应错误分类；
- token 用量、缓存命中和推理 token 明细；
- 面向知识问答的确定性 Router、一次 Tool 调用和明确停止条件；
- 问题、检索证据、LLM 回答和引用共享的请求追踪标识；
- 模型引用白名单、完成状态、链接和绝对路径的输出校验；
- pytest 基础测试。

尚未实现：

- Front Matter 字段值的语义解析；
- 其他两个初始 Tool、完整问答应用用例和 FastAPI 问答接口；
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
MARKDOWN_CHUNK_MAX_CHARACTERS=500
VECTOR_STORE_PATH=vector_index
VECTOR_COLLECTION_NAME=interview_agent_chunks
EMBEDDING_BATCH_SIZE=64
EMBEDDING_MODEL_NAME=BAAI/bge-small-zh-v1.5
EMBEDDING_CACHE_DIRECTORY=embedding_models
EMBEDDING_LOCAL_FILES_ONLY=false
SEARCH_NOTES_MIN_SCORE=0.58
SEARCH_NOTES_MAX_TOTAL_CHARACTERS=6000
RAG_CONTEXT_MAX_CHARACTERS=8000
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1200
AGENT_TOP_K=5
AGENT_MAX_ANSWER_CHARACTERS=8000
```

Phase 1D 对 Chroma 和 FAISS 做了同机最小验证。两者都能在当前 Windows/Python 环境中完成向量查询、删除和重启恢复；最终只采用 Chroma，因为它原生保存正文与引用元数据、支持元数据过滤和按 ID 更新，避免再为 FAISS 维护一套 ID 映射、元数据侧车和过滤逻辑。

Phase 1E 使用 FastEmbed 在本机运行 `BAAI/bge-small-zh-v1.5`。首次实际生成向量时会下载约 90MB 的公开模型文件到 `EMBEDDING_CACHE_DIRECTORY`，不会上传 Vault 内容；缓存完整后可将 `EMBEDDING_LOCAL_FILES_ONLY=true`，强制只使用本地文件。查询会自动增加该模型针对“短问题检索长文档”推荐的中文指令，文档片段保持原文。

当前阶段提供 Python 内部 Tool，不新增 HTTP 接口。调用方显式传入配置，加载器会规范化源目录和每个文件的真实路径，只递归读取 `.md` 文件，并按相对路径稳定返回：

```python
from interview_agent.core.config import get_settings
from interview_agent.rag import build_search_notes_context
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
    context = build_search_notes_context(
        response,
        max_characters=settings.rag_context_max_characters,
    )
```

每篇文档包含绝对规范化的 `source_path`、相对数据源的 `relative_path` 和 UTF-8 `content`。Front Matter 会从检索正文中分离并原样保留；正文片段仍使用原文件中从 1 开始的真实行号。切分只识别代码围栏外的 `#` 到 `######` ATX 标题；短内容优先保持段落完整，超长内容才按行和字符继续切分。当前默认片段上限为 500 字符，为模型的 512-token 上限保留特殊 token 余量；配置超过 500 会在启动时失败，直接调用 FastEmbed 适配器传入超长文本也会得到明确错误。

`prepare_index_documents` 会生成稳定文档 ID、片段 ID、原文指纹和索引指纹。`build_index_plan` 将当前文件与 SQLite 状态比较，返回 `added`、`modified`、`unchanged` 和 `deleted` 四组结果。相对路径或数据源命名空间改变会视为删除旧文档并新增新文档；正文、Front Matter、切分配置或定位元数据改变会视为修改。

`synchronize_vector_index` 只对新增或修改文档调用 Embedding；未变化文档不会产生 Embedding 调用，删除文档会清理对应向量。模型名称、维度或向量索引格式变化时会强制完整重建。流程先生成并写入全部向量，再在同一个 SQLite 事务中保存文档状态和向量配置；任何前序失败都不会提前推进 SQLite，重试同一计划是幂等的。

`search_chunks` 先校验当前 Embedding 配置与 SQLite、Chroma 中的索引配置一致，再生成一个查询向量。结果包含稳定片段 ID、数据源命名空间、相对路径、标题层级、原文行号、片段指纹、正文和余弦相似度分数，不暴露本机绝对路径。

`search_notes` 是 Agent 后续会调用的稳定 Tool 边界。它固定检索 `notes` 命名空间，不接受任意文件路径；问题最长 480 字符，连同查询指令后仍处于模型安全输入范围。`top_k` 限制为 1 到 10，并按 `SEARCH_NOTES_MIN_SCORE` 拒绝弱证据。返回正文还受总字符预算限制，截断时会显式设置 `content_truncated`。无合格证据会返回 `no_results`，不会把向量数据库强制返回的最近片段伪装成可靠依据。

每次 `search_notes` 调用生成 `trace_id` 和 `tool_call_id`。SQLite 只记录工具名、参数长度摘要、耗时、状态、错误类别和实际返回的片段 ID，不保存问题正文、笔记正文或绝对路径。索引未就绪、Embedding 超时、存储失败和无结果都有稳定状态。

`build_search_notes_context` 把 Tool 响应转换为后续回答生成使用的 `RagContext`。它按检索排名稳定排序，折叠完全相同的重复片段，为实际进入上下文的证据分配 `[S1]`、`[S2]` 等引用标识，并保留相对路径、标题层级、原文行号、指纹和追踪身份。`RAG_CONTEXT_MAX_CHARACTERS` 限制的是包括 JSON 包络、引用元数据和正文在内的完整上下文；空间不足时只截断最低优先级片段并显式标记。

证据使用紧凑 JSON 表达，Markdown 正文始终作为转义后的字符串值存在。上下文携带“不可信只读资料”策略，文档中的伪造 JSON、提示词或工具指令不能改变引用结构；真正的权限、工具白名单和写入边界仍由确定性代码控制。`no_results` 与 Tool 故障会转换为不同的上下文状态，后续 Agent 不会把系统错误误当成“知识库没有答案”。

Phase 1G 使用 `httpx` 直接调用 OpenAI-compatible `POST /chat/completions`，不依赖供应方 SDK。默认 `LLM_BASE_URL=https://api.deepseek.com`，默认模型为 `deepseek-v4-flash`；[DeepSeek 官方更新记录](https://api-docs.deepseek.com/updates)说明旧的 `deepseek-chat` 和 `deepseek-reasoner` 名称已在 2026-07-24 停用，模型变化应通过配置处理，而不是写入业务逻辑。请求和响应字段以[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion)为准。当前只实现非流式文本补全，不启用供应方 Tool Calling。

`OpenAICompatibleLLMClient` 对消息数量、单条和总字符数、输出 token、超时和重试次数设置本地上限。只对连接尚未建立、HTTP 429、502、503 和 504 自动进行有限重试；读取或写入超时可能发生在供应方已经接受并计费之后，因此不会自动重复 POST。认证、限流、无效请求、服务故障和响应结构错误会转换为稳定异常，异常不包含密钥、提示词或供应方错误正文。HTTP 只允许用于 loopback 本地兼容服务，远程地址必须使用 HTTPS。

客户端要求非流式响应只包含一个 `index=0` 的 assistant choice，并校验停止原因、正文、模型、请求 ID 和 token 用量。供应方返回的思维过程不会进入 `LLMResponse`，后续 Agent 只使用最终回答正文。

最小调用方式如下；应用层后续会负责提示词和 RAG 消息组装，适配器本身不读取知识库：

```python
from interview_agent.core.config import get_settings
from interview_agent.llm import (
    ChatMessage,
    ChatRole,
    OpenAICompatibleLLMClient,
)

settings = get_settings()
if settings.llm_api_key is None:
    raise RuntimeError("LLM_API_KEY is not configured")

with OpenAICompatibleLLMClient(
    api_key=settings.llm_api_key.get_secret_value(),
    base_url=settings.llm_base_url,
    model=settings.llm_model,
    timeout_seconds=settings.llm_timeout_seconds,
    max_retries=settings.llm_max_retries,
    temperature=settings.llm_temperature,
    max_tokens=settings.llm_max_tokens,
) as client:
    response = client.complete(
        (
            ChatMessage(role=ChatRole.SYSTEM, content="只回答技术问题。"),
            ChatMessage(role=ChatRole.USER, content="什么是 RAII？"),
        )
    )
```

日常测试通过 `httpx.MockTransport` 覆盖协议和错误路径，不访问网络。只有用户明确接受一次远程调用及其费用，并已经配置测试密钥时，才运行脱敏真实验收：

```powershell
$env:RUN_REAL_LLM_ACCEPTANCE="1"
$env:LLM_API_KEY="<仅在当前终端设置的测试密钥>"
.\.venv\Scripts\python -m pytest tests\test_real_llm_acceptance.py
```

真实验收只发送公开的 RAII 基础问题，不读取或发送 Vault、简历、文件路径和本机运行数据。

Phase 1H 的 `KnowledgeAgent` 建立了第一个真实 Agent 控制循环：

1. 校验问题并生成或接受规范 UUID `trace_id`；
2. 确定性 Router 识别知识问答、项目、简历和面试复盘意图；
3. 知识问答只调用一次 `search_notes`，不允许模型发明工具或参数；
4. 无证据、Tool 故障和不支持意图立即停止，不调用 LLM 猜测；
5. 有证据时构建防注入 RAG JSON，再调用一次 LLM；
6. 只接受正常完成、长度受控且引用全部来自本次检索的回答；
7. 返回回答、实际使用的引用、Tool 调用 ID、LLM 请求 ID 和共同追踪 ID。

当前 Router 对项目、简历和复盘意图采用保守拒绝，因为对应 Tool 尚未在 Phase 1H 实现。模型输出中的 `[S1]` 引用由代码映射回实际 `Citation`；未知、损坏或伪装成链接的引用会使整条回答失败。外部 URL、Markdown 链接和 Windows 绝对路径也不会直接进入最终回答，应用层后续只根据已验证引用生成来源展示。

提示词集中在 `agent/prompts.py` 并带版本号。问题和证据都作为 JSON 数据放在固定系统规则之后；即使文档包含提示注入文本，也不能改变代码控制的工具白名单、调用次数和停止条件。LLM 仍可能生成质量不佳但引用格式合法的文本，这是当前已知边界；回答效果评测和必要的排序优化属于 Phase 2。

SQLite 只保存数据源命名空间、相对路径、指纹、标题路径、行号和向量配置，不保存 Markdown 正文及本机绝对路径。Chroma 在本地保存片段正文、向量和检索元数据，默认目录 `vector_index/` 已被 Git 忽略。`ChromaVectorStore` 应通过 `with` 使用或显式调用 `close()`，这样 Windows 才能及时释放持久化文件。

`SEARCH_NOTES_MIN_SCORE=0.58` 来自 Phase 1E 收口时的最小匿名验收：5 个已确认由真实 Vault 覆盖的问题全部在 Top-5 命中，6 个 Vault 零覆盖但同样具有技术措辞的硬负例全部返回 `no_results`。这只是进入 Agent 主链前的可靠性基线，不等同于完整召回评测；Phase 2 仍会扩大固定问题集，并根据证据决定是否加入混合检索或重排序。

日常自动化测试使用确定性替身，不下载模型、调用网络或读取真实个人知识库。模型缓存已经准备好时，可以显式运行脱敏的真实模型验收：

```powershell
$env:RUN_REAL_EMBEDDING_ACCEPTANCE="1"
$env:EMBEDDING_CACHE_DIRECTORY="embedding_models"
.\.venv\Scripts\python -m pytest tests\test_real_retrieval_acceptance.py
```

该验收在 `TemporaryDirectory` 中创建 Markdown、SQLite 和 Chroma 数据，只读取配置的本地模型缓存。

源目录越界、符号链接解析后越界、扫描失败、Front Matter 未闭合、UTF-8 解码失败或内容超过上限都会抛出明确异常，不会静默跳过失败文件。

## 项目文档

- [PROJECT_SPEC.md](PROJECT_SPEC.md)：产品范围、架构和开发路线；
- [AGENTS.md](AGENTS.md)：开发原则、模块边界和验收要求。
