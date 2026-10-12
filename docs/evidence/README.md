# 验证记录索引

报告按运行时的代码快照保存。查看结果时，以报告中的版本、commit、源码摘要和执行时间为准。

## 当前功能与页面

| 文件 | 内容 |
| --- | --- |
| tests-quality.xml、tests-frontend-quality.tap | 本地 33 项 pytest 和 4 项 Node 测试，均无跳过 |
| quality-verification.json | 响应丢失、参数复制、手动恢复、缓存来源、键盘与轮询交互 |
| quality-desktop.png、quality-mobile.png、quality-mobile-form.png | 质量完善阶段的页面截图 |
| quality-pending.png、quality-cache.png | 创建结果待确认和缓存来源详情 |
| dag-connectors-verification.json | 三种实际视口、滚动、箭头方向及节点对齐检查 |
| dag-connectors.png、dag-connectors-mobile.png | 最新管线截图 |
| release-v0.3-verification.json | v0.3 首次发布的 Linux CI、远程 tree 和匿名下载校验 |

页面检查通过 Codex 浏览器工具完成。quality 报告对应连线修复前的代码；连线的后续检查单列在 dag-connectors 报告中。

## 恢复与历史版本

| 文件 | 内容 |
| --- | --- |
| recovery-hour.json | 3607.93 秒单任务演练，含数据库停机与全栈重启 |
| recovery-short.json | 短时数据库与进程中断恢复 |
| live-demo.json | HTTP 创建、缓存、手动重试、API/Worker SIGKILL 与产物摘要 |
| tests.xml、tests-v0.2.xml | v0.1 的 19 项测试、v0.2 的 31 项测试 |
| ci-v0.2.json、archive-v0.2-verification.json | v0.2 Linux CI 和独立安装验证 |
| archive-verification.json | v0.1 源码包的独立安装验证 |
| priority-one-ui.json、priority-one-dashboard.png | 取消、搜索、归档和分页演示 |

小时级报告覆盖单任务持续执行；真实 GPU、高并发压力和 Compose 运行尚未验收。具体范围见 [验收矩阵](../failure-matrix.md)。

## 交付包、用量和时间

- `delivery/manifest.json` 是当前下载包的源码快照与 SHA-256 入口。`archive-v0.3-*.json` 记录各次打包检查，旧报告中的摘要属于当时的包。
- `project-timeline.json`：项目讨论起点、发布终点及经过时间。
- `ai-usage-*.json`：会话累计计数。缓存输入属于总输入，快照不能相加。
- `disk-usage.json`：开发中的一次空间与空闲内存快照，最新建议见 [环境说明](../environment.md)。

这里只保存项目验证数据、时间戳和计数；数据库、凭据与会话正文不进入源码包。
