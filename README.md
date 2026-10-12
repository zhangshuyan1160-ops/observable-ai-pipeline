# Frameflow · 可恢复的 AI 动画任务管线

[公开源码](https://github.com/zhangshuyan1160-ops/observable-ai-pipeline) · [下载 v0.3.0 ZIP](https://github.com/zhangshuyan1160-ops/observable-ai-pipeline/raw/refs/heads/main/delivery/observable-ai-pipeline-v0.3.0.zip) · [提交说明](docs/submission.md)

面向动画素材处理的任务执行 MVP，重点是步骤重试、中断恢复、结果复用和状态查询。计算与 GPU 使用 MOCK，数据库和文件产物实际持久化。项目总用时约 8 小时（10 小时以内，含 AI 辅助开发、验证、反馈和发布），[时间记录](docs/evidence/project-timeline.json)。

**v0.3.0：稳定创建幂等、完整参数复制、恢复诊断、缓存来源及响应式管线连线。** 本地与 Linux CI 的 33 项 pytest 和 4 项前端测试通过；[改动与验收](docs/submission-quality.md) · [发布校验](docs/evidence/release-v0.3-verification.json) · [GitHub Actions](https://github.com/zhangshuyan1160-ops/observable-ai-pipeline/actions/workflows/ci.yml)。

![任务管线](docs/evidence/dag-connectors.png)

## 五分钟了解

提交动画任务立即返回 ID；6 个步骤按依赖执行。可注入临时错误、重试耗尽、永久错误或超时；真实杀死 Worker 后从每 4 帧的检查点继续。相同输入和版本复用已校验产物，生成可下载的 GIF、PNG 和 ZIP。

```mermaid
flowchart LR
  A[素材检查] --> B[帧索引]
  B --> C[风格化 / GPU mock]
  B --> D[缩略图 / CPU]
  C --> E[合成 GIF]
  D --> F[打包 ZIP]
  E --> F
```

**已实现任务取消、搜索/分页/归档、步骤失败恢复与缓存复用。** v0.3 补充稳定的创建幂等键、完整参数复制、错误/阻塞/恢复诊断及缓存来源。v0.2 已验证队列公平性和数据库中断恢复。 [v0.2 优化与验收](docs/priority-one.md) · [60 分钟真实故障演练](docs/evidence/recovery-hour.json) · [历史 v0.2 的 31 项测试报告](docs/evidence/tests-v0.2.xml) · [真实服务证据](docs/evidence/live-demo.json) · [验收范围与未测项](docs/failure-matrix.md)

## 本地运行（推荐，已在 Apple silicon Mac 验证）

需要 Python 3.12+ 和 Git。系统 Python 3.9 不适用。全部安装到本项目，不修改系统 Python：

```bash
# 在解压后的项目根目录运行；python3.12 必须指向你的 Python 3.12+
PYTHON_BIN=python3.12 sh scripts/bootstrap.sh
.venv/bin/python scripts/dev.py start
```

浏览器打开 http://127.0.0.1:8765 。接口文档 `/docs`，指标 `/metrics`。启动器管理一个 API、一个 CPU Worker、两个竞争单槽资源的 GPU mock Workers。首次初始化本地 PostgreSQL；按 Ctrl+C 停止，重新启动保留任务和产物。

本地演示使用 `pgserver==0.1.4` 提供的 PostgreSQL 16.2。Linux CI 使用 PostgreSQL 16 service。公网运行应使用维护中的数据库版本并配置鉴权；原生 Windows 和 WSL2 路径尚未验证。

```bash
# 另一个终端，均在项目根目录
.venv/bin/python scripts/dev.py status
.venv/bin/python scripts/dev.py interrupt gpu   # 杀死并重启本项目两个 GPU Workers
.venv/bin/python scripts/dev.py interrupt api
.venv/bin/python scripts/dev.py stop
```

已安装独立 PostgreSQL 时：安装 `requirements.lock` 与本项目，导出 `DATABASE_URL` 和 `ARTIFACT_ROOT`，分别运行 `uvicorn pipeline.api:create_app --factory` 及 `python -m pipeline.worker --resource cpu/gpu`。API/Worker 必须使用同一数据库和产物目录。

提供 `docker compose up --build` 备选配置，端口绑定本机，持久卷保存状态和产物。Compose 尚未运行验收，推荐先使用上面的本地启动方式。

## 验证与演示

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
node --test tests/frontend/create-intent.test.mjs   # 验证时需要 Node 22+，运行应用不需要
.venv/bin/pytest -q --basetemp=.local/pytest
# 本地服务启动后运行；会终止并重启本项目 Worker/API
.venv/bin/python scripts/verify_demo.py
# 独立数据库和子进程，不影响正在演示的服务
.venv/bin/python scripts/verify_recovery.py --duration 8
```

测试使用真实 PostgreSQL，每例独立数据库，自动清理。新增前端/真实 HTTP 联合测试需要 Node 22+；可通过 NODE_BIN 指定路径，无 Node 时该项明确跳过。CI 配置 Node 22，并运行 4 项前端测试和 33 项 pytest。CI 使用 PostgreSQL service 和 `TEST_DATABASE_URL`，不依赖 pgserver；远程 CI 状态以 GitHub Actions 实际结果为准。[五分钟演示脚本](docs/demo.md)

## 关键取舍

- PostgreSQL 同时管理状态和就绪队列，用短事务避免队列/数据库双写。适用于本例的低吞吐单机范围。
- 计算放在独立 Worker；心跳租约和 fencing 控制谁可以提交，保证至少一次执行，不承诺 exactly-once 计算。
- 四帧检查点平衡写入成本与故障后重做量；文件先原子写入，再登记成功，孤儿文件不算成功结果。
- 原生界面按 1.2 秒轮询展示数据库事实，保留焦点和详情展开；不增加前端构建依赖。

## 接口示例

```bash
curl -X POST http://127.0.0.1:8765/api/tasks \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-001' \
  -d '{"title":"重试演示","cache_enabled":false,"fault_mode":"transient_once"}'
```

同一个幂等键+同一请求返回原任务；同键不同请求返回 409。GET `/api/tasks/{id}` 查状态和尝试；GET `/api/tasks/{id}/events?after_seq=0` 增量读取事件；POST `/api/steps/{id}/retry` 为失败步骤创建新一轮尝试。新请求键控制新任务，缓存指纹控制是否重算，两者互不替代。

## 任务管理与升级

页面支持名称/ID 搜索、状态筛选、历史分页、参数详情、取消和归档恢复。对应接口：

- `GET /api/tasks?q=关键词&status=CANCELLED&view=all&limit=30`，用返回的 `next_cursor` 作为下一页 `before`。
- `POST /api/tasks/{id}/cancel`：运行中先 CANCELLING，Worker 确认停止后 CANCELLED；失联时明确标记“停止未确认”。
- `POST /api/tasks/{id}/archive?archived=true`：归档已结束任务；`false` 恢复。归档不删除文件。

从 v0.1 升级须先停止全部旧 API/Workers，再启动新版；schema v2 自动迁移且保留历史。不要同时运行不支持取消的旧 Worker。

## 工程目录与节省空间

```text
src/pipeline/      执行器、API、Worker、存储、动画处理与静态页面
scripts/           安装、启动、故障验收、打包、清理和 token 快照
tests/             真实 PostgreSQL 测试
docs/              设计、计划、Skills 来源、提交说明、测试证据
.local/            数据库、产物、下载缓存、日志、临时验证（忽略）
.venv/             可重建依赖（忽略）
dist/              本地构建 ZIP 与 SHA-256 清单（忽略）
delivery/          GitHub 交付 ZIP 与摘要（导出源码包时排除）
```

源码、环境和运行数据集中在这个独立目录。环境配置与空间建议见 [environment.md](docs/environment.md)。GitHub 仅保存源码、文档和交付文件。

```bash
# 必须先提交源码；仅打包 Git HEAD，不打包运行文件
python3 scripts/package.py
# 默认只报告，不删除；--apply 只清理指定的可重建缓存
python3 scripts/clean.py
python3 scripts/clean.py --apply
# 上传和校验完成、停止服务后，需要进一步节省空间时
python3 scripts/clean.py --dependencies --apply
```

清理脚本保留源码、ZIP、数据库和动画产物。源码 ZIP 可解压后用 bootstrap 重建环境；ZIP 不包含本地任务数据库备份。GitHub 仓库保留可浏览源码；压缩包是额外交付物。delivery/manifest.json 记录本地源码快照 commit 与 ZIP 的 SHA-256；GitHub 上传提交可能不同，下载后按 SHA-256 校验文件。

## 说明与边界

- [面试提交简要说明](docs/submission.md)：逐项回应题目。
- [设计](docs/architecture.md)：状态、事务、恢复、GPU 和小时级扩展。
- [实施计划与时间记录](docs/plan.md) · [Skills 检索及新增清单](docs/skills.md) · [工作记录与 AI 用量](docs/worklog.md)。

当前支持单机固定 DAG、至少一次执行和带所有权校验的结果提交。任意素材上传、多租户鉴权、真实 GPU 和多机调度属于后续扩展。已通过一小时单任务恢复演练，高并发压力和 Compose 运行尚未验收。

本项目使用 Codex 辅助开发；分工与 Token 记录见 [提交说明](docs/submission.md)。
