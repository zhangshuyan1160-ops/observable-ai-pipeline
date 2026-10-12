# 验证证据

- tests.xml：19 项 pytest，本地真实 PostgreSQL；测试函数见 tests/test_pipeline.py。
- live-demo.json：运行中的 HTTP 服务，缓存、手动重试、真实 Worker/API SIGKILL、恢复起点和 ZIP 摘要。
- dashboard.png：本地页面实际截图，显示失败后成功的两次尝试。
- archive-verification.json：源码 ZIP 解压后新建虚拟环境，19 项测试再通过；同一宿主机，非干净操作系统。
- disk-usage.json：开发中一次逻辑文件大小与空闲进程 RSS 快照，非峰值。
- ai-usage-*.json：当前会话累计 counters，阶段差值由相减得到，不对累计快照求和。

只保存演示任务与计数，未导出私人会话、凭据或数据库连接串。未测能力见 failure-matrix；证据日期不表示生产稳定性承诺。
