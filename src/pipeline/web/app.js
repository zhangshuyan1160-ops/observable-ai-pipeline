import { setupCreation } from "./create-form.mjs";
import { renderHTML } from "./dom.mjs";
import { stepDetails, eventDetails } from "./details.mjs";
import { setupPipelineEdges } from "./pipeline-edges.mjs";
const $ = (selector) => document.querySelector(selector);
const syncPipelineEdges = setupPipelineEdges($("#task-content"));
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const names = {
  QUEUED: "已入队",
  PENDING: "等待依赖",
  READY: "就绪",
  RUNNING: "执行中",
  WAITING_RESOURCE: "等待 GPU",
  RETRY_WAIT: "等待重试",
  SUCCEEDED: "已完成",
  CACHED: "已复用",
  FAILED: "失败",
  BLOCKED: "上游阻塞",
  INTERRUPTED: "进程中断",
  TIMED_OUT: "执行超时",
  CANCELLING: "取消中",
  CANCELLED: "已取消",
};
let selected = localStorage.getItem("frameflow-task"),
  selectedStep = "stylize",
  currentTask = null,
  items = [],
  busy = false,
  lastEvents = [],
  lastResources = null;
let pageCount = 1,
  nextCursor = null,
  filterVersion = 0,
  refreshQueued = false,
  cancelTarget = null;
const terminal = (status) =>
  ["SUCCEEDED", "FAILED", "CANCELLED"].includes(status);
const dateTime = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const badge = (s) =>
  /* HTML */ `<span class="badge ${escapeHTML(s)}"
    >${escapeHTML(names[s] || s)}</span
  >`;
const seconds = (value) => Number(value || 0).toFixed(1) + "s";
function toast(message) {
  $("#toast").textContent = message;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => ($("#toast").textContent = ""), 5000);
}
async function api(path, options = {}) {
  const r = await fetch(path, {
    signal: AbortSignal.timeout(10000),
    ...options,
  });
  if (!r.ok) {
    let b;
    try {
      b = await r.json();
    } catch {
      b = { detail: r.statusText };
    }
    const error = Error(
      typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail),
    );
    error.status = r.status;
    throw error;
  }
  return r.json();
}

const creation = setupCreation({
  api,
  toast,
  onCreated: async (created) => {
    selectTask(created.task_id);
    await refresh(true);
  },
});
$("#interrupt-help").onclick = () => $("#help-dialog").showModal();
$("#close-help").onclick = () => $("#help-dialog").close();
$("#interrupt-preset").onclick = () => {
  $("#help-dialog").close();
  creation.open(
    {
      title: "进程中断恢复 / 演示",
      frames: 32,
      batch_delay: 4,
      cache_enabled: false,
    },
    "等待至少一个检查点后，再执行中断命令。",
  );
};
$("#copy-interrupt").onclick = async () => {
  try {
    await navigator.clipboard.writeText(
      ".venv/bin/python scripts/dev.py interrupt gpu",
    );
    toast("中断命令已复制，请在项目目录执行");
  } catch {
    toast("无法写入剪贴板，请选中上方命令手动复制");
  }
};
$("#retry-demo").onclick = () =>
  creation.open(
    {
      title: "自动重试 / 演示",
      fault_mode: "transient_once",
      cache_enabled: false,
    },
    "首次临时失败后自动重试；已完成上游保留。",
  );
$("#reuse-demo").onclick = () => {
  if (currentTask) creation.copy(currentTask.params, true);
  else
    creation.open(
      { title: "结果复用 / 原始任务" },
      "先完成一次允许缓存的任务，再点击“验证结果复用”。",
    );
};
function selectTask(id) {
  selected = id;
  currentTask = null;
  lastEvents = [];
  localStorage.setItem("frameflow-task", id);
}
$("#task-list").onclick = (e) => {
  const button = e.target.closest("[data-task]");
  if (button) {
    selectTask(button.dataset.task);
    refresh(true);
  }
};
$("#task-content").onclick = async (e) => {
  const cancel = e.target.closest("[data-cancel]");
  if (cancel) {
    cancelTarget = cancel.dataset.cancel;
    $("#cancel-description").textContent =
      currentTask.params.title + " · " + cancelTarget;
    $("#cancel-dialog").showModal();
    return;
  }
  const archive = e.target.closest("[data-archive]");
  if (archive) {
    archive.disabled = true;
    archive.dataset.busy = "true";
    try {
      const archived = archive.dataset.archived !== "true";
      await api(
        "/api/tasks/" +
          archive.dataset.archive +
          "/archive?archived=" +
          archived,
        { method: "POST" },
      );
      toast(archived ? "已归档，历史和文件仍保留" : "已恢复到任务列表");
      await refresh(true);
    } catch (error) {
      toast(error.message);
    } finally {
      archive.disabled = false;
      delete archive.dataset.busy;
    }
    return;
  }
  const node = e.target.closest("[data-step]");
  if (node) {
    selectedStep = node.dataset.step;
    renderTask(currentTask);
    renderEvents(lastEvents);
    if (lastResources) renderResources(lastResources);
    return;
  }
  const retry = e.target.closest("[data-retry]");
  if (retry) {
    retry.disabled = true;
    retry.dataset.busy = "true";
    try {
      await api("/api/steps/" + retry.dataset.retry + "/retry", {
        method: "POST",
      });
      toast("已安排新一轮尝试，历史记录保留");
      currentTask = null;
      await refresh(true);
    } catch (error) {
      toast(error.message);
    } finally {
      retry.disabled = false;
      delete retry.dataset.busy;
    }
  }
  const clone = e.target.closest("[data-copy]");
  if (clone && currentTask)
    creation.copy(currentTask.params, clone.dataset.copy === "reuse");
  const source = e.target.closest("[data-source-task]");
  if (source) {
    selectTask(source.dataset.sourceTask);
    await refresh(true);
  }
  const start = e.target.closest("[data-create]");
  if (start) creation.open();
};
function renderList() {
  $("#task-count").textContent = items.length;
  renderHTML(
    $("#task-list"),
    items
      .map(
        (t) =>
          /* HTML */ `<button
            class="task-item ${t.id === selected ? "selected" : ""}"
            data-key="task-${escapeHTML(t.id)}"
            data-task="${escapeHTML(t.id)}"
            aria-current="${t.id === selected}"
          >
            <strong>${t.archived_at ? "▣ " : ""}${escapeHTML(t.title)}</strong
            ><small
              ><span>${escapeHTML(names[t.status])}</span
              ><span>${dateTime(t.created_at)}</span></small
            >
          </button>`,
      )
      .join("") ||
      '<div class="no-data">没有符合条件的任务<br>可调整搜索或筛选条件</div>',
  );
}
const parameterNames = {
  title: "任务名称",
  asset: "演示素材",
  frames: "总帧数",
  seed: "随机种子",
  model_version: "模型版本",
  step_delay: "步骤模拟耗时（秒）",
  batch_delay: "每批模拟耗时（秒）",
  cache_enabled: "允许结果复用",
  fault_step: "故障步骤",
  fault_mode: "故障模式",
  max_attempts: "每轮最多尝试",
  attempt_timeout: "单次执行时限（秒）",
  task_timeout: "任务总体时限（秒）",
  gpu_memory_mb: "模拟显存需求（MiB）",
};
function renderTask(t) {
  if (!t) {
    renderHTML(
      $("#task-content"),
      '<section class="panel empty-state"><div class="empty-icon">▧</div><h1>让任务开始运行</h1><p class="description">从一个动画素材开始，观察六个处理步骤。你可以注入失败、终止 Worker，再从保存的进度继续。</p><button class="primary" data-create="true">创建第一个任务</button></section>',
    );
    syncPipelineEdges();
    return;
  }
  const selectedNode =
    t.steps.find((s) => s.key === selectedStep) || t.steps[0];
  renderHTML(
    $("#task-content"),
    [
      taskHeader(t),
      taskOverview(t),
      pipelineView(t, selectedNode),
      detailView(t, selectedNode),
      resultView(t),
      timelineView(),
    ].join(""),
  );
  syncPipelineEdges();
}

function taskHeader(t) {
  return /* HTML */ `<section class="task-toolbar" data-key="toolbar-${t.id}">
      <div>
        <strong>${escapeHTML(t.params.title)}</strong
        ><small
          >${escapeHTML(t.id)} ·
          ${dateTime(t.created_at)}${t.archived_at ? " · 已归档" : ""}</small
        >
      </div>
      <div class="actions">
        ${!terminal(t.status)
          ? /* HTML */ `<button
              data-cancel="${t.id}"
              class="danger"
              ${t.status === "CANCELLING" ? "disabled" : ""}
            >
              ${t.status === "CANCELLING" ? "正在取消…" : "取消任务"}
            </button>`
          : /* HTML */ `<button
              data-archive="${t.id}"
              data-archived="${!!t.archived_at}"
            >
              ${t.archived_at ? "恢复到列表" : "归档任务"}
            </button>`}<button data-copy="exact">复制参数创建</button
        ><button data-copy="reuse">验证结果复用</button>
      </div>
    </section>
    ${t.status === "CANCELLING"
      ? '<div class="state-notice">取消请求已保存，正在等待运行步骤停止。停止确认前保留运行资源。</div>'
      : ""}
    ${t.cancel_unconfirmed
      ? '<div class="error-box">已停止调度并拒绝后续结果提交。取消期间 Worker 失联，无法确认原计算是否已经停止；本项目只释放模拟 GPU 槽。</div>'
      : ""}
    <details class="parameter-panel" data-key="params-${t.id}">
      <summary>任务参数与执行时限</summary>
      <dl>
        ${Object.entries(t.params)
          .map(
            ([key, value]) =>
              /* HTML */ `<div>
                <dt>${escapeHTML(parameterNames[key] || key)}</dt>
                <dd>
                  ${escapeHTML(
                    value === null
                      ? "沿用步骤耗时"
                      : value === true
                        ? "是"
                        : value === false
                          ? "否"
                          : value,
                  )}
                </dd>
              </div>`,
          )
          .join("")}
        <div>
          <dt>总体截止时间</dt>
          <dd>${dateTime(t.deadline)}</dd>
        </div>
        ${t.cancel_requested_at
          ? /* HTML */ `<div>
              <dt>取消请求时间</dt>
              <dd>${dateTime(t.cancel_requested_at)}</dd>
            </div>`
          : ""}${t.cancelled_at
          ? /* HTML */ `<div>
              <dt>取消结束时间</dt>
              <dd>${dateTime(t.cancelled_at)}</dd>
            </div>`
          : ""}
      </dl>
    </details>`;
}

function taskOverview(t) {
  const done = t.steps.filter((s) =>
      ["SUCCEEDED", "CACHED"].includes(s.status),
    ).length,
    cached = t.steps.filter((s) => s.status === "CACHED").length,
    attempts = t.steps.flatMap((s) => s.attempts),
    failed = attempts.filter((a) =>
      ["FAILED", "INTERRUPTED", "TIMED_OUT"].includes(a.status),
    ).length;
  return /* HTML */ `<section
    data-key="overview"
    class="overview"
    aria-label="任务概览"
  >
    <div class="metric">
      <label>当前任务状态</label>${badge(t.status)}<small
        >${escapeHTML(t.params.title)}</small
      >
    </div>
    <div class="metric">
      <label>完成步骤</label
      ><strong
        >${done}<span style="color:#a6b1c0;font-size:16px"> / 6</span></strong
      ><small>批次与步骤综合进度 ${Math.round(t.progress * 100)}%</small>
    </div>
    <div class="metric">
      <label>结果复用</label
      ><strong
        >${cached}<span style="font-size:12px;font-weight:400;margin-left:6px"
          >个步骤</span
        ></strong
      ><small>输入与版本一致时命中</small>
    </div>
    <div class="metric">
      <label>执行尝试</label
      ><strong
        >${attempts.length}<span
          style="font-size:12px;font-weight:400;margin-left:6px"
          >次</span
        ></strong
      ><small>${failed} 次失败 / 中断 / 超时</small>
    </div>
  </section>`;
}

function pipelineView(t, selectedNode) {
  return /* HTML */ `<section data-key="pipeline" class="panel">
    <div class="panel-header">
      <h2>
        处理管线
        <span
          style="font-size:10px;color:#8d9bb0;font-weight:400;margin-left:9px"
          >animation-v1</span
        >
      </h2>
      <small>点击步骤，查看执行明细</small>
    </div>
    <p class="scroll-hint">窄屏可左右滚动查看完整管线</p>
    <div class="pipeline-scroll" tabindex="0" aria-label="管线，可左右滚动">
      <div class="pipeline">
        <svg class="edges" data-live-island aria-hidden="true">
          <defs>
            <marker
              id="pipeline-arrow"
              markerWidth="6"
              markerHeight="6"
              markerUnits="userSpaceOnUse"
              refX="6"
              refY="3"
              orient="auto"
            >
              <path d="M0,0 L6,3 L0,6 Z" class="edge-arrow" />
            </marker>
          </defs>
          <g class="edge-routes"></g></svg
        >${t.steps
          .map(
            (s) =>
              /* HTML */ `<button
                class="node node-${s.key} ${s.key === selectedNode.key
                  ? "selected"
                  : ""}"
                data-key="step-${s.key}"
                data-step="${s.key}"
                aria-pressed="${s.key === selectedNode.key}"
              >
                <strong
                  >${escapeHTML(s.label)}<em
                    >${s.resource === "gpu" ? "GPU MOCK" : "CPU"}</em
                  ></strong
                >${badge(s.status)}<span class="progress-track"
                  ><i style="width:${s.progress * 100}%"></i
                ></span>
              </button>`,
          )
          .join("")}
      </div>
    </div>
    <div class="pipeline-footer">
      <span class="legend"
        ><span><i></i>执行中</span
        ><span><i class="success"></i>完成 / 复用</span
        ><span><i class="waiting"></i>重试 / 等待</span></span
      ><span>持久化状态 · 依赖就绪后领取</span>
    </div>
  </section>`;
}

function detailView(t, selectedNode) {
  return /* HTML */ `<div data-key="details-grid" class="lower-grid">
    <section class="panel">
      <div class="panel-header">
        <h2>${escapeHTML(selectedNode.label)} · 执行明细</h2>
        ${badge(selectedNode.status)}
      </div>
      <div class="step-body" data-key="detail-${selectedNode.id}">
        ${stepDetails(t, selectedNode)}
        <div class="detail-footer">
          ${selectedNode.status === "FAILED" &&
          !t.archived_at &&
          !["CANCELLING", "CANCELLED"].includes(t.status)
            ? /* HTML */ `<button data-retry="${selectedNode.id}">
                重试这个步骤
              </button>`
            : ""}${selectedNode.artifact
            ? /* HTML */ `<a
                href="/api/artifacts/${selectedNode.artifact.id}?download=true"
                >下载步骤产物</a
              >`
            : ""}<span class="details-id"
            >${escapeHTML(selectedNode.id.slice(0, 12))}</span
          >
        </div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-header">
        <h2>计算资源</h2>
        <span class="mock-tag">模拟 GPU · 非真实遥测</span>
      </div>
      <div class="resource-body" id="resource-body" data-live-island></div>
    </section>
  </div>`;
}

function resultView(t) {
  const gif = t.steps.find((s) => s.key === "compose")?.artifact;
  const bundle = t.steps.find((s) => s.key === "package")?.artifact;
  return `${
    gif
      ? /* HTML */ `<section
          data-key="result"
          class="panel"
          style="margin-top:20px;padding:0 21px 18px"
        >
          <div class="preview">
            <img src="/api/artifacts/${gif.id}" alt="已生成的演示动画" />
            <div>
              <h3 style="margin-bottom:6px">动画产物已就绪</h3>
              <p>包含真实 GIF、缩略图与摘要清单，可下载核对。</p>
              ${bundle
                ? /* HTML */ `<a
                      href="/api/artifacts/${bundle.id}?download=true"
                      >下载交付包</a
                    >　 `
                : ""}<button data-copy="reuse">验证结果复用</button>
            </div>
          </div>
        </section>`
      : ""
  }`;
}

function timelineView() {
  return /* HTML */ `<section data-key="events" class="panel timeline-panel">
    <div class="panel-header">
      <h2>任务事件</h2>
      <small>事务内记录 · 最近 30 条</small>
    </div>
    <div class="timeline" id="timeline" data-live-island></div>
  </section>`;
}

function renderResources(r) {
  const el = $("#resource-body");
  if (!el) return;
  const slot = r.slots[0],
    busy = !!slot?.attempt_id;
  const online = r.workers.filter((w) => w.online);
  renderHTML(
    el,
    /* HTML */ `<div>
        <div class="resource-title">
          <strong>gpu-mock-0</strong
          ><span class="badge ${busy ? "RUNNING" : "CACHED"}"
            >${busy ? "已占用" : "空闲"}</span
          >
        </div>
        <div class="resource-bar"><i style="width:${busy ? 100 : 0}%"></i></div>
        <div class="resource-copy">
          <span>${busy ? "1" : "0"} / 1 执行槽</span
          ><span>容量 ${(slot?.capacity_mb || 8192) / 1024} GiB（模拟）</span>
        </div>
        <p class="description" style="margin-top:12px">
          ${r.waiting.length
            ? "排队 " +
              r.waiting.length +
              " 个任务，最长等待 " +
              seconds(Math.max(...r.waiting.map((w) => w.waiting_seconds)))
            : "没有任务在等待 GPU 资源"}
        </p>
      </div>
      <div class="workers">
        <h3>
          在线 Worker
          <span style="color:#8393a8;font-weight:400">${online.length}</span>
        </h3>
        ${r.workers
          .slice(0, 4)
          .map(
            (w) =>
              /* HTML */ `<div class="worker">
                <code>${escapeHTML(w.id)}</code
                ><span
                  ><i class="dot ${w.online ? "" : "offline"}"></i> ${w.online
                    ? "在线"
                    : "心跳过期"}</span
                >
              </div>`,
          )
          .join("") ||
        '<div class="description">尚无 Worker，请启动本地服务</div>'}
      </div>`,
  );
}
function renderEvents(events) {
  const el = $("#timeline");
  if (el) renderHTML(el, eventDetails(events));
}

$("#close-cancel").onclick = $("#keep-task").onclick = () =>
  $("#cancel-dialog").close();
$("#confirm-cancel").onclick = async () => {
  const button = $("#confirm-cancel");
  button.disabled = true;
  try {
    const result = await api("/api/tasks/" + cancelTarget + "/cancel", {
      method: "POST",
    });
    $("#cancel-dialog").close();
    toast(
      result.status === "CANCELLED" ? "任务已取消" : "已请求取消，等待执行停止",
    );
    await refresh(true);
  } catch (e) {
    toast(e.message);
  } finally {
    button.disabled = false;
  }
};
let searchTimer;
function resetFilters() {
  pageCount = 1;
  filterVersion++;
  refresh(true);
}
$("#history-filters").onsubmit = (e) => {
  e.preventDefault();
  clearTimeout(searchTimer);
  resetFilters();
};
$("#task-search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(resetFilters, 250);
};
$("#status-filter").onchange = $("#archive-filter").onchange = resetFilters;
$("#load-more").onclick = () => {
  if (nextCursor) {
    pageCount++;
    refresh(true);
  }
};

async function loadList() {
  const params = new URLSearchParams({
    limit: "30",
    q: $("#task-search").value,
    view: $("#archive-filter").value,
  });
  if ($("#status-filter").value)
    params.set("status", $("#status-filter").value);
  let rows = [],
    cursor = null;
  for (let page = 0; page < pageCount; page++) {
    if (cursor) params.set("before", cursor);
    const result = await api("/api/tasks?" + params);
    rows.push(...result.items);
    cursor = result.next_cursor;
    if (!cursor) break;
  }
  return {
    items: [...new Map(rows.map((t) => [t.id, t])).values()],
    next_cursor: cursor,
  };
}
async function refresh(force = false) {
  if (busy) {
    if (force) refreshQueued = true;
    return;
  }
  busy = true;
  const version = filterVersion;
  try {
    const [list, resources] = await Promise.all([
      loadList(),
      api("/api/resources"),
    ]);
    if (version !== filterVersion) {
      refreshQueued = true;
      return;
    }
    items = list.items;
    nextCursor = list.next_cursor;
    lastResources = resources;
    if (!selected && items.length) selectTask(items[0].id);
    renderList();
    $("#load-more").hidden = !nextCursor;
    if (selected) {
      const requested = selected;
      const listed = items.find((t) => t.id === requested);
      if (
        force ||
        !currentTask ||
        !terminal(currentTask.status) ||
        !listed ||
        listed.updated_at !== currentTask.updated_at
      ) {
        let task;
        try {
          task = await api("/api/tasks/" + requested);
        } catch (e) {
          if (e.status === 404) {
            if (selected === requested) {
              selected = null;
              currentTask = null;
              localStorage.removeItem("frameflow-task");
              refreshQueued = true;
            }
            return;
          }
          throw e;
        }
        let after = lastEvents.at(-1)?.seq || 0,
          batch;
        const newEvents = [];
        do {
          batch = await api(
            "/api/tasks/" + requested + "/events?limit=500&after_seq=" + after,
          );
          newEvents.push(...batch.items);
          after = batch.next_seq;
        } while (batch.items.length === 500);
        if (selected !== requested) {
          refreshQueued = true;
          return;
        }
        currentTask = task;
        lastEvents = [...lastEvents, ...newEvents].slice(-500);
        renderTask(task);
        renderEvents(lastEvents);
      }
      renderResources(resources);
    } else {
      renderTask(null);
    }
    $("#connection").innerHTML = '<i class="dot"></i>本地连接正常';
  } catch (e) {
    $("#connection").innerHTML =
      '<i class="dot offline"></i>连接中断，正在重连';
    if (force) toast(e.message);
  } finally {
    busy = false;
    if (refreshQueued) {
      refreshQueued = false;
      queueMicrotask(() => refresh(true));
    }
  }
}
renderTask(null);
refresh(true);
setInterval(() => refresh(), 1200);
