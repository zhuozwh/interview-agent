# 真实 Vault 只读验收

## 目标与边界

该验收入口用于验证 Phase 1 在真实本地 Markdown 上的加载、切分、增量索引、路由、召回、拒答、引用和隐私边界。它属于 v0.3.x 的验收基础设施，不改变生产 Router、阈值、切分或排序策略，也不实现 Phase 2 功能。

验收命令具有以下硬边界：

- 必须显式设置 `RUN_REAL_VAULT_ACCEPTANCE=1`；
- `LLM_API_KEY` 必须为空；
- `EMBEDDING_LOCAL_FILES_ONLY` 必须为 `true`；
- 三个源目录必须使用真实路径，且互不相同、互不包含；
- SQLite、Chroma、模型缓存和报告必须位于数据源之外；
- 正式模式要求 `notes`、`projects`、`resume` 三个源都存在且包含 Markdown；
- 缺源时只能使用 `--allow-incomplete-sources` 生成预基线，报告中的 `acceptance_passed` 必定为 `false`；
- 运行前后会比较源目录全部文件和目录项；Markdown 额外比较 SHA-256，其他文件比较大小和最后写入时间；
- 报告不保存问题正文、期望文件名、检索正文或绝对路径。

真实 Markdown 只会进入本地内存、SQLite 元数据和本地 Chroma。简历证据进入 LLM 边界前会脱敏常见个人数据、本机绝对路径和 `file://` URI。验收使用确定性 LLM 替身检查引用与停止条件，不构造远端 LLM 客户端。

## 本机配置

复制 `.env.example` 为被 Git 忽略的 `.env`，并至少覆盖以下字段：

```dotenv
MARKDOWN_SOURCE_DIRECTORY=<独立 notes 目录>
PROJECT_SOURCE_DIRECTORY=<独立 projects 目录>
RESUME_SOURCE_DIRECTORY=<独立 resume 目录>

# 正式验收建议只列出三个精确源目录，不使用整个 Vault 根目录。
ALLOWED_DATA_DIRECTORIES=["<notes>","<projects>","<resume>"]

DATABASE_PATH=data/acceptance-v0.3.1/state.sqlite3
VECTOR_STORE_PATH=vector_index/acceptance-v0.3.1
VECTOR_COLLECTION_NAME=interview_agent_acceptance_v031
EMBEDDING_CACHE_DIRECTORY=embedding_models
EMBEDDING_LOCAL_FILES_ONLY=true
LLM_API_KEY=
```

`.env`、本地问题集、SQLite、Chroma、模型缓存和明细报告不得提交。仓库已忽略 `acceptance_local/`，该目录可保存本机问题集与报告。

## 本地问题集协议

问题集为 UTF-8 JSON。真实问题、相对文件路径和个人事实映射只保存在本机，仓库仅记录协议和匿名汇总。

```json
{
  "schema_version": 1,
  "cases": [
    {
      "case_id": "N01",
      "question": "一个不含个人标识的本地问题",
      "expected_intent": "knowledge_question",
      "probes": [
        {
          "namespace": "notes",
          "category": "positive",
          "expectation": "success",
          "expected_paths": ["topic/example.md"],
          "critical": true
        },
        {
          "namespace": "projects",
          "category": "cross_namespace",
          "expectation": "no_results",
          "expected_paths": [],
          "critical": false
        }
      ]
    }
  ]
}
```

约束如下：

- `case_id` 必须匿名且唯一；
- 问题最长 480 字符，不允许 NUL 等控制字符；
- `expected_paths` 只允许 POSIX 相对路径，不允许绝对路径和 `..`；
- `positive` 必须期望 `success` 并提供至少一个可接受来源；
- `hard_negative` 和 `cross_namespace` 必须期望 `no_results`；
- 每个已加载 namespace 至少有一个正例，问题集还必须包含对抗性负例；
- 正例允许列出多个都能完整回答问题的来源，避免把同义文档误判成失败。

## 运行方式

正式三源验收：

```powershell
$env:RUN_REAL_VAULT_ACCEPTANCE="1"
.\.venv\Scripts\python -m interview_agent.acceptance `
  --cases acceptance_local\v0.3.1\cases.json `
  --report acceptance_local\v0.3.1\report.json
```

缺少一个源时的预基线：

```powershell
$env:RUN_REAL_VAULT_ACCEPTANCE="1"
.\.venv\Scripts\python -m interview_agent.acceptance `
  --cases acceptance_local\v0.3.1\cases.prebaseline.json `
  --report acceptance_local\v0.3.1\report.prebaseline.json `
  --allow-incomplete-sources
```

退出码：

- `0`：正式三源、安全不变量和质量门槛全部通过；
- `1`：配置、路径、问题集或运行时边界使验收无法安全执行；
- `2`：验收已完成并写出报告，但缺源或某项门槛未通过。

## 匿名指标

报告记录：

- 每个 namespace 的文档数、片段数和 UTF-8 字节数；
- 冷/暖增量同步摘要和第二次同步幂等性；
- Router 准确率；
- `Hit@1`、`Hit@5`、MRR；
- 硬负例和跨 namespace 拒绝率；
- 正例可落地率和成功证据的引用完整性；
- 正例 Agent 闭环与负例“不调用 LLM”通过率；
- SQLite、LLM 替身载荷和 Vault 零修改边界；
- 匿名失败 case ID、稳定失败分类和 Phase 2 决策触发项。

阈值或效果失败不会在验收器里静默调参。v0.3.x 只修复验收框架、Phase 1 正确性、安全和隐私缺陷；扩大问题集、按 namespace 校准阈值、调整切分、混合检索和重排序从 v0.4.0 开始。

## 真实 LLM

本命令永远不调用真实 LLM。远端验收继续使用单独的 `tests/test_real_llm_acceptance.py`，且必须由用户明确批准调用次数、token 上限和费用后显式启用。若未来要发送选定 Vault 证据，还需要额外确认外发内容范围；首轮不得发送真实简历片段。
