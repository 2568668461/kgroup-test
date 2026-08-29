# Kapibala 任务编排服务（全栈；陈楠，单人）

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

## 启动与验证

```bash
# 新机器推荐：PostgreSQL 16 + Alembic + API/看板
cp .env.example .env
docker compose up --build
# http://localhost:8000 ；POST /api/demo/reset 生成五种状态

pytest tests/test_parameters.py
TEST_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/kapibara_test pytest tests
python scripts/concurrency_claim_test.py --evidence
python scripts/idempotency_test.py --evidence   # 需先启动 API
```

本机实测：33 tests passed；认领 `10进程 x 20轮 x 100任务 = 2000`，重复 `0`、遗漏 `0`；五进程重复成功与成功/失败混合上报均只保留一行且最终 success。完整输出见 [`evidence/`](evidence/README.md)。看板展示五种状态，可对 running Step 并发上报 5 次并显示每次响应与该 Step 最终日志数。

实际投入约 2 天（2026-08-28 至 2026-08-29）。按题目规模未引入 Redis/MQ，也未实现 worker lease/心跳：worker 崩溃后任务不会重复认领，但会停在 claimed/running；生产版应在数据库事务中按 lease 超时回收。
