# 题目一（全栈方向）：任务调度看板

FastAPI + PostgreSQL 的任务调度核心与极简看板，覆盖三层参数合并、跨进程任务认领和幂等完成上报。

## 设计与取舍

```text
Browser (HTMX 2s polling) -> FastAPI API -> service state machine
                                           -> repository SQL -> PostgreSQL 16
```

选择 Python 是因为我最熟悉其 Web/测试生态，能在两天内把时间集中到事务正确性；并发安全由 PostgreSQL 行锁与约束保证，不依赖 GIL 或进程内锁。Windows 压测使用 `multiprocessing` 的 `spawn`：10 个真实进程各自创建 SQLAlchemy session/数据库连接，并直接调用正式 `task_service.claim_next()`。

- **参数**：启动任务时冻结 L2 组快照；L2 `""` 是有效值，L3 `""` 保留此前有效值；L3 非空覆盖向后粘性传播。纯函数深拷贝输入和每步快照，15 个边界测试覆盖新 key、嵌套值、`null`/布尔/数字及多次覆盖。
- **认领**：单条 CTE SQL 在一个请求事务内完成 `FOR UPDATE SKIP LOCKED` 选取与 UPDATE；`claim_token` 校验后续写操作。CHECK 约束保证 pending 时 owner/token 都为空，其他状态两者都存在。
- **幂等**：`UNIQUE(task_id, step_sequence)` + `ON CONFLICT`；`success = old OR new`，时间取最早。task 行锁把越序检查、日志合并和状态推进放在同一事务。

状态机：`pending -> claimed -> running -> done / failed`。失败 Step 可由成功重试升级；多 Step 任务恢复 running 时清除旧 `finished_at`。

## 启动、交互与验证

### 从零启动（Docker）

#### 1. 安装 Docker Desktop（Windows）

从 [Docker Desktop for Windows 官方页面](https://docs.docker.com/desktop/setup/install/windows-install/) 下载并安装 Docker Desktop：

- 安装程序选择默认的 **WSL 2 based engine**；Windows 功能中的虚拟机平台和 WSL 2 按安装程序提示启用；
- 确认 BIOS/UEFI 已开启 CPU 虚拟化；安装结束后按提示重启 Windows；
- 启动 Docker Desktop，等待状态显示 **Engine running**；
- 重新打开 PowerShell，确认命令可用：

```powershell
docker --version
docker compose version
```

#### 2. 克隆并启动项目

```bash
git clone https://github.com/2568668461/kgroup-test.git
cd kgroup-test
cp .env.example .env                 # Windows PowerShell 可跳过此行
docker compose up --build            # 首次构建；会自动迁移并启动 API
```

打开 <http://localhost:8000>。启动后调用一次 `POST /api/demo/reset`（可用浏览器开发者工具、curl 或下方 PowerShell 命令），看板就会生成五种状态的固定演示任务：

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/demo/reset
```

首页任务表展示 `pending / claimed / running / done / failed`；`running` 行可直接点击“五次并发上报”，详情页也有同一入口。按钮发出 5 个独立 HTTP 请求，随后读取数据库确认目标 Step 仍只有 1 条日志。表格和详情每 2 秒自动刷新。

### 测试与结果证据

```bash
pytest tests/test_parameters.py
TEST_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/kapibara_test pytest tests
python scripts/concurrency_claim_test.py --evidence
python scripts/idempotency_test.py --evidence   # 需先启动 API
```

Windows PowerShell（每条命令单独执行）：

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/kapibara_test"
& .\.venv\Scripts\python.exe -m pytest tests/test_parameters.py
& .\.venv\Scripts\python.exe -m pytest tests

# 多进程认领：使用测试数据库，10 进程 × 20 轮
$env:KAPIBARA_DSN = "postgresql://app:app@localhost:5432/kapibara_test"
$env:DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/kapibara_test"
& .\.venv\Scripts\python.exe scripts/concurrency_claim_test.py --evidence

# 幂等上报：保持 Docker app 容器运行，目标 API 为 localhost:8000
$env:KAPIBARA_BASE_URL = "http://127.0.0.1:8000"
& .\.venv\Scripts\python.exe scripts/idempotency_test.py --evidence
```

幂等测试必须在 Docker 的 `app` 容器运行时执行；认领测试和普通测试连接 `kapibara_test`。命令不要粘成一行。

如果 Windows 主机的 `5432` 被其他 PostgreSQL 服务占用或认证配置不同，可直接在 Docker 网络内运行认领压测（推荐）：

```powershell
docker compose exec -e KAPIBARA_DSN=postgresql://app:app@db:5432/kapibara_test app python scripts/concurrency_claim_test.py --evidence
```

运行认领压测前，先在 Docker 网络内为 `kapibara_test` 执行一次迁移：

```powershell
docker compose exec -e DATABASE_URL=postgresql+psycopg://app:app@db:5432/kapibara_test app alembic upgrade head
```

完整运行结果在 [`evidence/claim_concurrency.txt`](evidence/claim_concurrency.txt)（10 进程 × 20 轮 × 100 任务，重复 0、遗漏 0）和 [`evidence/idempotency.txt`](evidence/idempotency.txt)（5 进程重复/混合上报，日志始终 1 行，最终 success）；证据说明见 [`evidence/README.md`](evidence/README.md)。本机单元与集成测试共 33 项通过。

实际投入约 2 天（2026-08-28 至 2026-08-29）。按题目规模未引入 Redis/MQ，也未实现 worker lease/心跳：worker 崩溃后任务不会重复认领，但会停在 claimed/running；生产版应在数据库事务中按 lease 超时回收。
