# Interview Agent

一个面向 C++ 后端秋招准备的本地 AI Agent 项目。

项目计划基于个人知识库、简历和真实项目资料，提供面试问答、项目理解、技术学习和面试复盘能力。项目使用现有 LLM API，不进行模型训练。

## 当前阶段

当前版本为 **v0.2.0**，已完成 **Phase 1A：Markdown 只读加载基础**。

已实现：

- Python 模块化单体工程骨架；
- FastAPI 服务与 `GET /health`；
- 环境变量配置和标准日志；
- SQLite 连接基础设施；
- 配置允许目录内的 Markdown 递归发现和 UTF-8 只读加载；
- Markdown 来源路径、相对路径和正文的最小数据模型；
- 路径规范化、越界拦截、稳定排序和有界读取；
- pytest 基础测试。

尚未实现：

- DeepSeek 或其他 LLM API；
- Markdown 切分和 Front Matter 解析；
- Markdown 索引、Embedding、向量数据库和 RAG；
- Agent Router、Tool Calling 和面试问答；
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

## Markdown 只读加载

先在 `.env` 中配置 Markdown 源目录及允许目录。`ALLOWED_DATA_DIRECTORIES` 使用 JSON 数组；源目录可以是允许目录本身或其子目录：

```dotenv
MARKDOWN_SOURCE_DIRECTORY=knowledge/interview
ALLOWED_DATA_DIRECTORIES=["knowledge"]
MARKDOWN_MAX_FILE_SIZE_BYTES=2097152
MARKDOWN_MAX_TOTAL_SIZE_BYTES=20971520
```

当前阶段提供 Python 内部加载能力，不新增 HTTP 接口。调用方显式传入配置，加载器会规范化源目录和每个文件的真实路径，只递归读取 `.md` 文件，并按相对路径稳定返回：

```python
from interview_agent.core.config import get_settings
from interview_agent.retrieval import load_markdown_documents

settings = get_settings()
documents = load_markdown_documents(
    settings.markdown_source_directory,
    settings.allowed_data_directories,
    max_file_size_bytes=settings.markdown_max_file_size_bytes,
    max_total_size_bytes=settings.markdown_max_total_size_bytes,
)
```

每个结果包含绝对规范化的 `source_path`、相对数据源的 `relative_path` 和 UTF-8 `content`。源目录越界、符号链接解析后越界、扫描失败、单文件读取或 UTF-8 解码失败、内容超过上限都会抛出明确异常，不会静默跳过失败文件。加载过程不会修改原始 Markdown。

## 项目文档

- [PROJECT_SPEC.md](PROJECT_SPEC.md)：产品范围、架构和开发路线；
- [AGENTS.md](AGENTS.md)：开发原则、模块边界和验收要求。
