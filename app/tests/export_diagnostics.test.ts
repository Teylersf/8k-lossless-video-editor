import { strict as assert } from "node:assert";
import { test } from "node:test";
import { copyDiagnostics, failureSummary } from "../src/export_diagnostics.ts";

test("export summary keeps the final useful cause while details remain unchanged", () => {
  const details =
    'Export failed:\nTraceback (most recent call last):\n  File "worker.py", line 7\nValueError: AV1 encoder is unavailable\nConversion failed!\n';
  assert.equal(failureSummary(details), "AV1 encoder is unavailable");
  assert.ok(details.endsWith("Conversion failed!\n"));
  assert.equal(
    failureSummary(""),
    "The renderer could not complete this export.",
  );
  assert.equal(failureSummary("One actionable line"), "One actionable line");
  assert.equal(failureSummary("x".repeat(500)).length, 240);
});

test("copy sends the complete multiline diagnostic, not the shortened summary", async () => {
  const details = "first line\n" + "x".repeat(6000) + "\nfinal cause";
  let received = "",
    fallback = false;
  assert.equal(
    await copyDiagnostics(
      details,
      async (text) => {
        received = text;
      },
      () => {
        fallback = true;
        return true;
      },
    ),
    true,
  );
  assert.equal(received, details);
  assert.equal(fallback, false);
});

test("Clipboard rejection falls back and reports actual fallback success", async () => {
  let received = "";
  assert.equal(
    await copyDiagnostics(
      "details\nend",
      async () => {
        throw new Error("denied");
      },
      (text) => {
        received = text;
        return true;
      },
    ),
    true,
  );
  assert.equal(received, "details\nend");
  assert.equal(await copyDiagnostics("details", undefined, () => false), false);
  assert.equal(
    await copyDiagnostics(
      "details",
      async () => {
        throw new Error("denied");
      },
      () => {
        throw new Error("unsupported");
      },
    ),
    false,
  );
});
