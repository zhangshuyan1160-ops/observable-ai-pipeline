// Called by pytest against its own PostgreSQL/API. Discard an accepted HTTP reply,
// reconstruct the browser-intent controller (reload), then retry changed UI data.
import assert from "node:assert/strict";
import { CreateIntent } from "../../src/pipeline/web/create-intent.mjs";
const base = process.argv[2];
const map = new Map();
const storage = {
  getItem: (k) => map.get(k) ?? null,
  setItem: (k, v) => map.set(k, v),
  removeItem: (k) => map.delete(k),
};
const params = {
  title: "quality / dropped response",
  frames: 7,
  seed: 17,
  cache_enabled: false,
  batch_delay: 0.1,
};
const post = async (record) => {
  const response = await fetch(base + "/api/tasks", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": record.key,
    },
    body: JSON.stringify(record.params),
  });
  assert.equal(response.status, 202);
  return response.json();
};
let original;
const first = new CreateIntent(storage, () => "frontend-lost-response");
await assert.rejects(
  first.submit(params, async (record) => {
    original = await post(record);
    throw new TypeError("Drop reply after server commit");
  }),
);
const saved = JSON.stringify(first.pending());
const reload = new CreateIntent(storage, () => "unexpected-new-key");
const result = await reload.submit({ ...params, seed: 999 }, post);
assert.equal(result.task_id, original.task_id);
assert.equal(result.reused_request, true);
assert.equal(reload.pending(), null);
const conflict = await fetch(base + "/api/tasks", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Idempotency-Key": "frontend-lost-response",
  },
  body: JSON.stringify({ ...params, seed: 999 }),
});
assert.equal(conflict.status, 409);
console.log(
  JSON.stringify({
    task_id: result.task_id,
    reused_request: result.reused_request,
    snapshot: JSON.parse(saved),
    conflict_status: conflict.status,
  }),
);
