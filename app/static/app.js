// Kapibara demo interactions — plain fetch, no framework required.

const resultBox = () => document.getElementById("api-result");

function show(html) {
  const box = resultBox();
  if (box) box.innerHTML = html;
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function workerId() {
  return document.getElementById("worker-id")?.value || "worker-demo-9";
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { status: res.status, data };
}

async function claimNext(taskId) {
  const { status, data } = await postJSON(`/api/workers/${encodeURIComponent(workerId())}/claim-next`);
  if (status === 204) {
    show("<b>claim-next:</b> 204 No Content — 队列已空");
    return;
  }
  show(
    `<b>claim-next:</b> HTTP ${status} — 认领任务 #${esc(data.task_id)} ` +
    `「${esc(data.name)}」 status=${esc(data.status)}<br>` +
    `<code>claim_token=${esc(data.claim_token)}</code>` +
    (taskId && data.task_id === taskId ? "<br>（即当前页面任务，可点击启动）" : "")
  );
}

async function startTask(taskId, token) {
  const { status, data } = await postJSON(`/api/tasks/${taskId}/start`, { claim_token: token });
  show(
    `<b>start:</b> HTTP ${status}<pre>${esc(JSON.stringify(data, null, 2))}</pre>`
  );
}

async function resetDemo() {
  const { status, data } = await postJSON("/api/demo/reset");
  show(`<b>demo/reset:</b> HTTP ${status} — ${esc(data?.detail ?? "")}`);
}

// Five concurrent completion reports against the current running step.
async function completeFive(btn) {
  const taskId = btn.dataset.taskId;
  const seq = Number(btn.dataset.seq);
  const token = btn.dataset.token;
  btn.disabled = true;
  const url = `/api/tasks/${taskId}/steps/${seq}/complete`;
  const body = { claim_token: token, success: true };

  const results = await Promise.allSettled(
    [1, 2, 3, 4, 5].map(() => postJSON(url, body))
  );

  let rows = "";
  let ok = 0;
  results.forEach((r, i) => {
    if (r.status === "fulfilled") {
      ok++;
      const d = r.value.data;
      rows +=
        `<tr><td>#${i + 1}</td><td>${r.value.status}</td>` +
        `<td>${esc(d?.outcome ?? "-")}</td>` +
        `<td>${esc(d?.task_status ?? d?.detail ?? "-")}</td>` +
        `<td>success=${esc(d?.log?.success ?? "-")}</td></tr>`;
    } else {
      rows += `<tr><td>#${i + 1}</td><td colspan="4">网络错误：${esc(r.reason)}</td></tr>`;
    }
  });

  const detail = await fetch(`/api/tasks/${taskId}`).then((r) => r.json());
  const logCount = detail.logs.length;

  show(
    `<b>五次并发上报 step ${seq}（任务 #${taskId}）</b>` +
    `<table class="five"><thead><tr><th>请求</th><th>HTTP</th><th>outcome</th><th>task_status</th><th>日志</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    `<p>5 次请求中 <b>${ok}</b> 次成功返回；数据库执行日志行数 = <b>${logCount}</b>（应为 1）` +
    `；任务最终状态 = <b>${esc(detail.status)}</b></p>`
  );
  btn.disabled = false;
}
