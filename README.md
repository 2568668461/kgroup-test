# Kapibara 任务编排服务（全栈；陈楠（单人））

FastAPI + PostgreSQL 的任务编排引擎：三层参数合并（L1 任务 / L2 组 / L3 步骤）、
`FOR UPDATE SKIP LOCKED` 原子认领、`claim_token` 所有权校验、幂等完成上报。

技术栈：Python 3.12 · FastAPI · SQLAlchemy 2.0 + Psycopg 3 · PostgreSQL 16 ·
Alembic · Jinja2 + HTMX（本地内置，无 CDN 依赖）· Pytest · Docker Compose。

## 架构

```
浏览器 (HTMX, 2s 轮询) ──> FastAPI (app/api)
                              │  app/services  状态机 + 参数合并
                              │  app/domain    纯函数（无 DB 依赖）
                              │  app/repositories  行锁 / SKIP LOCKED / UPSERT
                              ▼
                        PostgreSQL 16 (JSONB + CHECK/UNIQUE 约束)
                              ▲
     worker 进程（HTTP 或直连 psycopg）多进程并发认领/上报
```

三项核心正确性机制：

1. **原子认领**：单条 SQL（CTE + `FOR UPDATE SKIP LOCKED` + UPDATE 同事务），
   多进程/跨机器无重复认领，无任务时返回 204。
2. **参数快照冻结**：任务进入 `running` 时锁定任务与组行，读取一次 L2 存入
   `group_parameters_snapshot`，计算各步骤 `resolved_parameters`；之后组配置
   改动不影响已启动任务。
3. **幂等完成上报**：`UNIQUE(task_id, step_sequence)` + `ON CONFLICT` 单调合并
   （`success = old OR new`，`completed_at` 取最早）。成功永不降级，失败可重试升级。

状态机：`pending → claimed → running → done / failed`。
失败重试成功后任务状态单调重算。数据层另有
`ck_tasks_status` / `ck_tasks_claim_consistency` CHECK 约束兜底。

## 参数合并规则（纯函数 `app/domain/parameters.py`）

- L2 组 override 无条件覆盖 L1（**空字符串是有效值**，会覆盖 base）；
- L3 步骤值为 `""` 时忽略该 key，保留上一时刻有效值（粘性传播）；
- L3 非 `""` 覆盖当前值，后续步骤继承；后续步骤可再次覆盖；
- 每步保存独立深拷贝快照；输入字典永不被原地修改；
- 数字 / 布尔 / `null` / 字符串类型不混淆（仅精确的 `""` 触发回退）。

边界用例见 `tests/test_parameters.py`（15 个用例）。

## 启动

```bash
# 方式一：Docker（推荐给评审方 / 新机器，一条命令）
cp .env.example .env      # 按需修改
docker compose up --build # PostgreSQL 16 + 迁移 + 服务
# 打开 http://localhost:8000 （看板），POST /api/demo/reset 重建演示数据

# 方式二：无 Docker 本地开发（Windows，仓库自带便携 PostgreSQL + vendor 依赖）
pip install -r requirements-dev.txt --target vendor   # 首次一次
python scripts/dev_run.py --reset                     # 起 PG → 迁移 → 服务 → 重建演示数据
# 打开 http://127.0.0.1:8000 ；--port 改端口，--no-pg 表示 PG 已由外部提供
```

## 测试

```bash
pytest tests/test_parameters.py        # 纯函数单元测试（无需 DB）
TEST_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/kapibara_test \
  pytest tests/                        # 全量：真实 PostgreSQL 集成测试（不用 SQLite，DB 不可达时自动跳过）
python scripts/concurrency_claim_test.py --evidence   # 10 进程 × 20 轮 × 100 任务认领
python scripts/idempotency_test.py --evidence         # 5 进程并发幂等上报（需服务已启动）
ruff check .                           # 格式与静态检查
```

并发脚本用 `multiprocessing`（spawn）+ 每进程独立连接，结果存 `evidence/`，
完整输出见 [`evidence/README.md`](evidence/README.md)。
实际耗时（本机 Windows + 便携 PostgreSQL 16）：参数单测 <1s；集成测试 ~8s；
认领压测 20 轮 ~1 分钟；幂等压测 ~5s。

## 接口

`POST /api/groups`、`POST /api/tasks`、`POST /api/workers/{id}/claim-next`（空时 204）、
`POST /api/tasks/{id}/start`、`POST /api/tasks/{id}/steps/{seq}/complete`、
`GET /api/tasks`、`GET /api/tasks/{id}`、`POST /api/demo/reset`（仅开发模式）。
完成上报响应含 `outcome: created | upgraded | duplicate_no_change`。

看板：任务表格（2 秒 HTMX 轮询）、任务详情（L1/L2/L3/解析参数/日志）、
「五次并发完成上报」按钮（`Promise.allSettled` 同时发 5 个请求并展示各自结果与
日志行数——现场演示用；正式结论以独立多进程脚本为准）。

## 明确未实现（取舍）

- **worker 租约 / 心跳 / 失联回收**：题目未要求。生产环境会增加 lease TTL、心跳与
  超时重新入队，且重新入队同样必须在数据库事务中完成（置回 `pending` 并清空
  `claim_token`，受 `ck_tasks_claim_consistency` 约束保护）。
- 不接 Gemini API；仓库无任何密钥（`.env` 已被 `.gitignore` 排除，
  仅有 `.env.example` 变量名）。
