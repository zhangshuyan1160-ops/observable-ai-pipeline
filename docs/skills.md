# Skills 检索、筛选与新增记录

检索/安装日期：2026-10-12。目标是支持题 D 的两天 MVP，重点覆盖后台任务、耐久执行、数据库状态、可观察性、故障验证和状态看板。

## 实际完成的检索

1. 使用现有 `skill-installer`，从 Vercel 原始仓库固定 commit 安装并阅读 `find-skills`。
2. 按 `find-skills` 工作流先检查 [skills.sh 目录](https://skills.sh/)，再运行 Skills CLI 的 `find`。
3. 实际运行 **12 组查询、240 条返回、232 个去重候选**。进一步核对了下表中的关键候选。
4. 联网搜索 GitHub 和 skills.sh，读取 8 个源仓库的元信息；对入选项读取原始 `SKILL.md`，检查来源、适用范围、版本与许可。
5. 新增 **6 个**技能；复用已有技能，未批量安装所有搜索结果。

当前终端没有 `npx`，因此使用内置 Node + `pnpm dlx skills@1.7.2 find "查询词"`。使用 Vercel Skills CLI 完成查询。

| 查询词 | 返回条数 | 主要判断 |
| --- | --- | --- |
| `workflow orchestration` | 20 | 选流程/活动分离与恢复模式，不引入完整编排平台 |
| `background jobs` | 20 | 选 Python 后台任务、幂等与有限重试 |
| `observability` | 20 | 选 Python 结构化日志、关联 ID、有限指标标签 |
| `fastapi` | 20 | 已有 fastapi-expert，避免重复安装模板技能 |
| `postgresql` | 20 | 选表设计、约束、索引和时间语义 |
| `python testing` | 20 | 复用已有 python-testing-patterns |
| `webapp testing` | 20 | 核对 Anthropic 技能，已有 Playwright 能力足够 |
| `frontend design` | 20 | 选择原始 Anthropic 来源，避免同名镜像 |
| `docker` | 20 | 复用 docker-development；不增加云平台绑定 |
| `github actions` | 20 | 复用已有 github-actions-templates |
| `gpu scheduling` | 20 | 大多偏 K8s/云 GPU/性能优化，不适合两天 mock MVP |
| `checkpoint recovery` | 20 | 多数是 agent 会话 checkpoint，与任务执行检查点不同 |

原始结果：[find-results.json](research/find-results.json)。仓库元信息：[repository-snapshot.json](research/repository-snapshot.json)。固定版本及安装后全部文件摘要：[skills-lock.json](research/skills-lock.json)。安装量和 stars 记录检索时的状态，筛选同时考虑正文、许可和项目适用性。

## 已新增的 6 个 Skills

| Skill / 来源 | 检索时安装量 | 源仓库 stars | 本项目用途 | 当前阶段使用状态 |
| --- | --- | --- | --- | --- |
| [find-skills](https://github.com/vercel-labs/skills/tree/13e4063a1cf913f5606d57d42ab83a86f5001e04/skills/find-skills) / Vercel | 约 3.8M | 33,865 | 技能发现与质量筛选 | 已用于本次调研 |
| [python-background-jobs](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/python-development/skills/python-background-jobs) / wshobson | 约 10.8K | 40,436 | 独立 Worker、幂等、至少一次、退避 | 已用于方案取舍 |
| [python-observability](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/python-development/skills/python-observability) / wshobson | 约 11.2K | 40,436 | JSON 日志、关联 ID、指标基数 | 已用于观察模型 |
| [postgresql-table-design](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/database-design/skills/postgresql-table-design) / wshobson | 约 27K | 40,436 | 状态表、唯一约束、索引、UTC 时间 | 已用于持久化设计 |
| [workflow-orchestration-patterns](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/backend-development/skills/workflow-orchestration-patterns) / wshobson | 约 11.7K | 40,436 | 流程与活动边界、幂等、心跳、恢复粒度 | 作为架构参考；未采用 Temporal SDK |
| [frontend-design](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design) / Anthropic | 约 975.5K | 180,579 | 面向排障的 DAG、状态与历史可视化 | 已应用于 DAG/历史看板并完成浏览器截图检查 |

六项安装到 `~/.codex/skills/<name>/`，按固定 commit 保存，运行项目不依赖这些技能。

### 固定版本与复现

安装使用现有 Codex `skill-installer`，命令如下；目录已存在时安装器会拒绝覆盖。所有引用文件随对应技能目录安装。

```bash
python3 "$HOME/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo vercel-labs/skills \
  --ref 13e4063a1cf913f5606d57d42ab83a86f5001e04 \
  --path skills/find-skills

python3 "$HOME/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo wshobson/agents \
  --ref 46891e7e60da0e52baf1050b7b6391b64e84c6d9 \
  --path plugins/python-development/skills/python-background-jobs \
         plugins/python-development/skills/python-observability \
         plugins/database-design/skills/postgresql-table-design \
         plugins/backend-development/skills/workflow-orchestration-patterns

python3 "$HOME/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo anthropics/skills \
  --ref dbd4588f9e1033efb41dad4bef2f7947c8993d44 \
  --path skills/frontend-design
```

Vercel Skills 和 wshobson 仓库元信息标识 MIT；frontend-design 的技能级 `LICENSE.txt` 为 Apache-2.0。项目只保存来源、哈希和原创评估，不将第三方技能全文复制进拟公开仓库。

## 复用已有技能

以下是规划时已有的候选技能，实际使用项见后文：

| Skill | 后续启用阶段 | 选择理由 |
| --- | --- | --- |
| `fastapi-expert` | API 与 Pydantic schema | 已具备相关后端指导 |
| `python-pro` | 执行器与错误类型 | 类型、异步边界与清晰异常处理 |
| `python-testing-patterns` | 状态机和数据库故障测试 | 对真实恢复边界写必要测试 |
| `docker-development` | 打包与本地多进程演示 | API/Worker 共用镜像，数据独立卷 |
| `github-actions-templates` | CI | lint、测试和 PostgreSQL service |
| `playwright` | UI 验收 | 已有真实浏览器操作与截图流程 |
| `code-documenter` | 最终说明与 API 文档 | 代码与使用文档保持一致 |

`skill-installer` 是本次实际使用的系统技能，不计入新增的 6 个。

## 深入核对但暂不安装

| 候选 | 核对内容 | 不选原因 |
| --- | --- | --- |
| [Temporal 官方 temporal-developer](https://github.com/temporalio/skill-temporal-developer) | 官方 SKILL.md、4.3K 安装、230 stars、原始仓库 | 来源权威，生产化备选；本题当前不用 Temporal SDK，暂不加整套运行依赖 |
| [Anthropic webapp-testing](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/webapp-testing) | 全文、脚本使用约定、约 175.1K 安装 | 与现有 Playwright 能力重叠；持续轮询页面也不能机械依赖 networkidle |
| [observability-and-instrumentation](https://github.com/addyosmani/agent-skills/tree/1be8e34187e34647bb83adc3a1323b26ae6f6abe/skills/observability-and-instrumentation) | 观察问题、日志、RED/USE、trace、runbook；约 43.6K 安装、105,176 stars | 覆盖更广且示例偏 TS；当前 Python 技能已覆盖 MVP，避免整套监控要求挤占时间 |
| [DBOS agent-skills](https://github.com/dbos-inc/agent-skills) | 搜索项和仓库元信息，18 stars | 是合理耐久执行备选，但未深入评测；不在两天内同时试多个执行框架 |
| [o11y-dev opentelemetry-skill](https://github.com/o11y-dev/opentelemetry-skill) | 搜索项和仓库元信息，48 stars | 本次未深入审计其内容与许可；已有更直接的 Python 技能 |

GPU 搜索包含 NVIDIA TAO/Kubernetes 等候选。MVP GPU 需求是资源槽与队列观察，依据 NVIDIA 官方 DCGM 文档写真实扩展方案即可，不为加分项额外引入 K8s、Modal 或付费 GPU。

## 阅读后的修正与边界

- `python-background-jobs` 中的数据库写入后再入队示例适合说明流程，但没有自动解决双写一致性；本项目用同一数据库队列事务规避。
- 其“先查状态再写”的幂等示例不足以处理并发领取；本项目补充唯一约束、行锁与 fencing。
- `workflow-orchestration-patterns` 的历史重放规则面向 Temporal；本项目采用数据库检查点恢复，保留两者的实现区别。
- 技能用于开发参考，恢复行为由本项目的数据库和进程测试验证。
- 仅在相应阶段读取需要的技能，避免把全部技能全文装进每轮上下文，降低重复输入和无关约束。

## 实施阶段实际使用

本次另读取并采用已有 `fastapi-expert`（接口校验）、`python-testing-patterns`（真实数据库/子进程故障验证）、`docker-development`（备选容器打包）。它们不计入新增的六项。`playwright` 已阅读，实际页面验收使用 Codex 内置 CUA 浏览器及其 Playwright 定位器，未新增 Chromium 下载或 Node 项目依赖。其余候选未在实现阶段启用。

## v0.2 四项优化

沿用已安装的 python-background-jobs（执行/取消协议）与 python-testing-patterns（真实 PostgreSQL 回归和进程故障）。未新增技能，也未引入 Celery/Redis；技能示例不替代本项目的事务和所有权测试。

## 提交前质量收尾

本轮读取并应用 `pdf:pdf`（读取三页建议、提取文字并渲染核对）及 `python-testing-patterns`（有针对性的 PostgreSQL/HTTP 回归）。沿用现有技能，未新增安装。Prettier 3.6.2 从 npm 官方注册表获取，仅用于本地格式化，存于被忽略的 `.local/tools/`，不引入运行依赖或前端框架。
