# 脚本

在项目根目录运行。

| 脚本 | 用途 | 改动范围 |
| --- | --- | --- |
| bootstrap.sh | 安装锁定依赖与项目 | .venv、.local/pip-cache |
| dev.py | 启停 PostgreSQL/API/Workers；只控制自有子进程 | .local |
| verify_demo.py | 通过 HTTP 演示并执行真实 SIGKILL | 本地演示任务、evidence/live-demo.json |
| package.py | 干净 Git HEAD 生成源码 ZIP 与 SHA-256 | dist |
| clean.py | 默认 dry-run；明确 --apply 清理指定缓存 | 不删除数据库、产物、源码、ZIP |
| snapshot_ai_usage.py | 当前会话累计 token 快照 | 指定的 JSON；不导出对话正文 |
