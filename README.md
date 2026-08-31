# Interview Agent

一个面向 C++ 后端秋招准备的本地 AI Agent 项目。

项目计划基于个人知识库、简历和真实项目资料，提供面试问答、项目理解、技术学习和面试复盘能力。项目使用现有 LLM API，不进行模型训练。

## 当前阶段

当前开发版本为 **v0.5.4**，处于 **Phase 3：产品化与可用性增强**。

v0.4.6 及以前保持不可变。Phase 2 已完成检索失败归因、固定 namespace、独立阈值、事实证据门控、有限一轮上下文、真实 LLM 合成边界与生产装配收口。v0.5.0 增加本地聊天页、受控会话历史、启动前检查和一键启动/停止；v0.5.1 修复 Windows PowerShell 5.1 启动兼容性；v0.5.2 根据真实使用失败补齐输出预算预检、中文单点错误、显式手动重试、安全有限格式和证据强度提示；v0.5.3 增加按已保存引用身份打开当前本地 Markdown 原文的核验面板；v0.5.4 增加默认离线的外部资料预览、确认和独立 `[W]` 来源展示框架。v0.5.x 不改动 Phase 2 的阈值、Top-K、Router、提示词或答案接受条件。Git/源码扫描、C++ 源码理解和 MATLAB 不在本阶段范围。

已实现：

- Python 模块化单体工程骨架；
- FastAPI 服务、`GET /health` 与 `POST /ask`；
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
- `get_project_context` 项目资料只读语义检索；
- `get_resume_context` 简历资料最小化检索和常见敏感字段脱敏；
- `notes`、`projects`、`resume` 三个固定向量命名空间；
- 三类资料互不重叠的真实路径校验和一次增量索引同步；
- 项目、简历和知识问题各选择一个最小只读 Tool；
- 面试记录脱敏后的无 Tool 复盘，以及固定四部分输出校验；
- 回答证据强度提示和按意图生成的安全追问建议；
- 应用用例统一生成会话与追踪标识；
- SQLite 保存不含问题、证据、回答正文的会话和 Agent 调用摘要；
- 第一次 `/ask` 时延迟加载本地模型、同步索引并组装运行时；
- 从 Markdown 到 FastAPI 响应的完整本地集成测试。
- 根路径 `/` 的本地聊天界面，无需直接使用 FastAPI `/docs`；
- 回答、相对引用、置信提示、安全错误和建议追问的浏览器展示；
- 已保存引用可打开右侧只读证据面板，核对当前 Markdown 原文、标题路径、行号和相关性分数；
- 普通知识问答可手动进入外部查询预览和确认，外部 `[W]` 来源与本地 `[S]` 引用分区且只在当前页面临时显示；
- 回答的有限粗体、列表和行内代码格式，所有 HTML、链接和伪造引用仍按纯文本处理；
- 中文单点错误、稳定诊断码，以及确认费用后才执行的一次手动重试；
- SQLite 受控会话历史、保留期与数量上限、逐会话删除和全部清空；
- 历史正文与 Phase 2 无正文审计分表隔离，禁用历史时不建立正文表；
- 只读 preflight，以及可双击的 `start.cmd` / `stop.cmd`；
- preflight 对已知截断输出预算给出失败，对验收运行时路径给出不迁移数据的提醒；
- 真实服务 PID、解释器映像、启动时间和随机令牌共同保护优雅停止；
- raw 召回、阈值误杀、排序、事实存在性和策略错误的独立评测归因；
- notes/projects/resume 分层混淆矩阵、正负例加权代价和纯阈值反事实；
- 显式跨 namespace 查询与明显批量敏感数据外带的检索前停止；
- “是否存在/是否使用/是否做过”的具体实体证据门控和否定式拒答；
- 在原始 Top-K 内进行词面与当前/历史限定的一致性排序；
- 一轮显式 `previous_question` 引用消解，旧回答和旧引用不会被继承；
- 当前轮明确切换 namespace 时丢弃冲突的上一问，避免旧状态改变 Router 或 Tool；
- 固定集 Hit@5、硬负例拒绝、跨 namespace 拒绝和引用完整性均达到 100%。
- schema v3 强制 calibration/holdout 分割分别覆盖三域正负例；
- 阈值只能在 calibration 选择，再原样迁移到 holdout，禁止用留出集反向调参；
- 冻结匿名留出集覆盖邮箱技术问题误杀、合取事实、跨域和多轮引用。
- 真实 LLM 验收固定为 4 次零重试调用，每次最多输出 256 token；
- 首轮远端证据只允许公开问题与合成 notes/projects，不含真实 resume 或 Vault。
- 生产装配端到端冒烟覆盖合成三源、本地运行时、有限一轮指代和隐私外带停止；
- 双调用真实冒烟在发送前只允许公开 RAII 与固定合成 notes/projects 证据，不允许 resume、个人信息或本机路径。

尚未实现：

- Front Matter 字段值的语义解析；
- 关键词与向量混合检索或独立重排模型（当前固定集未证明必要）；
- 自动持久化的长会话记忆或两轮以上上下文；
- Git/源码扫描、C++ 源码理解或 MATLAB 接入；
- 知识库写入、多用户权限或公网部署。
- 真实外部搜索提供方、网页抓取、外部内容进入 LLM 合成或自动写入。

## 快速开始

要求 Python 3.11 或更高版本。首次使用先复制 `.env.example` 为未提交的 `.env`，配置三个互斥只读数据源和 `LLM_API_KEY`，再创建环境并安装：

创建环境并安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Windows 推荐直接双击仓库根目录的 `start.cmd`。它会先以只读方式检查 Python、密钥、三源隔离、Markdown、Embedding 缓存条件和 `127.0.0.1:8000`，通过后在后台启动并打开聊天页。停止时双击 `stop.cmd`；停止脚本只接受本机随机令牌，并再次核验真实服务 PID、解释器映像和启动时间。

也可以在 PowerShell 中运行：

```powershell
.\scripts\start.ps1
.\scripts\stop.ps1
```

只执行检查、不启动：

```powershell
.\scripts\start.ps1 -CheckOnly
```

开发调试时可绕过后台脚本以前台方式启动：

```powershell
.\.venv\Scripts\python -m interview_agent.main
```

运行测试：

```powershell
.\.venv\Scripts\python -m pytest
```

应用默认监听 `http://127.0.0.1:8000`。普通用户入口是 `GET /` 的聊天页；健康检查、问答接口和开发调试文档分别是 `GET /health`、`POST /ask` 与 `GET /docs`。

不要提交包含本机配置或密钥的 `.env`。启动、健康检查、浏览聊天页、读取历史、展开已保存引用和外部查询预览都不会加载 Embedding 或调用远端服务；提交实际问题时才会按既有 Phase 2 边界把当前问题和完成回答所需的最小证据发送给配置的 LLM endpoint。v0.5.4 默认没有真实搜索提供方，因此点击“联网补充”只会完成本地策略检查并明确提示未联网。任何新的真实搜索或自动化远端验收仍必须另行确认模型/服务、endpoint、次数、token、费用和允许外发范围；v0.5.0 至 v0.5.4 的开发和自动化验收均没有发起新远端调用。

真实 Vault 验收使用单独、显式启用的离线入口。它强制本地 Embedding、拒绝 LLM 密钥、比较 Vault 前后指纹，并只输出匿名报告；完整配置、问题集协议和退出码见 [真实 Vault 只读验收](docs/acceptance/REAL_VAULT_ACCEPTANCE.md)。v0.3.1 的原始指标和失败见 [v0.3.1 正式基线](docs/acceptance/V0.3.1_BASELINE.md)，v0.3.2 的安全边界见 [v0.3.2 安全收口](docs/acceptance/V0.3.2_SECURITY_CLOSURE.md)，v0.4.0 首轮归因见 [v0.4.0 Phase 2 首轮](docs/acceptance/V0.4.0_PHASE2_ROUND1.md)，v0.4.1 的留出协议见 [v0.4.1 留出评测](docs/acceptance/V0.4.1_HOLDOUT_PROTOCOL.md)，远端调用预算与确认项见 [v0.4.2 LLM 前置检查](docs/acceptance/V0.4.2_LLM_PREFLIGHT.md)，三轮真实模型归因和最终结果见 [v0.4.3 真实 LLM 验收](docs/acceptance/V0.4.3_REAL_LLM_ACCEPTANCE.md)，有限上下文边界见 [v0.4.4 一轮上下文验收](docs/acceptance/V0.4.4_LIMITED_CONTEXT.md)，用户冻结真实留出结果见 [v0.4.5 独立 holdout](docs/acceptance/V0.4.5_INDEPENDENT_HOLDOUT.md)，Phase 2 最终装配证据见 [v0.4.6 生产端到端收口](docs/acceptance/V0.4.6_PHASE2_CLOSURE.md)，v0.5.0 的产品化证据见 [v0.5.0 本地可用闭环](docs/acceptance/V0.5.0_LOCAL_USABILITY.md)，Windows 启动热修复证据见 [v0.5.1 Windows 启动闭环](docs/acceptance/V0.5.1_WINDOWS_STARTUP_HOTFIX.md)，真实失败驱动的界面与诊断收口见 [v0.5.2 诊断与可读性验收](docs/acceptance/V0.5.2_DIAGNOSTIC_USABILITY.md)，本地引用原文核验边界见 [v0.5.3 证据核验验收](docs/acceptance/V0.5.3_EVIDENCE_VERIFICATION.md)，默认离线的外部证据边界见 [v0.5.4 外部证据基础验收](docs/acceptance/V0.5.4_EXTERNAL_EVIDENCE_FOUNDATION.md)。

使用 `/ask` 前，先创建并配置三个互不相同、互不包含的数据源目录，并设置 `LLM_API_KEY`。默认目录是 `knowledge/interview`、`knowledge/projects` 和 `knowledge/resume`；它们都必须位于 `ALLOWED_DATA_DIRECTORIES` 白名单内。第一次请求会同步三个目录的 Markdown 索引，模型缓存不存在时还可能下载公开 Embedding 模型，因此耗时会明显高于后续请求。

后续使用现有 Obsidian Vault 时，不需要复制 Markdown 到仓库。可以直接把三个源目录指向 Vault 内职责互不重叠的文件夹，例如：

```dotenv
MARKDOWN_SOURCE_DIRECTORY=D:/Obsidian/面试/知识
PROJECT_SOURCE_DIRECTORY=D:/Obsidian/面试/项目
RESUME_SOURCE_DIRECTORY=D:/Obsidian/面试/简历
ALLOWED_DATA_DIRECTORIES=["D:/Obsidian/面试"]
```

应用始终只读这些 Markdown；SQLite、Chroma 和模型缓存仍应配置到项目的本地运行目录，并由 `.gitignore` 排除。正常应用启动会在创建任何运行时文件前拒绝这些写入路径位于数据源内部，也会拒绝目录型运行时根反向包含数据源。

PowerShell 调试示例：

```powershell
$body = @{
    question = "智能指针如何体现 RAII？"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/ask" `
    -ContentType "application/json" `
    -Body $body
```

面试复盘在同一接口增加 `interview_record` 字段。有限多轮只接受一个可选 `previous_question`：只有当前问题包含“它、这个、上述、前面”等显式指代，且当前轮与上一轮明确 namespace 不冲突时，才用于本轮路由和检索；当前轮明确切换到 projects 或 resume 时以当前轮为准。聊天历史可以为界面恢复保存每轮当前问题和已校验回答，但 Agent 不加载整段历史，不继承旧回答或旧引用，当前回答仍只能引用本轮重新检索得到的 `[S数字]`。缺少 LLM 密钥、数据源目录、索引或模型配置时，`/ask` 返回 503，但 `/health` 不会触发模型下载或远程调用，仍可用于确认进程存活。

## 外部资料补充边界

v0.5.4 在普通技术知识回答旁增加“联网补充”入口，但默认没有配置真实搜索服务。点击后先由本机把当前问题归一化为实际外发查询；项目、简历、面试记录、个人上下文、本地路径、联系方式、常见凭据或“刚才/上面资料”之类非自包含指代会直接停止。未配置提供方时界面明确显示“本次没有联网”，不会发送第二个搜索请求。

未来配置提供方后，界面会先显示提供方名称、实际查询和最多来源数，并通过系统确认框再次征得同意；确认后最多调用提供方一次，不自动重试。返回结果使用独立 `[W1]` 编号、域名和“提供方标记”的来源类型，明确标注尚未核验，不与本地 `[S]` 引用混排。只允许安全的公共 HTTPS 链接，带凭据、敏感查询参数、自定义端口、本机/内网域名、字面 IP 或异常文本的候选会被过滤。

外部查询、搜索 ID、标题、摘要和 `[W]` 卡片只存在于当前页面内存；不会进入 SQLite、`localStorage`、`sessionStorage`、`/ask`、本地引用核验或 LLM 上下文。刷新页面、切换会话或新建会话即丢失，因而没有单独删除动作；删除/清空历史只处理既有本地聊天正文。真实提供方接入、网页抓取、来源事实核验、外部内容合成和费用预算都不是 v0.5.4 的能力。

## 本地会话历史

历史默认写入 `DATABASE_PATH` 指定的 SQLite（默认 `data/interview_agent.db`），与文档索引状态和无正文审计共用文件、分表管理。正文表只保存当前问题、已校验回答、状态、稳定错误、置信提示、建议追问和展示用相对引用；不保存检索证据正文、完整 Vault、提示词、密钥、供应方错误正文、`interview_record`、`previous_question`、内部 chunk/document ID 或指纹。

默认生命周期是 30 天、最多 100 个会话、每会话最近 100 轮；可通过以下未提交配置调整：

```dotenv
SESSION_HISTORY_ENABLED=true
SESSION_HISTORY_RETENTION_DAYS=30
SESSION_HISTORY_MAX_SESSIONS=100
SESSION_HISTORY_MAX_TURNS_PER_SESSION=100
```

界面侧边栏可删除单个会话，“数据与隐私”旁的“清空历史”可删除全部聊天正文。对应本地接口是 `DELETE /api/history/{session_id}` 和 `DELETE /api/history`。删除会话会级联移除正文与展示引用，但不删除 Phase 2 既有的不含正文审计摘要；如需彻底重置全部本机运行记录，应先停止服务，再同时删除 `DATABASE_PATH` 对应 SQLite 和 `VECTOR_STORE_PATH`，模型缓存与 `logs/` 可按需删除。`.run/` 在正常停止后应为空。真实 Markdown/Vault 永远不属于该清理范围。

## Markdown 只读加载、增量向量索引与检索

先在 `.env` 中配置 Markdown 源目录及允许目录。`ALLOWED_DATA_DIRECTORIES` 使用 JSON 数组；源目录可以是允许目录本身或其子目录：

```dotenv
MARKDOWN_SOURCE_DIRECTORY=knowledge/interview
PROJECT_SOURCE_DIRECTORY=knowledge/projects
RESUME_SOURCE_DIRECTORY=knowledge/resume
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
PROJECT_CONTEXT_MIN_SCORE=0.535
PROJECT_CONTEXT_MAX_TOTAL_CHARACTERS=6000
RESUME_CONTEXT_MIN_SCORE=0.56
RESUME_CONTEXT_MAX_TOTAL_CHARACTERS=3000
RAG_CONTEXT_MAX_CHARACTERS=8000
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_THINKING_MODE=disabled
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1200
AGENT_TOP_K=5
AGENT_MAX_ANSWER_CHARACTERS=8000
```

Phase 1D 对 Chroma 和 FAISS 做了同机最小验证。两者都能在当前 Windows/Python 环境中完成向量查询、删除和重启恢复；最终只采用 Chroma，因为它原生保存正文与引用元数据、支持元数据过滤和按 ID 更新，避免再为 FAISS 维护一套 ID 映射、元数据侧车和过滤逻辑。

Phase 1E 使用 FastEmbed 在本机运行 `BAAI/bge-small-zh-v1.5`。首次实际生成向量时会下载约 90MB 的公开模型文件到 `EMBEDDING_CACHE_DIRECTORY`，不会上传 Vault 内容；缓存完整后可将 `EMBEDDING_LOCAL_FILES_ONLY=true`，强制只使用本地文件。查询会自动增加该模型针对“短问题检索长文档”推荐的中文指令，文档片段保持原文。

检索 Tool 继续只作为 Python 内部边界，不开放任意检索 HTTP 接口。v0.5.0 新增的 HTTP 只覆盖聊天提交和本地历史生命周期；调用方不能指定 Tool、namespace 或路径。以下内部示例中，加载器会规范化源目录和每个文件的真实路径，只递归读取 `.md` 文件，并按相对路径稳定返回：

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

Phase 1G 使用 `httpx` 直接调用 OpenAI-compatible `POST /chat/completions`，不依赖供应方 SDK。默认 `LLM_BASE_URL=https://api.deepseek.com`，默认模型为 `deepseek-v4-flash`；[DeepSeek 官方更新记录](https://api-docs.deepseek.com/updates)说明旧的 `deepseek-chat` 和 `deepseek-reasoner` 名称已在 2026-07-24 停用，模型变化应通过配置处理，而不是写入业务逻辑。请求和响应字段以[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion)为准。DeepSeek V4 默认启用思考模式，思考 token 与最终答案共享 `max_tokens`；短回答 grounded RAG 默认设置 `LLM_THINKING_MODE=disabled`，避免小预算只生成思考而没有完整答案。非 DeepSeek 兼容端点可以设置 `provider_default`，此时客户端不发送供应方扩展字段。当前只实现非流式文本补全，不启用供应方 Tool Calling。

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
    thinking_mode=settings.llm_thinking_mode,
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

Phase 1H 的 `KnowledgeAgent` 先用 `search_notes` 建立了第一个真实 Agent 控制循环：

1. 校验问题并生成或接受规范 UUID `trace_id`；
2. 确定性 Router 识别知识问答、项目、简历和面试复盘意图；
3. 当时的知识问答只调用一次 `search_notes`，不允许模型发明工具或参数；
4. 无证据、Tool 故障和不支持意图立即停止，不调用 LLM 猜测；
5. 有证据时构建防注入 RAG JSON，再调用一次 LLM；
6. 只接受正常完成、长度受控且引用全部来自本次检索的回答；
7. 返回回答、实际使用的引用、Tool 调用 ID、LLM 请求 ID 和共同追踪 ID。

Phase 1J 已把 Router 接到三个只读 Tool：知识、项目和简历问题分别固定选择 `search_notes`、`get_project_context` 或 `get_resume_context`，一次请求最多调用其中一个。模型输出中的 `[S1]` 引用由代码映射回实际 `Citation`；未知、损坏或伪装成链接的引用会使整条回答失败。外部 URL、Markdown 链接以及 Windows、UNC、POSIX 绝对路径也不会直接进入最终回答，API 只根据已验证引用生成来源展示。

提示词集中在 `agent/prompts.py` 并带版本号。问题和证据都作为 JSON 数据放在固定系统规则之后；即使文档包含提示注入文本，也不能改变代码控制的工具白名单、调用次数和停止条件。LLM 仍可能生成质量不佳但引用格式合法的文本，这是当前已知边界；回答效果评测和必要的排序优化属于 Phase 2。

Phase 1I 增加两个与 `search_notes` 同级的只读 Tool：

- `get_project_context` 固定检索 `projects` 命名空间，只面向已经纳入数据源的项目说明、设计和实现状态；它不读取源码或 Git 历史，也不会根据文档空白推断功能已实现。
- `get_resume_context` 固定检索 `resume` 命名空间，默认正文预算为 3000 字符；返回前会脱敏常见邮箱、中国大陆手机号、身份证号、微信/WeChat 账号、本机绝对路径和 `file://` URI，避免把个人标识或本机用户名发送给后续 LLM。

三类数据源分别通过 `MARKDOWN_SOURCE_DIRECTORY`、`PROJECT_SOURCE_DIRECTORY` 和 `RESUME_SOURCE_DIRECTORY` 配置，但每个目录及其文件仍必须位于 `ALLOWED_DATA_DIRECTORIES` 白名单内。索引时为三类文档分别传入 `notes`、`projects`、`resume` namespace，并将它们合并为同一次增量计划；Chroma 查询由 Tool 内部固定 namespace 过滤，调用方不能指定任意目录或跨资料类型检索。

三个 Tool 共享 `ScopedSemanticSearchTool` 的输入校验、弱证据过滤、正文预算、错误映射和 SQLite 追踪。这个共享层在第三个真实 Tool 出现后才建立，不是为假设扩展预先设计的插件系统。

简历脱敏是发往 LLM 前的最小化保护，不是完整的数据防泄漏系统：当前只覆盖上述常见格式，不识别护照、银行卡号或任意自由文本中的隐私。原始简历片段和向量仍只保存在本机 Chroma 中；若未来接入其他远端服务，必须先补充对应的数据分类和脱敏策略。

Phase 1J 增加应用层和 HTTP 闭环。`AskInterviewAgentUseCase` 为每次请求生成 `trace_id`，为连续请求生成或复用规范 `session_id`，调用 Agent 后把状态、意图、路由原因、Tool/LLM 标识、引用编号、耗时和输入长度写入 SQLite；问题、面试记录、证据和回答正文均不落入追踪表。追踪写入失败时不会继续返回未审计的成功回答。

面试复盘不需要把用户记录伪装成知识库 Tool。调用方显式提供 `interview_record` 后，代码先对问题和记录中的常见联系方式脱敏，再进行一次 LLM 调用；结果必须按“问题归纳、回答表现、暴露短板、后续行动”四部分输出，并且不能生成知识库引用、外部链接或绝对路径。只有“请复盘”但没有记录时会在本地停止，不让模型猜测一场不存在的面试。

实际 `/ask` 流程为：校验 HTTP 数据 → 应用层建立会话和追踪 → Router 选择零个或一个 Tool → 本地 Embedding/Chroma 检索 → RAG 证据校验与限长 → 一次 LLM 调用 → 回答/引用校验 → SQLite 安全摘要 → HTTP 响应。高置信提示只表示本次回答实际引用了至少两个不同来源文件，不是模型正确率；复盘没有外部证据评分，因此标记为 `not_applicable`。

SQLite 的索引与审计表只保存数据源命名空间、相对路径、指纹、标题路径、行号、向量配置和无正文追踪摘要，不保存 Markdown 正文及本机绝对路径。v0.5.0 的聊天正文位于单独 `chat_sessions` / `chat_turns` 表，并受上文生命周期与删除策略约束。Chroma 在本地保存片段正文、向量和检索元数据，默认目录 `vector_index/` 已被 Git 忽略。`ChromaVectorStore` 应通过 `with` 使用或显式调用 `close()`，这样 Windows 才能及时释放持久化文件。

Phase 2 固定集不再假设三个 namespace 共享同一分数分布。`notes=0.58` 保留 Phase 1 基线；`projects=0.535` 位于关键正例 `0.537974` 与未被事实策略覆盖的最近负例 `0.529941` 之间；`resume=0.56` 让最低关键正例 `0.561703` 进入候选。代价矩阵同时证明单靠这些较低阈值会在 projects/resume 产生 4/3 个误接受，因此它们只能与 namespace 检索前停止和事实锚点门控共同使用，不能单独视为安全校准。

日常自动化测试使用确定性替身，不下载模型、调用网络或读取真实个人知识库。模型缓存已经准备好时，可以显式运行脱敏的真实模型验收：

```powershell
$env:RUN_REAL_EMBEDDING_ACCEPTANCE="1"
$env:EMBEDDING_CACHE_DIRECTORY="embedding_models"
.\.venv\Scripts\python -m pytest tests\test_real_retrieval_acceptance.py
```

该验收在 `TemporaryDirectory` 中创建 Markdown、SQLite 和 Chroma 数据，只读取配置的本地模型缓存。

源目录越界、符号链接解析后越界、扫描失败、Front Matter 未闭合、UTF-8 解码失败或内容超过上限都会抛出明确异常，不会静默跳过失败文件。

## Phase 2 留出验收与已知边界

v0.4.6 默认离线测试为 **295 passed、4 skipped**；新增跳过项是显式真实本地端到端 Embedding 和显式真实 DeepSeek 端到端冒烟。显式真实本地端到端验收另有 **1 passed**。用户批准的真实双调用冒烟已单独通过，固定两次、零重试、`thinking=disabled`，只发送公开 RAII 与合成 notes/projects 证据；其后新增的发送前白名单由默认离线测试覆盖，没有再次产生远端调用。v0.4.5 的 45-case 真实三源回归和独立 holdout 结论继续有效：原固定集 Router 100%、Hit@1 85.71%、Hit@5 100%、MRR 0.916667、硬负例拒绝 100%、跨 namespace 拒绝 100%、正负例 Agent 边界 100% 和引用完整性 100%；用户独立 holdout 的两个 split 与 19 个 probe 也全部通过。本版本没有调整阈值、Top-K、可接受答案或提示词，因此不存在用本轮真实输出反向调参。

当前已知边界：

- Router 使用可解释的固定关键词，不处理复杂多意图或开放式规划；
- 一次请求最多使用一个只读 Tool；无可靠证据时停止，不自动扩大检索链；
- Agent 多轮只支持调用方显式提供一条上一轮用户问题；界面可持久化正文用于恢复，但不会把整段历史、旧回答或旧引用送回 Agent，也不支持自动长会话摘要；
- 本地共享运行时按单用户模型串行执行，第一次 `/ask` 还会同步索引；
- 常见个人信息脱敏不是完整 DLP，不覆盖护照、银行卡或任意自由文本隐私；
- LLM 输出没有完整的语义 DLP；在当前单机单用户、资料由本人维护的边界下暂缓，若改为多人、公网或不可信资料输入必须重新评估；
- 引用校验能证明来源确实来自本次检索，但不能自动证明每句话都被证据语义蕴含；
- 事实锚点门控只覆盖明确技术标识、引号实体、常见组织名称和属性对，不等同于开放域事实蕴含模型；
- 真实 LLM 的单轮合成边界已通过；供应方可用性、价格和模型行为仍可能变化，真实资料外发范围仍需单独确认。

## Phase 3 v0.5.0 本地可用性验收

v0.5.0 默认离线套件为 **327 passed、4 skipped**，较 v0.4.6 新增 32 个通过项且没有新增 skip。新增覆盖聊天页及同源安全头、受控历史 CRUD、保留期与数量清理、禁用不落正文、绝对引用拒绝、历史错误脱敏、preflight 失败归因、真实 PID 记录和令牌停止协议。JavaScript 语法、Python `compileall`、PowerShell AST 解析、`pip check` 和 `git diff --check` 均通过。

Windows 实机演示先后暴露并修复了虚拟环境启动器 PID 与真实服务 PID 不一致、虚拟环境路径与操作系统进程映像路径不一致，以及优雅退出后 PID 文件重复删除三项问题。最终 `start.ps1 -NoBrowser` 通过当前本地配置的只读 preflight，`/health` 返回 `ok`；`stop.ps1` 触发 Uvicorn application shutdown 后 PID 文件不存在、8000 端口不监听。演示只扫描本地 Markdown，不发起提问、不加载 Embedding、不建立索引、不调用远端 LLM。

Phase 2 的旧生产装配夹具显式关闭 v0.5 正文历史后，继续证明审计 SQLite 不含问题或源正文；v0.5 独立夹具同时证明默认历史只保存受控字段并可删除。检索、Router、RAG、LLM 提示和引用校验文件均未修改，冻结阈值仍是 notes `0.58`、projects `0.535`、resume `0.56`。

## Phase 3 v0.5.1 Windows 启动热修复

v0.5.1 来自真实用户入口失败：`start.cmd` 在 Windows PowerShell 5.1 下先因无 BOM 的 UTF-8 中文脚本被系统代码页误读而语法失败；修复后又在当前宿主同时提供 `Path` / `PATH` 时触发 `Start-Process` 的重复键异常。修复为给两个 PowerShell 入口写入 UTF-8 BOM、在启动子进程前规范化当前脚本进程的 Path 键，并让 `.cmd` 保存 PowerShell 的真实退出码后再决定是否暂停。实机以系统 `powershell.exe -File` 和两个 `.cmd` 完成启动、HTTP 200 首页、`/health=ok` 与停止闭环；默认离线回归为 **330 passed、4 skipped**。详细证据见 [v0.5.1 Windows 启动闭环](docs/acceptance/V0.5.1_WINDOWS_STARTUP_HOTFIX.md)。

## Phase 3 v0.5.2 诊断与可读性验收

v0.5.2 来自真实聊天中的截断、英文重复错误、Markdown 原样显示和无关引用不易识别。preflight 现在拒绝已知会截断日常回答的 256 token 验收预算，并提醒 `acceptance-*` 运行时路径不会被自动迁移；界面用安全 DOM 展示有限粗体、列表和行内代码，始终显示证据强度，把失败统一为一处中文说明，并只在用户确认重新检索、远端调用和潜在费用后允许一次手动重试。API 与历史会在边界处净化任意底层错误正文。默认离线回归为 **338 passed、4 skipped**，恶意 HTML、脚本 URL、伪造引用、路径/密钥异常和重试循环对抗用例全部通过；没有新增真实远端调用。详细证据见 [v0.5.2 诊断与可读性验收](docs/acceptance/V0.5.2_DIAGNOSTIC_USABILITY.md)。

## Phase 3 v0.5.3 本地证据核验验收

v0.5.3 让成功保存到本地历史的结构化引用可以打开右侧核验面板。浏览器只提交 `session_id + trace_id + citation_id`，服务端从受控历史恢复 namespace、相对路径和行号，再在固定白名单内有界读取当前 Markdown；面板明确说明当前文件可能已变化、检索分数不是正确率。证据正文不进入 SQLite 或浏览器持久化，历史关闭、保存失败、引用删除或路径越界都不会降级成任意文件读取。默认离线回归为 **361 passed、5 skipped**；新增 skip 仅是当前 Windows 账户不能创建测试符号链接，其余穿越、伪造身份、内容注入、失效来源、超大/非法文件、循环导入和审计同时间戳乱序均已验证。没有请求 `/ask`、加载模型或发起远端调用。详细证据见 [v0.5.3 证据核验验收](docs/acceptance/V0.5.3_EVIDENCE_VERIFICATION.md)。

## Phase 3 v0.5.4 外部证据基础验收

v0.5.4 把外部搜索建成与本地 Agent 分离的可选证据平面：只有普通技术知识问题能手动进入本地预览，提供方存在时还需确认实际外发查询，随后最多调用一次；结果使用独立 `[W]` 卡片，只在当前页面临时显示。默认生产装配没有真实提供方，因此本版本自动化和冷启动验收均为零真实搜索、零新 LLM 调用。敏感信息、个人/本地上下文、确认篡改、危险 URL、恶意 HTML、超量候选、提供方异常和浏览器持久化均有对抗覆盖。最终默认离线回归为 **388 passed、5 skipped**，无新增 skip；Phase 2 检索和回答链零改动。详细证据见 [v0.5.4 外部证据基础验收](docs/acceptance/V0.5.4_EXTERNAL_EVIDENCE_FOUNDATION.md)。

## 项目文档

- [PROJECT_SPEC.md](PROJECT_SPEC.md)：产品范围、架构和开发路线；
- [AGENTS.md](AGENTS.md)：开发原则、模块边界和验收要求。
- [安全最佳实践审查](docs/security/SECURITY_BEST_PRACTICES_REPORT.md)：按严重性记录发现、修复和接受风险；
- [威胁模型](docs/security/interview-agent-threat-model.md)：记录资产、信任边界、攻击路径和风险触发条件。
- [v0.4.0 Phase 2 首轮](docs/acceptance/V0.4.0_PHASE2_ROUND1.md)：记录失败归因、代价矩阵、指标变化和剩余风险。
- [v0.4.1 留出评测](docs/acceptance/V0.4.1_HOLDOUT_PROTOCOL.md)：记录 calibration/holdout 防泄漏协议、对抗结果和人工冻结点。
- [v0.4.2 LLM 前置检查](docs/acceptance/V0.4.2_LLM_PREFLIGHT.md)：记录 DeepSeek 模型、调用数、token、费用和证据外发确认边界。
- [v0.4.3 真实 LLM 验收](docs/acceptance/V0.4.3_REAL_LLM_ACCEPTANCE.md)：记录两次失败、思考模式/输出契约修复、第三轮通过和回归结果。
- [v0.4.4 一轮上下文验收](docs/acceptance/V0.4.4_LIMITED_CONTEXT.md)：记录 namespace 状态优先级修复、匿名多轮矩阵、引用边界和零回归证据。
- [v0.4.5 独立 holdout](docs/acceptance/V0.4.5_INDEPENDENT_HOLDOUT.md)：记录用户首次运行前冻结的真实问题集、双 split 指标和隐私边界。
- [v0.4.6 Phase 2 收口](docs/acceptance/V0.4.6_PHASE2_CLOSURE.md)：记录生产装配端到端、发送前白名单和最终阶段边界。
- [v0.5.0 本地可用闭环](docs/acceptance/V0.5.0_LOCAL_USABILITY.md)：记录聊天界面、受控历史、preflight、一键启停、失败归因和 Phase 2 零退化证据。
- [v0.5.1 Windows 启动闭环](docs/acceptance/V0.5.1_WINDOWS_STARTUP_HOTFIX.md)：记录系统 PowerShell 5.1 中文脚本解码失败、热修复和真实双击入口验收。
- [v0.5.2 诊断与可读性验收](docs/acceptance/V0.5.2_DIAGNOSTIC_USABILITY.md)：记录真实聊天失败、有限安全格式、错误净化、受控重试、对抗测试和剩余检索风险。
- [v0.5.3 证据核验验收](docs/acceptance/V0.5.3_EVIDENCE_VERIFICATION.md)：记录受控引用身份、当前文件核验、路径/持久化边界、对抗测试和剩余风险。
- [v0.5.4 外部证据基础验收](docs/acceptance/V0.5.4_EXTERNAL_EVIDENCE_FOUNDATION.md)：记录外发预览、确认、来源分区、零持久化、对抗测试和真实提供方决策边界。
