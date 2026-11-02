import { strict as assert } from "node:assert";
import { test } from "node:test";
import { finiteSeekTarget } from "../src/media_seek.ts";

test("Source-to-Program remount waits for metadata, then retries the requested seek", () => {
  assert.equal(finiteSeekTarget(3, NaN, 0, 0), null);
  assert.equal(finiteSeekTarget(3, 4.01, 0, 0), null);
  assert.equal(finiteSeekTarget(3, 4.01, 1, 0), 3);
  assert.equal(finiteSeekTarget(3, 4.01, 4, 3), null);
});

test("NaN, infinite and negative durations never produce a DOM seek value", () => {
  for (const duration of [NaN, Infinity, -Infinity, -1]) {
    assert.equal(finiteSeekTarget(3, duration, 4, 0), null);
  }
  for (const requested of [NaN, Infinity, -Infinity]) {
    assert.equal(finiteSeekTarget(requested, 4, 4, 0), null);
  }
  for (const state of [NaN, Infinity, -1, 0]) {
    assert.equal(finiteSeekTarget(3, 4, state, 0), null);
  }
});

test("A nonfinite current position can be repaired with a finite bounded target", () => {
  for (const current of [NaN, Infinity, -Infinity]) {
    assert.equal(finiteSeekTarget(3, 4, 1, current), 3);
    assert.equal(finiteSeekTarget(-3, 4, 1, current), 0);
    assert.equal(finiteSeekTarget(9, 4, 1, current), 3.999);
  }
});

test("Zero/short files and out-of-bounds playheads remain finite", () => {
  assert.equal(finiteSeekTarget(4, 0, 1, NaN), 0);
  assert.equal(finiteSeekTarget(4, 0.0005, 1, NaN), 0);
  assert.equal(finiteSeekTarget(-2, 4, 4, 2), 0);
  assert.equal(finiteSeekTarget(4, 4.01, 4, 0), 4);
  assert.equal(finiteSeekTarget(4.02, 4.01, 4, 0), 4.0089999999999995);
});

test("Already synchronized playback avoids redundant seek events", () => {
  assert.equal(finiteSeekTarget(3, 4, 4, 3.02), null);
  assert.equal(finiteSeekTarget(3, 4, 4, 3.03), 3);
});
