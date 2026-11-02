"use strict";
// Actual Electron GUI + isolated mock child lifecycle; no real GPU/media backend.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const electron = require("electron");

async function main() {
  const folder = fs.mkdtempSync(path.join(__dirname, "..", ".smoke-"));
  const environment = {
    ...process.env,
    EDITOR_DESKTOP_TEST: "1",
    EDITOR_DESKTOP_TEST_USER_DATA: folder,
  };
  delete environment.ELECTRON_RUN_AS_NODE;
  const log = fs.openSync(path.join(folder, "electron.log"), "a");
  const child = spawn(
    electron,
    [path.join(__dirname, ".."), "--desktop-smoke"],
    {
      cwd: path.join(__dirname, ".."),
      env: environment,
      windowsHide: true,
      stdio: ["ignore", log, log],
    },
  );
  fs.closeSync(log);
  const timer = setTimeout(() => child.kill(), 90000);
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", resolve);
    });
    assert.equal(code, 0, `Electron smoke failed. See ${folder}`);
    const receipt = JSON.parse(
      fs.readFileSync(path.join(folder, "logs", "desktop-smoke.json"), "utf8"),
    );
    assert.equal(receipt.version, require("../package.json").version);
    assert.equal(receipt.mock_backend, true);
    for (const key of [
      "owned_identity_verified",
      "backend_ready",
      "nodeUnavailable",
      "processUnavailable",
      "editorRendered",
      "context_isolation",
      "sandbox",
      "backend_stopped",
    ])
      assert.equal(receipt[key], true, key);
    assert.equal(receipt.node_integration, false);
    console.log(JSON.stringify({ status: "passed", receipt, folder }, null, 2));
  } finally {
    clearTimeout(timer);
    if (child.exitCode === null && child.signalCode === null) child.kill();
  }
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
