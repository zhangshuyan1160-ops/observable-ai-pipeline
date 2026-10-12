# 本机环境与实际空间

2026-10-12 实测：Apple M5 / 10 逻辑核 / 16GiB 内存 / macOS 26.6.2，启动前约 709.7GiB 可用空间。足以完成本 MVP，无需租机。

## 实际安装与目录

- 复用 Codex 已有 Python 3.12.14 创建项目 `.venv`；未修改系统 Python 3.9.6。评审运行只需要自己的 Python 3.12+，不依赖作者私有路径。
- FastAPI / psycopg / Pillow / pytest / httpx / ruff 锁定版本见 requirements*.lock。
- pgserver 0.1.4 自带 PostgreSQL 16.2（本地 Unix socket），数据库与日志放 `.local`。这是真实 PostgreSQL，无 SQLite 替代。
- 原生 HTML/CSS/JS，无前端 npm 依赖。复用 Codex 浏览器验收，没有新增浏览器下载。
- 未安装 Docker Desktop、CUDA、PyTorch、大模型权重、Redis 或 Kubernetes。提供 Compose 备选文件但未运行验证。
- 6 项新增 skills 共约 92KiB，位于用户级 Codex skills 目录；运行项目不需要 skills。

## 开发期间实测快照

| 内容 | 逻辑文件大小（约） |
| --- | --- |
| .venv，含本地数据库二进制与测试工具 | 123MiB |
| .local，数据库、测试目录、缓存、演示产物 | 172MiB |
| 源码、测试、脚本、文档和 Git | 小于 2MiB |
| 合计 | 约 0.3GiB，随演示任务增长 |

[原始快照](evidence/disk-usage.json)。这是一次开发中测量，不是最终清理后值；不含预先存在的 Codex 与共享 Python。启动器/API/3 个 Worker/数据库及子进程的空闲 RSS 合计约 218MiB（共享页可能重复统计），并非峰值或性能保证。

建议本地为这个轻量版本预留 1–2GB，用于重复安装和少量测试。最初 5–12GB 估算包含 Docker Desktop/镜像/浏览器；最终复用运行时并使用原生 PostgreSQL，因此无需这部分新增开销。若以后选择 Docker 或处理真实视频，应重新计量。

## 交付后节省空间

源码、测试、文档和截图保留在一个 Git 工程；`dist` 只放源码 ZIP 和摘要清单。发布并验证后先清理 `.local/pip-cache`、pytest 产物等可重建内容；若不再演示，停止服务后可删除 `.venv`，通过 bootstrap 恢复。

`python3 scripts/clean.py` 默认只显示将清理什么，添加 `--apply` 才执行；`--dependencies` 额外清理 .venv。脚本不删除 PostgreSQL 数据、有效产物、源码或压缩包，不运行全局 Docker/pip 清理。**上传 GitHub 本身不会自动释放本机空间。**

GitHub 保存可浏览源码，ZIP 为额外备份；不上传 .venv/.local/数据库/凭据。源码包不包含运行历史备份，因此不能把删除数据库视作可从源码恢复所有演示记录。

## 配置下限与可选服务器

建议 Python 3.12+、2 核 CPU、4GB 系统内存、1–2GB 开发余量起步（估算）；本机 16GB 已验证。只有要求持续公网演示才需要服务器，可从 2vCPU/4GB/40GB SSD 起步，并加认证、限流、TLS、数据库补丁维护与备份。题目允许公开源码，不需要为本次交付租服务器。

本机休眠时计算会暂停；恢复执行不等于电脑合盖后仍持续服务。真实 GPU 模型和大视频不在本次资源测量范围内。

### 打包复核后的清理结果

全新虚拟环境复核通过后，清理独立复核目录、下载缓存与测试临时文件约 255MiB。`du -sh` 显示主项目约 **269MiB**（.venv 135MiB、.local 133MiB）；源码 ZIP 约 **187KiB**。依赖和持久数据库仍保留，等待公开上传验证后再决定停止演示与清理依赖。此值是磁盘已分配空间，与上表逻辑字节不同。
