# Interview Agent

一个本地运行的个人技术知识与面试 Agent 项目，面向 C++ 后端秋招准备。项目计划基于个人知识库和真实项目资料提供可追溯的学习与面试辅助。

## 当前阶段

项目当前处于 **Phase 0：基础工程初始化**。

已实现：

- Python `src` layout 与模块化单体骨架；
- 环境变量配置加载；
- Python 标准日志初始化；
- SQLite 连接基础设施；
- FastAPI 应用工厂与 `GET /health`；
- 基础自动化测试。

尚未实现：

- LLM 或 DeepSeek API 接入；
- Embedding、向量数据库、Markdown 索引与 RAG；
- Agent Router、Tool Calling、引用和面试问答；
- Web 前端和业务数据库表。

## 环境要求

- Python 3.11 或更高版本；
- Python 自带的 `venv` 与 `pip`。

## 安装

以下命令适用于 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

如需自定义本地配置，可复制 `.env.example` 为 `.env` 后修改。`.env` 不应提交到版本库。

## 启动

权威启动方式：

```powershell
.\.venv\Scripts\python -m interview_agent.main
```

服务默认监听 `http://127.0.0.1:8000`，健康检查地址为 `http://127.0.0.1:8000/health`。

## 测试

```powershell
.\.venv\Scripts\python -m pytest
```
