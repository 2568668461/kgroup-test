# Kapibala 任务编排服务

FastAPI + PostgreSQL 的任务调度核心与极简看板，覆盖三层参数合并、跨进程原子认领和幂等完成上报。

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

```bash
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

PowerShell 设置测试数据库变量：

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/kapibara_test"
python -m pytest tests
```

完整运行结果在 [`evidence/claim_concurrency.txt`](evidence/claim_concurrency.txt)（10 进程 × 20 轮 × 100 任务，重复 0、遗漏 0）和 [`evidence/idempotency.txt`](evidence/idempotency.txt)（5 进程重复/混合上报，日志始终 1 行，最终 success）；证据说明见 [`evidence/README.md`](evidence/README.md)。本机单元与集成测试共 33 项通过。

实际投入约 2 天（2026-08-28 至 2026-08-29）。按题目规模未引入 Redis/MQ，也未实现 worker lease/心跳：worker 崩溃后任务不会重复认领，但会停在 claimed/running；生产版应在数据库事务中按 lease 超时回收。
