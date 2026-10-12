# v0.2：第一优先级四项优化

范围仅为调度公平性、恢复证据、任务取消、历史任务管理。沿用本地 PostgreSQL、FastAPI、独立 Worker 和原生页面，没有新增服务或模型依赖。工程预算约 8–14 小时，实际自动运行时间和个人主动工时分开记录。

## 1. 调度公平性

原实现在筛选资源类型、重试时间之前先截取最早 32 个活跃任务。用真实上游产物构造 33 个仅等待 GPU 的任务后，后续 CPU 任务无法被领取；33 个未来重试任务也会挡住后续新任务。两条回归测试在修改前均失败。

修复为先筛选“存在匹配 Worker 资源、状态可领取、已到执行时间的步骤”的任务，再按创建时间和 ID 取最多 32 个，并继续使用任务→步骤→资源槽的锁顺序。没有改变至少一次执行语义。相同资源下仍按任务年龄排序，不提供优先级抢占或多租户配额。

## 2. 故障恢复证据

新增 `scripts/verify_recovery.py`，创建自己的临时 PostgreSQL、API、CPU/GPU 子进程与产物目录，不操作正在演示的数据库。

- 保存 4 帧后真实停止数据库，保持 API/Workers 活着；检查 `/readyz` 返回 503，持续超过租约时间后重新启动数据库。
- 保存 8 帧后对脚本持有的 API/Workers 发送 SIGKILL，再停止、重启数据库和所有服务。
- 检查风格化三次尝试为 INTERRUPTED / INTERRUPTED / SUCCEEDED，恢复起点与中断前检查点一致，其他步骤只执行一次，事件序号连续，最终 ZIP 摘要一致，GPU 槽释放。
- 单独的 pytest 子进程在真实产物写完、成功事务提交前被杀死，验证孤儿文件不被当作成功，新尝试使用新的不可变路径。
- Worker 维护扫描与计算分开运行，耗时步骤执行期间仍可回收失联尝试；启动时数据库暂时不可用会重试连接。

```bash
# 短时真实故障演练；需要本地 pgserver 环境
.venv/bin/python scripts/verify_recovery.py --duration 8
# 单个任务约一小时：16 批，每批模拟 225 秒，期间执行上述两次故障
.venv/bin/python scripts/verify_recovery.py --duration 3600 --output docs/evidence/recovery-hour.json
```

短时证据见 [recovery-short.json](evidence/recovery-short.json)。小时级证据 [recovery-hour.json](evidence/recovery-hour.json) 已通过：实际 3607.93 秒，恢复起点 4/8 帧，三次尝试 INTERRUPTED / INTERRUPTED / SUCCEEDED，39 条事件连续，最终 ZIP 摘要一致。报告中的 last_checkpoint_frames=60 是最后一次定期进度采样，最终成功以 status=PASSED 及成功尝试为准。此演练是单任务长时间持久执行验证，不是 GPU 性能或高并发压测。`batch_delay` 只改变模拟等待，不进入缓存指纹。

## 3. 任务取消

`POST /api/tasks/{id}/cancel` 返回 202。请求在任务锁内落库并追加事件；重复取消不重复追加请求或增加尝试。

- 尚未执行/等待资源/等待重试：步骤直接 CANCELLED，不产生虚假 attempt；已经成功的步骤与文件保留。
- 正在执行：任务 CANCELLING，立即禁止后续领取及成功/进度提交；保持运行步骤和资源槽，直到 Worker 确认退出。
- Worker 心跳检测到所有权不可继续后，取消计算协程；有界的线程内文件/图像操作先结束，再发送停止确认。不能把 `asyncio.to_thread` 的协程取消误认为 OS 线程已经停止。
- 停止确认后 attempt/step 记 CANCELLED，释放模拟槽；所有活动步骤结束后 task 记 CANCELLED。
- Worker 失联：租约过期后终止调度，attempt 留 INTERRUPTED / CANCEL_WORKER_LOST，task 的 `cancel_unconfirmed=true`。页面明确标注停止未确认；仅释放模拟槽，不声称真实 GPU 已停止。
- 取消后的任务不可重试原步骤，可以用相同参数新建任务，继续复用允许复用的已成功结果。成功/失败终态取消返回 409。

取消与成功提交由同一个任务行锁确定先后顺序；成功提交先获得锁的步骤可保留成功，取消落库后不能出现新的成功提交。

## 4. 历史任务管理

`GET /api/tasks` 新增 `q`（名称/ID，忽略大小写的字面子串搜索）、`view=active|archived|all`；保留 `status`、`limit` 和 `before` 游标。`active` 表示未归档，包含已结束任务，不代表执行中。获取 limit+1 行准确判断是否有下一页。

页面提供搜索、全部六种任务状态筛选、归档范围、加载更早任务、完整参数与总体截止时间。选中的详情独立于列表筛选，刷新可继续查询较早或已归档的任务。翻页期间出现新任务时按 ID 去重；不宣称跨多次 HTTP 查询的列表是不可变快照。

`POST /api/tasks/{id}/archive?archived=true|false` 只允许已结束任务，重复操作幂等且追加审计事件。归档仅改变列表可见性，不删除历史、产物或共享缓存，也不减少磁盘占用。归档失败任务若要重试，须先恢复到列表。

## 升级与工程组织

`storage/schema.sql` 保留初始 v1，`storage/migrations/002_task_controls.sql` 在同一事务内扩展状态约束并增加取消时间、停止未确认标记、归档时间和查询索引。版本表和 PostgreSQL advisory lock 保证多进程启动时只迁移一次。测试覆盖 v1 历史数据、旧请求幂等键以及并发初始化。

升级本地服务时先停止旧 API/所有 Workers，再启动新版，自动迁移且保留已有任务。旧 Worker 不懂取消协议，因此不支持混合版本滚动升级。数据库迁移不同于工作流定义/检查点跨版本迁移；后两者仍未实现。

新增测试放 `tests/test_task_controls.py`，恢复脚本放 `scripts/`，证据放 `docs/evidence/`，隔离实验数据放 `.local/recovery-*`，均不往项目父目录散放文件。

## 验收

- PostgreSQL 测试：公平性、取消竞态、线程停止确认、取消时失联、GPU 等待取消、重试等待取消、全量搜索、37 条任务分页、归档恢复、v1 数据迁移、旧键幂等重放，以及原有恢复测试。
- 浏览器：真实提交长任务，观察取消中→已取消；归档/搜索/筛选/恢复；35 条归档记录从 30 条加载到 35 条；最早记录刷新后仍可查看；检查时无 console error/warn。
- 实際个人开发/操作时间仍按本人记录，不把自动化验收算成人工开发。

尚未增加任意素材上传、暂停、真实 GPU、孤儿文件 GC、全链路 tracing、多租户鉴权或通用 DAG 编辑；这些不属于本轮四项范围。

源码归档全新环境复核通过：31 项测试、pip check、wheel 内迁移文件核查。Linux CI 同样通过 31 项测试，见 [CI](evidence/ci-v0.2.json) 与 [独立安装证据](evidence/archive-v0.2-verification.json)。
