// A tab-scoped intent survives refresh. Persist before the first network write.
export const STORAGE_KEY = "frameflow-create-intent-v1";

export class CreateIntent {
  constructor(storage, newKey = () => crypto.randomUUID()) {
    this.storage = storage;
    this.newKey = newKey;
  }

  pending() {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const record = JSON.parse(raw);
    if (
      record.version !== 1 ||
      typeof record.key !== "string" ||
      !record.params ||
      typeof record.params !== "object"
    ) {
      throw new Error(
        "创建记录无法读取；请保留当前页面记录，核对任务列表后再另建任务。",
      );
    }
    return record;
  }

  prepare(params) {
    const existing = this.pending();
    if (existing) return existing; // Ignore edited parameters: retry the original snapshot.
    const record = {
      version: 1,
      key: this.newKey(),
      params: structuredClone(params),
      createdAt: new Date().toISOString(),
    };
    this.storage.setItem(STORAGE_KEY, JSON.stringify(record)); // Failure must prevent the POST.
    return record;
  }

  acknowledge(key) {
    if (this.pending()?.key === key) this.storage.removeItem(STORAGE_KEY);
  }

  abandon() {
    // Only called after an explicit user choice; this never cancels a server task.
    this.storage.removeItem(STORAGE_KEY);
  }

  async submit(params, post) {
    const record = this.prepare(params);
    const result = await post(record);
    if (typeof result?.task_id !== "string" || !result.task_id)
      throw new Error("响应缺少任务 ID，提交结果待确认。");
    this.acknowledge(record.key);
    return result;
  }
}

export function copyParameters(params, reuse = false) {
  const copy = structuredClone(params);
  if (reuse) {
    copy.cache_enabled = true;
    copy.fault_mode = "none";
  }
  return copy;
}
