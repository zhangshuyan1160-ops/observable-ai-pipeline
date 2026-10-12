# 验证

`.venv/bin/pytest -q --basetemp=.local/pytest`：19 项，包含参数化案例。默认自动启动本地真实 PostgreSQL，每例新建/销毁隔离数据库。CI 设置 TEST_DATABASE_URL（需允许 CREATE DATABASE）后使用外部 PostgreSQL，不安装 pgserver。

`test_real_worker_sigkill_checkpoint_recovery` 启动真实 Worker 子进程，等检查点写入后 SIGKILL，再启动新 Worker 验证恢复起点与父步骤未重算。其余测试覆盖幂等、并发领取、事件事务/分页、缓存冲突/失效、租约 fencing、任务/步骤超时、预算和资源槽。

浏览器人工式自动操作证据和 HTTP 多进程演示记录在 docs/evidence；它们与 pytest 分开。Docker、DB 网络分区、真实 GPU、小时级压测未覆盖，详见 failure-matrix。

## 提交前质量收尾的前端验证

运行 `node --test tests/frontend/create-intent.test.mjs`（Node 22+，无 npm 依赖）。pytest 中 `test_browser_intent_lost_response_keeps_one_database_task` 启动独立 API，在真实 PostgreSQL 上执行前端意图模块：丢弃首次已成功响应、重建控制器、重试并检查只有一条任务/创建事件，另验证 409。通过 NODE_BIN 指定 Node 路径；无 Node 时此项明确跳过，CI 安装 Node。

`test_cache_lineage_uses_producing_attempt_and_historical_hit` 验证二次/三次缓存仍指向原始生产者，且产物后续丢失不会把历史校验误称为当前健康。浏览器响应丢失夹具见 `scripts/response_loss_proxy.py`；页面操作证据见 `docs/evidence/quality-verification.json`。
