# 并发证据存档

本目录保存真实多进程并发测试的完整输出（`--evidence` 参数追加写入）。

`pytest tests` 是普通单元/集成测试；下面两个脚本是题目要求的独立并发压测，
会启动真实的多进程和独立数据库连接，因此不会被普通 `pytest` 自动执行。

## 认领压测（concurrency_claim_test.py）

命令：`python scripts/concurrency_claim_test.py --evidence`

- 10 个 worker 进程（`multiprocessing` spawn 模式，每进程独立 SQLAlchemy session/连接）
- 每个进程直接调用正式 `task_service.claim_next()`，不复制认领 SQL
- 每轮 100 个 pending 任务，连续 20 轮
- 按每轮实际插入的任务 ID 校验：总认领数 = 2000、重复 = 0、遗漏 = 0、剩余 pending = 0

结果文件：[`claim_concurrency.txt`](claim_concurrency.txt)

## 幂等压测（idempotency_test.py）

命令：先启动服务，然后 `python scripts/idempotency_test.py --evidence`

- 场景 A：5 个独立进程同时上报同一 running step 成功 →
  0 个 5xx、`execution_logs` 恰好 1 行、任务状态只推进一次
- 场景 B：5 个独立进程混合上报（成功/失败交错）→ 最终日志为 success（单调合并）

结果文件：[`idempotency.txt`](idempotency.txt)

## 重跑

```bash
docker compose up --build -d
KAPIBARA_DSN=postgresql://app:app@localhost:5432/kapibara python scripts/concurrency_claim_test.py --evidence
python scripts/idempotency_test.py --evidence
```

Windows PowerShell（每条命令单独执行，需先启动 Docker Compose）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:KAPIBARA_DSN = "postgresql://app:app@localhost:5432/kapibara_test"
$env:DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/kapibara_test"
.\.venv\Scripts\python.exe scripts/concurrency_claim_test.py --evidence
$env:KAPIBARA_BASE_URL = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe scripts/idempotency_test.py --evidence
```

认领结果追加到 [`claim_concurrency.txt`](claim_concurrency.txt)，幂等结果追加到
[`idempotency.txt`](idempotency.txt)。
