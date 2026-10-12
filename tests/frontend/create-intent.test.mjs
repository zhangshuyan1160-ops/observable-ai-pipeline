import test from "node:test";
import assert from "node:assert/strict";
import {
  CreateIntent,
  copyParameters,
} from "../../src/pipeline/web/create-intent.mjs";

function storage() {
  const map = new Map();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  };
}

test("lost reply and reload retry the immutable snapshot with the original key", async () => {
  const store = storage();
  const first = new CreateIntent(store, () => "stable-key");
  const params = { title: "quality / loss", seed: 7, cache_enabled: false };
  await assert.rejects(
    first.submit(params, async (record) => {
      assert.equal(record.key, "stable-key");
      throw new TypeError("Network response lost");
    }),
  );
  params.seed = 99;
  const reloaded = new CreateIntent(store, () => "must-not-be-used");
  const result = await reloaded.submit(params, async (record) => {
    assert.equal(record.key, "stable-key");
    assert.equal(record.params.seed, 7);
    return { task_id: "original-task", reused_request: true };
  });
  assert.equal(result.task_id, "original-task");
  assert.equal(reloaded.pending(), null);
  assert.equal(reloaded.prepare(params).key, "must-not-be-used");
});

test("storage failure prevents POST and malformed success retains intent", async () => {
  let called = false;
  const blocked = new CreateIntent(
    {
      getItem: () => null,
      setItem: () => {
        throw Error("quota");
      },
    },
    () => "key",
  );
  await assert.rejects(
    blocked.submit({}, async () => {
      called = true;
    }),
  );
  assert.equal(called, false);
  const intent = new CreateIntent(storage(), () => "key");
  await assert.rejects(
    intent.submit({ title: "x" }, async () => ({})),
    /任务 ID/,
  );
  assert.equal(intent.pending().key, "key");
});

test("explicit new operation changes key; unrelated acknowledgement cannot discard it", () => {
  let counter = 0;
  const intent = new CreateIntent(storage(), () => String(++counter));
  const first = intent.prepare({ seed: 1 });
  intent.abandon();
  const second = intent.prepare({ seed: 2 });
  intent.acknowledge(first.key);
  assert.equal(intent.pending().key, second.key);
  assert.notEqual(first.key, second.key);
});

test("copy preserves all parameters; reuse changes only its two advertised fields", () => {
  const original = {
    title: "failure",
    seed: 53,
    cache_enabled: false,
    fault_mode: "exhaust_once",
    fault_step: "compose",
    max_attempts: 5,
    step_delay: 0.01,
    batch_delay: 1.25,
    attempt_timeout: 42,
    task_timeout: 100,
    gpu_memory_mb: 7000,
    future_field: { a: 1 },
  };
  const copy = copyParameters(original);
  assert.deepEqual(copy, original);
  copy.future_field.a = 2;
  assert.equal(original.future_field.a, 1);
  assert.deepEqual(copyParameters(original, true), {
    ...original,
    cache_enabled: true,
    fault_mode: "none",
  });
});
