# v0.3.2 安全最佳实践审查

## 执行摘要

本次审查面向 Python 3.11+ 的本地单用户 FastAPI/RAG 应用，覆盖配置、文件读取、SQLite、Chroma、Embedding、LLM HTTP 边界、Agent 输出校验、日志、依赖和发布流程。审查未发现 Critical 或 High 问题；发现 1 个 Medium 级 Phase 1 正确性缺陷并已在 v0.3.2 修复：正常应用启动此前没有像真实 Vault 验收器一样阻止 SQLite、Chroma 或模型缓存路径与只读数据源重叠。

剩余两个 Medium 风险都依赖当前使用边界发生变化：服务被绑定到非 loopback 地址，或本地资料变成不可信输入且仍发送到远程 LLM。LLM 输出侧完整语义 DLP 按用户决定在当前单机单用户场景下暂缓，作为接受风险记录，不视为已经修复。

## 审查范围与依据

- 范围：`src/interview_agent/`、`tests/`、`pyproject.toml`、`.env.example`、`.gitignore`、README 和验收文档。
- 运行模型：Windows 本机、单用户、服务默认只监听 `127.0.0.1`，无多租户和公网入口。
- 敏感数据：真实 notes/projects/resume Markdown、简历联系方式、本机路径、LLM API 密钥、本地 Chroma 正文与向量。
- 不读取内容：本机 `.env`、真实 Vault 正文、SQLite/Chroma 正文和模型缓存。
- 不执行：真实远端 LLM 调用、Phase 2 检索或 Router 改造。
- Skill 参考库没有通用 Python CLI/RAG 专项条目；本报告按仓库实际 Python、FastAPI、httpx、SQLite、Chroma 和本地文件系统边界审查，没有套用不相关的 Flask/Django 检查表。

## Critical

无。

## High

无。

## Medium

### SEC-001：正常运行时写入路径可能与只读数据源重叠——已修复

- 状态：Fixed in v0.3.2。
- 影响：错误的 `.env` 配置可能让 SQLite、Chroma 或模型缓存直接写入 notes/projects/resume 数据源；目录型运行时根若反向包含数据源，也会放大后续重建或人工清理时误伤 Markdown 的风险。
- 原因：真实 Vault 验收器已经检查运行时路径，但 `build_local_runtime` 在创建数据库、模型缓存和 Chroma 前没有同等检查。
- 修复：`src/interview_agent/application/runtime.py:149-179` 在组装任何可写组件前调用边界检查；`src/interview_agent/application/runtime.py:289-319` 规范化路径并拒绝双向目录重叠。
- 验证：`tests/test_application_runtime.py:127-198` 覆盖三类运行时路径写入数据源及目录型运行时根包含数据源，并确认失败前没有创建目标文件。
- 残余风险：同一 OS 用户在校验后主动替换目录或符号链接属于本地账户控制范围；当前单用户威胁模型下为 Low。

### SEC-002：LLM 输出没有完整语义 DLP——接受风险

- 状态：Accepted for local single-user v0.3.2。
- 影响：模型可能生成引用格式合法但语义不当的内容；仅凭格式、URL、绝对路径和引用编号校验，不能证明每句话均被证据蕴含，也不能覆盖任意自由文本隐私。
- 现有控制：问题在发送前调用公共脱敏器（`src/interview_agent/agent/knowledge_agent.py:279-289`）；简历 Tool 对证据做同类脱敏；回答必须使用本次检索引用，且拒绝未知引用、链接和绝对路径（`src/interview_agent/agent/knowledge_agent.py:514-594`）；提示词将问题和证据标记为不可信 JSON 数据（`src/interview_agent/agent/prompts.py:15-94`）。
- 决策：用户明确当前仅个人使用，暂不增加输出侧隐私门禁。
- 重新开启条件：多人使用、监听非 loopback、接收他人或自动抓取的文档、接入新的远端供应方、需要处理护照/银行卡等更多数据类别。

### SEC-003：FastAPI 没有认证，错误绑定地址会扩大数据和费用暴露——条件风险

- 状态：Accepted under loopback-only deployment。
- 影响：若使用其他启动命令把 ASGI 应用绑定到局域网或公网，任意可达者可调用 `/ask`，消耗远端 LLM 费用并获取基于个人资料的回答。
- 现有控制：官方启动入口固定监听 `127.0.0.1`（`src/interview_agent/main.py:61-66`）；HTTP 请求不能指定 Tool、namespace 或路径（`src/interview_agent/api/ask.py:15-22`）。
- 缺口：`create_app` 本身没有认证或“禁止非 loopback”能力，外部 Uvicorn 命令可以绕过默认 host。
- 决策：当前产品规范明确为本地单用户，不增加认证系统。
- 重新开启条件：局域网共享、反向代理、容器端口发布、公网部署或多用户访问。

## Low

### SEC-004：依赖可安装范围较宽，发布复现性依赖安装时间

- 状态：Open, Low。
- 证据：`pyproject.toml:14-20` 使用兼容版本区间，没有仓库级 lockfile 或哈希锁定。
- 影响：未来重新安装可能获得不同的小版本，带来行为变化或供应链风险。
- 现有控制：每个核心依赖都有主版本上限；当前环境 `pip check` 无冲突；FastEmbed 只允许其内置支持模型，Chroma 遥测显式关闭。
- 建议：进入需要可复制部署或 CI 发布的阶段后再选择 `uv.lock`、constraints 或哈希锁定；不要为当前本地 MVP临时引入第二套包管理流程。

### SEC-005：HTTP 请求体在 ASGI 层没有预解析字节上限

- 状态：Accepted under loopback-only deployment。
- 证据：Agent 对问题限制 480 字符、面试记录限制 12,000 字符（`src/interview_agent/agent/knowledge_agent.py:36-37,456-510`），但 FastAPI/Pydantic 在请求体解析完成后才进入这些检查。
- 影响：若服务暴露给不可信网络，大请求可能先消耗内存和解析时间。
- 建议：只有部署边界改变时，才在反向代理或 ASGI 中间件增加请求字节上限和速率限制。

### SEC-006：本地索引和运行记录默认依赖 OS 文件权限保护

- 状态：Accepted local storage model。
- 影响：拥有相同 Windows 账户文件权限的进程可以读取或篡改 Chroma 正文、向量和 SQLite 元数据。
- 现有控制：SQLite 不保存问题或回答正文；Chroma 关闭匿名遥测（`src/interview_agent/storage/chroma.py:58-65`）；运行时目录被 Git 忽略。
- 建议：若设备存在多账户共享或备份外发，再评估磁盘加密、专用目录 ACL 和加密备份。

## 已确认的正向控制

- Markdown 路径使用真实路径解析、数据源与白名单双重校验，并拒绝符号链接越界（`src/interview_agent/retrieval/markdown.py:55-177`）。
- 文件读取有单文件与总量上限，并使用严格 UTF-8（`src/interview_agent/retrieval/markdown.py:82-116,191-210`）。
- LLM 远程地址要求 HTTPS，HTTP 只允许 loopback；httpx 禁止环境代理并关闭重定向（`src/interview_agent/llm/openai_compatible.py:75-85,387-424`）。
- LLM 异常不回显密钥、提示词或供应方错误正文，响应体有 2 MiB 上限。
- Chroma 禁用匿名遥测，且不让 Chroma 自动选择 Embedding 函数或下载模型（`src/interview_agent/storage/chroma.py:58-65,88-99`）。
- Agent 一次请求最多使用一个固定 Tool；模型不能生成可执行 Tool、路径、SQL 或系统命令。
- `.env`、SQLite、Chroma、模型缓存和真实验收明细均被 Git 忽略；跟踪文件扫描未发现真实密钥或真实本机用户路径。
- SQLite 查询参数化，追踪表不保存问题、面试记录、证据或回答正文。

## 验证结果

- 本机真实配置只读边界预检：通过；未输出配置值或密钥。
- Python 编译检查：通过。
- 默认离线测试：`241 passed, 2 skipped`。
- 真实本地 Embedding：`1 passed`。
- `pip check`：无依赖冲突。
- 真实远端 LLM：未执行，没有网络调用和费用。
- v0.3.1 真实 Vault 三源基线未重跑；本次没有修改加载、切分、Embedding、检索、排序、Router 或引用算法，原匿名指标继续作为 Phase 2 输入。

## 发布判断

在已确认的单机单用户、loopback-only、资料由本人维护的边界下，没有阻止 v0.3.2 发布的 Critical/High 问题。SEC-001 已修复；SEC-002、SEC-003、SEC-005、SEC-006 均有明确使用前提和重新开启条件。检索效果失败不属于本报告的安全修复范围，从 v0.4.0 Phase 2 开始处理。
