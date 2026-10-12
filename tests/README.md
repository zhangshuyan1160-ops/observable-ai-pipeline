# 测试与演示

当前版本包含 33 项 pytest 和 4 项 Node 测试。本地与 Linux CI 均已通过，结果见 [发布记录](../docs/evidence/release-v0.3-verification.json)。

```bash
.venv/bin/pytest -q --basetemp=.local/pytest
node --test tests/frontend/create-intent.test.mjs
```

pytest 默认启动本地 PostgreSQL，每例使用独立数据库。CI 通过 `TEST_DATABASE_URL` 使用 PostgreSQL 16 service；数据库用户需要 CREATE DATABASE 权限。HTTP/前端联合测试需要 Node 22+，可用 `NODE_BIN` 指定路径；缺少 Node 时该项跳过，CI 已安装 Node。

## 覆盖范围

- `test_pipeline.py`：幂等、并发领取、状态与事件事务、缓存校验、fencing、超时、GPU 槽，以及 Worker SIGKILL 后从检查点恢复。
- `test_task_controls.py`：调度公平性、取消竞态、线程停止确认、搜索与分页、归档、schema v2 迁移。
- `test_submission_quality.py`：创建响应丢失后仍只有一个任务，以及缓存多次命中后的原始生产者追溯。
- `frontend/`：稳定创建意图、存储失败、响应异常、完整参数复制，以及与真实 HTTP API 的联合验证。

独立进程演练与浏览器检查保存在 docs/evidence。数据库停机、全栈重启和一小时执行已验证；网络分区、真实 GPU、Compose 及高并发压力尚未覆盖。详见 [故障验收矩阵](../docs/failure-matrix.md)。
