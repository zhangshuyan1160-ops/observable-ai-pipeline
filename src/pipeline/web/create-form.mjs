import { CreateIntent, copyParameters } from "./create-intent.mjs";

const defaults = {
  title: "Orbit / 动画素材",
  asset: "orbit",
  frames: 16,
  seed: 42,
  model_version: "mock-v1",
  step_delay: 1.2,
  batch_delay: null,
  cache_enabled: true,
  fault_step: "stylize",
  fault_mode: "none",
  max_attempts: 3,
  attempt_timeout: 120,
  task_timeout: 3600,
  gpu_memory_mb: 4096,
};
const numeric = [
  "frames",
  "seed",
  "step_delay",
  "max_attempts",
  "attempt_timeout",
  "task_timeout",
  "gpu_memory_mb",
];

export function setupCreation({ api, toast, onCreated }) {
  const $ = (selector) => document.querySelector(selector);
  // sessionStorage scopes pending intent to this tab; it survives reload.
  // Access is deferred so a blocked store does not prevent read-only use.
  const intent = new CreateIntent({
    getItem: (key) => sessionStorage.getItem(key),
    setItem: (key, value) => sessionStorage.setItem(key, value),
    removeItem: (key) => sessionStorage.removeItem(key),
  });
  const form = $("#create-form");
  let draft = { ...defaults },
    sending = false;

  function fill(params) {
    draft = { ...defaults, ...structuredClone(params) };
    for (const [key, value] of Object.entries(draft)) {
      const control = form.elements.namedItem(key);
      if (!control) continue; // Unknown fields stay in the snapshot, never silently dropped.
      if (control.type === "checkbox") control.checked = value;
      else control.value = value ?? "";
    }
  }

  function read() {
    const params = { ...draft, ...Object.fromEntries(new FormData(form)) };
    for (const key of numeric) params[key] = Number(form.elements[key].value);
    params.cache_enabled = form.elements.cache_enabled.checked;
    const batch = form.elements.batch_delay.value;
    if (batch === "") delete params.batch_delay;
    else params.batch_delay = Number(batch);
    return params;
  }

  function error(message) {
    $("#create-error").textContent = message;
    $("#create-error").hidden = !message;
  }

  function sync() {
    try {
      const pending = intent.pending();
      $("#pending-create").hidden = !pending;
      $("#intent-status").hidden = !pending;
      $("#new-intent").hidden = !pending;
      $("#new-intent").disabled = sending;
      $("#create-fields").disabled = !!pending || sending;
      $("#submit-create").disabled = sending;
      $("#submit-create").textContent = sending
        ? "正在确认…"
        : pending
          ? "重试原请求并确认结果"
          : "创建并执行";
      if (pending)
        $("#intent-status").textContent =
          `提交结果待确认：${pending.params.title}。原参数已锁定，重试使用同一幂等键。开始时间 ${new Date(pending.createdAt).toLocaleString("zh-CN")}。关闭弹窗不会取消服务端任务。`;
      return pending;
    } catch (err) {
      error("无法保存或读取创建记录：" + err.message);
      $("#submit-create").disabled = true;
      return null;
    }
  }

  function open(params = defaults, context = "") {
    const pending = sync();
    fill(pending ? pending.params : params);
    $("#create-context").textContent = pending
      ? "先确认上次请求的结果。若确需另一项任务，可明确选择“另建任务”。"
      : context;
    error("");
    sync();
    $("#create-dialog").showModal();
  }

  $("#open-create").onclick = () => open();
  $("#resume-create").onclick = () => open();
  $("#close-create").onclick = $("#cancel-create").onclick = () =>
    $("#create-dialog").close();
  $("#new-intent").onclick = () => {
    if (
      sending ||
      !window.confirm(
        "原请求可能已创建任务。另建将使用新幂等键，可能产生第二个任务。确认开始另一项创建操作？",
      )
    )
      return;
    try {
      const previous = intent.pending();
      intent.abandon();
      fill(previous?.params ?? defaults);
      error("");
      $("#create-context").textContent =
        "新的创建操作：可修改参数后提交。原请求在服务端的任务不会被取消。";
      sync();
    } catch (err) {
      error(err.message);
    }
  };

  form.onsubmit = async (event) => {
    event.preventDefault();
    if (sending) return;
    error("");
    try {
      const params = intent.pending()?.params ?? read();
      intent.prepare(params); // Persist before disabling controls or starting network IO.
      sending = true;
      sync();
      const result = await intent.submit(params, (record) =>
        api("/api/tasks", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": record.key,
          },
          body: JSON.stringify(record.params),
        }),
      );
      $("#create-dialog").close();
      toast(
        result.reused_request
          ? "已确认原任务，没有重复创建"
          : "任务已创建，等待 Worker 领取",
      );
      await onCreated(result);
    } catch (err) {
      error(
        `${err.message}。未确认成功前保留原请求；可重试确认，或明确另建任务。422 表示参数被拒绝，409 表示幂等键冲突。`,
      );
    } finally {
      sending = false;
      sync();
    }
  };
  fill(defaults);
  sync();
  return {
    open,
    copy: (params, reuse = false) =>
      open(
        copyParameters(params, reuse),
        reuse
          ? "验证结果复用：保留素材、帧数、模型版本、随机种子及执行参数；仅开启缓存、关闭故障注入。缓存来源可能是任意历史成功任务。请检查后提交。"
          : "复制参数创建：完整保留原任务的缓存、故障配置、超时和显存需求。请检查后提交；这是一个新任务。",
      ),
  };
}
