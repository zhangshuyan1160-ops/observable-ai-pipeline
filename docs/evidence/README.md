# 验证证据

- tests.xml：19 项 pytest，本地真实 PostgreSQL；测试函数见 tests/test_pipeline.py。
- live-demo.json：运行中的 HTTP 服务，缓存、手动重试、真实 Worker/API SIGKILL、恢复起点和 ZIP 摘要。
- dashboard.png：本地页面实际截图，显示失败后成功的两次尝试。
- archive-verification.json：源码 ZIP 解压后新建虚拟环境，19 项测试再通过；同一宿主机，非干净操作系统。
- disk-usage.json：开发中一次逻辑文件大小与空闲进程 RSS 快照，非峰值。
- ai-usage-*.json：当前会话累计 counters，阶段差值由相减得到，不对累计快照求和。

只保存演示任务与计数，未导出私人会话、凭据或数据库连接串。未测能力见 failure-matrix；证据日期不表示生产稳定性承诺。

## 本地提交前质量收尾（独立于历史 v0.2）

- `tests-quality.xml`：33 项真实 PostgreSQL / HTTP pytest，0 跳过。
- `tests-frontend-quality.tap`：4 项 Node 原生测试，无 npm 运行依赖。
- `quality-verification.json`：实际创建意图、响应丢失、复制、重试、复用、键盘及视口验证，含任务 ID、两次 HTTP 请求记录、来源关系和代码摘要。
- `quality-desktop.png`：质量收尾时的 1440×1000 页面（后续连线已修复）；`quality-mobile.png` / `quality-mobile-form.png`：390×844 的管线与表单。
- `quality-pending.png`：首次响应丢失后，刷新恢复的原参数锁定状态；`quality-cache.png`：真实缓存来源展开。
- `ai-usage-quality.json`：后续累计用量快照，含审查与修改，不等于本轮单独用量。

浏览器操作由 Codex 执行，不计作候选人的个人手工开发。上述 quality 报告保留当时的源码摘要与发布状态，不能作为后续版本全部源码的验证。历史小时级报告未重跑。

## v0.3 连线修复与发布

- `dag-connectors.png` / `dag-connectors-mobile.png`：修复后的真实管线截图。
- `dag-connectors-verification.json`：三种实际视口、滚动及缩放检查，五个入口箭头的方向、位置和前端源码摘要。
- 最新源码 ZIP 与 SHA-256 位于仓库 `delivery/`，不将 v0.2 的安装报告当作 v0.3 独立安装验证。远程 CI 以 GitHub Actions 对应提交的实际结果为准。
