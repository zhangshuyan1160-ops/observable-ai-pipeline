# 故障注入与验收矩阵

已实施并通过 19 项 pytest，以及真实运行服务的 HTTP 故障演示。下面保留最初验收设计，并在报告表逐项注明证据及未覆盖范围。一次普通任务成功不能代替所有故障证据。

| ID / 优先级 | 场景与注入 | 预期行为 | 必须观察的证据 |
| --- | --- | --- | --- |
| F01 / P0 | 无故障完整执行 | 扇出独立执行，合并等待两个父节点 | 步骤起止顺序；实际可读产物 |
| F02 / P0 | stylize 首次抛可重试错误，关闭缓存 | 退避后重试，已完成父步骤不重算 | attempts ≥ 2；父产物摘要/attempt 不变；next_run_at |
| F03 / P0 | probe 输入无效 | 不自动重试，后代 BLOCKED，任务最终 FAILED | 永久错误类别；attempt 只有 1 次；无 GPU 分配 |
| F04 / P0 | 每次执行都临时失败 | 达到每轮最多 3 次后停止 | 三次历史保留，终态 FAILED，无无限重试 |
| F05 / P0 | 运行中对 Worker 真实 SIGKILL / kill 容器 | 租约过期后由另一个/重启后的 Worker 接手 | 旧 attempt INTERRUPTED，新 worker/attempt；已完成步骤未重跑 |
| F06 / P0 | 关闭所有 Worker，保留 DB，之后重新启动 | 停止期间仍可查状态；重启后扫描恢复 | 数据库状态和产物仍在；不谎称无 Worker 时可执行 |
| F07 / P0 | 旧 attempt 租约过期后模拟迟到提交 | 旧 token 更新 0 行；新结果不被覆盖 | stale commit 拒绝记录；当前产物摘要不变 |
| F08 / P0 | API 进程重启，Worker 继续 | 计算继续；恢复查询一致 | API 重启前后相同 task ID 与历史 |
| F09 / P0 | stylize 第 3 批途中杀 Worker | 从已提交的第 2 批之后继续 | checkpoint 摘要；新 attempt 恢复起点；最多重做一个未提交批次 |
| F10 / P0 | 同输入/版本/seed，用新的请求幂等键创建第二任务 | 新 task ID；适用步骤 CACHED | cache 来源 artifact；不产生对应计算 attempt |
| F11 / P0 | 同任务输入，只改 stylize 的模型版本/seed | 上游仍可复用，stylize 及下游失效 | 缓存命中与失效边界精确对应 DAG |
| F12 / P0 | 删除或损坏一个缓存文件，再提交同输入 | 失效缓存，重算受影响部分 | checksum/不存在事件；不会直接返回成功引用 |
| F13 / P0 | 写完产物但提交状态前崩溃 | 文件不意味着成功；恢复可安全重算 | 无假成功；不可变路径；孤儿文件可识别 |
| F14 / P0 | 两个 Worker 同时抢同一步 | 只能一个有效领取；无双重当前 attempt | 并发数据库集成测试、唯一约束与锁证据 |
| F15 / P0 | 失败后手动重试；重复点击 retry | 新 retry round，保留旧历史；重复请求不创建多个活跃 attempt | 旧错误可查、成功父步骤保持；操作冲突返回清晰 |
| F16 / P0 | 心跳继续但 mock 业务无进展直到超时 | attempt deadline 到达后超时/重试，不能无限续租 | heartbeat 与 progress 不同；TIMED_OUT 历史 |
| F17 / P0 | 同请求幂等键并发 POST；再用相同键但不同请求体 | 相同体返回同任务；不同体拒绝冲突 | 数据库只有一个任务；冲突可见 |
| F18 / P0 | 查历史时并发事务提交，分页/刷新 | 按任务内 seq 不丢不重；查询可去重 | 事件序号与事务提交顺序；状态和事件一致 |
| F19 / P1 | 两个任务争抢一个 GPU mock 槽 | 一运行一等待；等待不消耗尝试 | slot 占用最多 1；等待时长；明确 MOCK 标识 |
| F20 / P1 | GPU 请求大于总显存；模拟确定性 OOM | 明确不可调度/资源失败，不无限排队重试 | 资源需求与容量、错误分类、退避期间释放槽 |
| F21 / P0 | 检查点版本不符或批次文件损坏 | 拒绝错误复用；从可验证位置恢复或整步重算 | 失效原因，最终产物正确，无伪造进度 |
| F22 / P1 | DB 临时断开/恢复 | 无法续租则放弃所有权；恢复后按租约处理 | 不持有虚假成功；连接恢复与重试事件 |
| F23 / P1 | 两个任务同 cache key 同时未命中 | 可重复计算；缓存唯一引用不被破坏 | 唯一 key 冲突处理，不声称 single-flight |

## 测试层次

- 状态机单元测试：允许/拒绝的转换、错误分类、DAG 无环、指纹参数边界、重试预算。
- PostgreSQL 集成测试：领取、事件和状态同事务、token 校验、幂等创建、事件分页顺序；不要用 SQLite 代替验证 PostgreSQL 锁语义。
- 进程故障测试：启动真实 Worker 子进程/容器，等待持久化 checkpoint 后再 kill，使用条件等待而非固定长 sleep。
- 浏览器验收：提交、选择步骤、查看失败原因与 attempts、刷新后状态仍在、缓存标识清楚、离线/空状态可理解。

每个场景使用独立 task、artifact 目录和可追溯参数。故障注入配置不进入结果 fingerprint；重试/中断用例关闭缓存，缓存用例再显式开启。测试默认短时间模式，但不把虚拟时钟测试说成小时级真实压测。

## 失败案例如何写进最终说明

“注入一次失败后成功恢复”证明系统处理某类故障，不等于开发过程中真的犯了一个 bug。最终复盘分别写：

1. 已验证故障：注入方式、观察结果、对应测试。
2. 实际缺陷：当时的错误假设、复现、修复与回归。
3. 未实现能力：例如真实 GPU、节点分区、存储永久损坏，说明范围和原因。

不提前虚构“遇到缓存 bug 并修复”等故事。当前真实遇到的环境问题记录在 [worklog.md](worklog.md)。

## 实际验收报告（2026-10-12）

证据：[tests.xml](evidence/tests.xml)、[live-demo.json](evidence/live-demo.json)、[dashboard.png](evidence/dashboard.png)。测试函数均位于 tests/test_pipeline.py。

| 编号 | 结果 | 实际证据 / 范围 |
| --- | --- | --- |
| F01 | 通过 | happy_path_real_files_and_dependency_order；live-demo 可下载 ZIP 摘要一致 |
| F02 | 通过 | transient_retry_preserves_parents；浏览器首失败/再成功的两次记录 |
| F03 | 通过 | permanent_failure_blocks_without_retry 两组参数化输入 |
| F04、F15 | 通过 | budget_exhaustion_then_manual_retry_preserves_history；真实 API 再跑成功 |
| F05、F09 | 通过 | real_worker_sigkill_checkpoint_recovery + live-demo；实际杀进程后新 Worker 从 4 帧继续，父步骤仍一次 |
| F06 | 部分覆盖 | SIGKILL 测试在无运行中 GPU Worker 时保留状态，随后启动替代者；整套 API/DB/全部 Workers 停止后重启未独立自动验收 |
| F07、F13 | 通过协议验证 | stale_fencing_heartbeat_checkpoint_complete_and_orphan_file；模拟“文件已写、提交前失联”窗口，未在该指令点实杀进程 |
| F08 | 通过 | API 工厂重建测试 + live-demo 实际 API SIGKILL，新 PID 查询同一任务历史 |
| F10、F11 | 通过 | cache_reuse_and_model_invalidation；参数指纹单测；真实 HTTP 六步缓存 |
| F12 | 通过 | corrupt_cached_artifact_recomputed；文件损坏后校验失效、重算 |
| F14、F17 | 通过 | concurrent_claim_and_request_idempotency；六个竞争者只一有效领取 |
| F16 | 通过 | attempt_timeout_with_live_heartbeats；心跳不能无限延长 deadline |
| F18 | 通过 | event_cursor_waits_for_commit_order + rollback_does_not_publish_event_or_state；未提交事务不可见、分页连续 |
| F19 | 通过 | gpu_slot_waiting_capacity_and_release；等待不消耗 attempt、失败释放槽 |
| F20 | 部分覆盖 | 超容量请求终止已验证；没有真实 CUDA OOM 或降批重试测试 |
| F21 | 通过 | corrupt_checkpoint_is_invalidated；指纹/schema/文件校验，不复用损坏帧 |
| F22 | 未验收 | Worker 代码会捕获数据库异常并放弃提交；未做真实 DB 断网/恢复注入，不能宣称该故障验证通过 |
| F23 | 通过 | concurrent_cache_miss_publishes_one_valid_reference；允许重复计算，唯一缓存引用有效 |

另测 overall_deadline_without_workers：没有执行进展也能由回收扫描终止超过总体时限的任务。所有测试使用短时配置；没有小时级稳定性或吞吐压测。

浏览器验证：创建“首次失败”任务成功；切换步骤后资源/事件保留；查看失败和成功两次记录、动画与下载入口；检查时无 console error/warn。移动端截图和全浏览器兼容性未完整验收。
