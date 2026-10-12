# 验证

`.venv/bin/pytest -q --basetemp=.local/pytest`：19 项，包含参数化案例。默认自动启动本地真实 PostgreSQL，每例新建/销毁隔离数据库。CI 设置 TEST_DATABASE_URL（需允许 CREATE DATABASE）后使用外部 PostgreSQL，不安装 pgserver。

`test_real_worker_sigkill_checkpoint_recovery` 启动真实 Worker 子进程，等检查点写入后 SIGKILL，再启动新 Worker 验证恢复起点与父步骤未重算。其余测试覆盖幂等、并发领取、事件事务/分页、缓存冲突/失效、租约 fencing、任务/步骤超时、预算和资源槽。

浏览器人工式自动操作证据和 HTTP 多进程演示记录在 docs/evidence；它们与 pytest 分开。Docker、DB 网络分区、真实 GPU、小时级压测未覆盖，详见 failure-matrix。
