const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const date = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const statuses = {
  RUNNING: "执行中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  TIMED_OUT: "执行超时",
  INTERRUPTED: "进程中断",
  CANCELLED: "已取消",
};
const errors = {
  PERMANENT: "永久错误",
  TRANSIENT: "临时错误",
  ConnectionError: "连接错误（可重试）",
  WORKER_LOST: "Worker 失联",
  TIMEOUT: "执行超时",
  TASK_DEADLINE: "超过任务总体时限",
  USER_CANCELLED: "用户取消",
  CANCEL_WORKER_LOST: "取消期间失联，停止未确认",
};
const eventNames = {
  "task.created": "任务已创建",
  "task.succeeded": "任务已完成",
  "task.failed": "任务失败",
  "task.cancel_requested": "请求取消",
  "task.cancelled": "任务已取消",
  "task.archived": "任务已归档",
  "task.unarchived": "恢复到任务列表",
  "step.pending": "重新等待依赖",
  "step.ready": "依赖已就绪",
  "step.blocked": "等待上游修复",
  "step.cached": "复用结果",
  "step.manual_retry": "手动重试",
  "step.waiting_resource": "等待 GPU 槽",
  "step.unschedulable": "资源不足",
  "step.deadline_exceeded": "超过任务时限",
  "step.cancelled": "步骤已取消",
  "attempt.started": "开始执行",
  "attempt.succeeded": "执行完成",
  "attempt.failed": "本次失败",
  "attempt.interrupted": "进程中断",
  "attempt.timed_out": "执行超时",
  "attempt.cancelled": "执行已停止",
  "attempt.cancel_unconfirmed": "执行停止未确认",
  "attempt.stale_commit_rejected": "拒绝过期提交",
  "checkpoint.saved": "保存检查点",
  "checkpoint.invalidated": "检查点失效",
  "cache.invalidated": "缓存失效",
};

function attemptsTable(attempts) {
  if (!attempts.length)
    return '<p class="no-data">尚无计算尝试；缓存命中不创建虚假的 attempt。</p>';
  return /* HTML */ `<div class="table-scroll" data-key="attempts-table">
    <table>
      <thead>
        <tr>
          <th>轮次 / 尝试</th>
          <th>状态</th>
          <th>耗时</th>
          <th>恢复起点</th>
          <th>执行详情</th>
        </tr>
      </thead>
      <tbody>
        ${attempts
          .map(
            (a) =>
              /* HTML */ ` <tr data-key="attempt-${a.id}">
                <td>${a.retry_round + 1} / ${a.attempt_no}</td>
                <td>
                  <span class="badge ${esc(a.status)}"
                    >${esc(statuses[a.status] || a.status)}</span
                  >
                </td>
                <td>
                  ${Math.max(
                    0,
                    ((a.finished_at ? new Date(a.finished_at) : new Date()) -
                      new Date(a.started_at)) /
                      1000,
                  ).toFixed(1)}s
                </td>
                <td>${a.resume_from} 帧</td>
                <td class="attempt-detail">
                  <details data-key="attempt-detail-${a.id}">
                    <summary>${a.error ? "查看错误" : "查看 Worker"}</summary>
                    <dl>
                      <dt>Worker</dt>
                      <dd>${esc(a.worker_id)}</dd>
                      <dt>Attempt ID</dt>
                      <dd>${esc(a.id)}</dd>
                      <dt>开始 / 结束</dt>
                      <dd>${date(a.started_at)} / ${date(a.finished_at)}</dd>
                      ${a.error
                        ? /* HTML */ `<dt>错误分类</dt>
                            <dd>
                              ${esc(errors[a.error_type] || a.error_type)} ·
                              ${esc(a.error_type)}
                            </dd>
                            <dt>原因</dt>
                            <dd class="error-text">${esc(a.error)}</dd>`
                        : ""}
                    </dl>
                  </details>
                </td>
              </tr>`,
          )
          .join("")}
      </tbody>
    </table>
  </div>`;
}

function ancestors(task, step) {
  const result = new Set();
  const visit = (key) => {
    if (result.has(key)) return;
    result.add(key);
    task.steps.find((s) => s.key === key)?.depends_on.forEach(visit);
  };
  step.depends_on.forEach(visit);
  return task.steps.filter((s) => result.has(s.key));
}

export function stepDetails(task, step) {
  const latest = step.attempts.at(-1);
  const failure = [...step.attempts].reverse().find((a) => a.error);
  const dependencies = ancestors(task, step);
  const blockers = dependencies.filter((s) =>
    ["FAILED", "BLOCKED", "CANCELLED"].includes(s.status),
  );
  const resumed = step.attempts.length > 1 ? latest : null;
  // A finished ancestor retained its artifact and has no attempt after this recovery began.
  // Cached ancestors have no own attempt; avoid claiming a time relationship without evidence.
  const retained = resumed
    ? dependencies.filter(
        (s) =>
          s.status === "SUCCEEDED" &&
          s.attempts.length &&
          s.attempts.every(
            (a) =>
              a.finished_at &&
              new Date(a.finished_at) <= new Date(resumed.started_at),
          ),
      )
    : [];
  const waiting =
    step.status === "RETRY_WAIT"
      ? Math.max(0, Math.ceil((new Date(step.next_run_at) - Date.now()) / 1000))
      : 0;
  const lineage = step.cache_lineage;
  return /* HTML */ ` ${step.error
      ? /* HTML */ `<div class="error-box" data-key="step-error">
          <strong
            >${esc(
              errors[failure?.error_type] || failure?.error_type || "步骤错误",
            )}</strong
          ><br />${esc(step.error)}
        </div>`
      : ""}
    <dl class="diagnostics" data-key="diagnostics">
      <div>
        <dt>本轮尝试</dt>
        <dd>
          第 ${step.retry_round + 1} 轮 · ${step.attempts_in_round} /
          ${task.params.max_attempts}
          次${step.status === "FAILED" &&
          step.attempts_in_round >= task.params.max_attempts
            ? " · 本轮预算耗尽"
            : ""}
        </dd>
      </div>
      <div>
        <dt>处理进度</dt>
        <dd>
          ${Math.round(step.progress * 100)}%${step.checkpoint.frames
            ? " · " + step.checkpoint.frames.length + " 帧已保存"
            : ""}
        </dd>
      </div>
      <div>
        <dt>最近心跳</dt>
        <dd>${date(latest?.heartbeat_at)}</dd>
      </div>
      <div>
        <dt>执行资源</dt>
        <dd>${step.resource === "gpu" ? "GPU mock / 单槽" : "CPU Worker"}</dd>
      </div>
    </dl>
    ${step.status === "RETRY_WAIT"
      ? /* HTML */ `<p class="state-notice" data-key="waiting">
          自动重试安排于
          ${date(step.next_run_at)}，${waiting
            ? "约 " + waiting + " 秒后可领取"
            : "已到重试时间，等待 Worker 领取"}。时间到达不代表已经开始执行。
        </p>`
      : ""}
    ${blockers.length && step.status === "BLOCKED"
      ? /* HTML */ `<p class="state-notice" data-key="waiting">
          阻塞来源：${blockers
            .map((s) => esc(s.label) + "（" + esc(s.key) + "）")
            .join("、")}。恢复失败上游后，依赖重新检查。
        </p>`
      : ""}
    ${step.status === "PENDING"
      ? /* HTML */ `<p class="description">
          等待直接依赖：${step.depends_on
            .map((k) => esc(task.steps.find((s) => s.key === k)?.label || k))
            .join("、")}。
        </p>`
      : ""}
    ${resumed
      ? /* HTML */ `<p class="recovery-note" data-key="recovery">
          最近恢复尝试从 ${resumed.resume_from}
          帧继续${resumed.resume_from === 0
            ? "（整步重新执行）"
            : ""}。${retained.length
            ? "已完成上游未重新执行：" +
              retained.map((s) => esc(s.label)).join("、") +
              "。"
            : ""}全部
          ${step.attempts.length} 次尝试仍保留。
        </p>`
      : ""}
    ${lineage
      ? /* HTML */ `<details class="cache-detail" data-key="cache-${step.id}">
          <summary>结果复用来源与校验</summary>
          <dl>
            <dt>来源任务</dt>
            <dd>
              <button data-source-task="${esc(lineage.task_id)}">
                查看 ${esc(lineage.title)}</button
              ><code>${esc(lineage.task_id)}</code>
            </dd>
            <dt>来源步骤 / Attempt</dt>
            <dd>${esc(lineage.step_key)} / ${esc(step.artifact.attempt_id)}</dd>
            <dt>产物 / 大小</dt>
            <dd>${esc(step.artifact.id)} / ${step.artifact.size} bytes</dd>
            <dt>输入指纹</dt>
            <dd><code>${esc(step.fingerprint)}</code></dd>
            <dt>SHA-256</dt>
            <dd><code>${esc(step.artifact.sha256)}</code></dd>
            <dt>校验记录</dt>
            <dd>
              ${lineage.validated_at
                ? "命中时已通过文件大小与 SHA-256 校验：" +
                  date(lineage.validated_at)
                : "缺少命中事件，无法确认校验时间"}。这是历史记录；下载时重新校验。
            </dd>
          </dl>
        </details>`
      : ""}
    ${attemptsTable(step.attempts)}`;
}

export function eventDetails(events) {
  return events
    .slice(-30)
    .reverse()
    .map(
      (e) =>
        /* HTML */ `<div class="event" data-key="event-${e.task_id}-${e.seq}">
          <time
            >${new Date(e.created_at).toLocaleTimeString("zh-CN", {
              hour12: false,
            })}</time
          ><span class="event-name">${esc(eventNames[e.type] || e.type)}</span>
          <div class="event-meta">
            ${esc(e.step_key || "task")}${e.payload.completed_frames !==
            undefined
              ? " / 已保存 " + e.payload.completed_frames + " 帧"
              : ""}${e.payload.resume_from !== undefined
              ? " / 从 " + e.payload.resume_from + " 帧开始"
              : ""}${e.payload.error ? " / " + esc(e.payload.error) : ""}${e
              .payload.will_retry
              ? " / 将自动重试"
              : ""}
            <details data-key="event-detail-${e.task_id}-${e.seq}">
              <summary>原始事件 · #${e.seq}</summary>
              <code>${esc(e.type)}</code>
              <pre>${esc(JSON.stringify(e.payload, null, 2))}</pre>
            </details>
          </div>
        </div>`,
    )
    .join("");
}
