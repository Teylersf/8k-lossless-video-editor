"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { test } = require("node:test");
const { BackendSession, requestJSON, freePort } = require("../backend.cjs");
const {
  APP_IDENTITY,
  localOrigin,
  isOwnedURL,
  isSafeExternal,
  isOwnedDownload,
  assertOwnedStatus,
  canGrantPermission,
} = require("../security.cjs");

function fixture(t, extra = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "8k-desktop-test-"));
  const session = new BackendSession({
    executable: process.execPath,
    args: [path.join(__dirname, "mock-backend.cjs")],
    dataDir: path.join(directory, "data"),
    logDir: path.join(directory, "logs"),
    startupTimeoutMs: 3000,
    pollIntervalMs: 25,
    shutdownGraceMs: 100,
    ...extra,
  });
  t.after(async () => {
    await session.stop();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return session;
}

test("only exact owned loopback origin is a local editor page", () => {
  const origin = localOrigin(12345);
  assert.equal(isOwnedURL(origin + "/api/jobs", origin), true);
  for (const value of [
    "http://127.0.0.1:8770",
    "http://127.0.0.1:8766",
    "http://localhost:12345",
    "http://127.0.0.1.evil:12345",
    "https://127.0.0.1:12345",
    "file:///tmp/x",
    "http://user@127.0.0.1:12345",
  ])
    assert.equal(isOwnedURL(value, origin), false, value);
  for (const port of [0, -1, 65536, 1.5, NaN, "8770"])
    assert.throws(() => localOrigin(port));
});

test("external links are HTTPS-only, without credentials, local hosts or command schemes", () => {
  assert.equal(
    isSafeExternal("https://github.com/Teylersf/8KLosslessVideoEditor"),
    true,
  );
  for (const value of [
    "file:///C:/x.exe",
    "javascript:alert(1)",
    "shell:app",
    "https://user:password@example.com",
    "https://localhost/x",
    "https://127.0.0.1",
    "https://192.168.1.1",
    "https://[::1]",
    "https://example.local",
    "https://example.com:8443",
    "https://example.com/\nfile.exe",
  ])
    assert.equal(isSafeExternal(value), false, value);
});

test("downloads require owned initiator and owned verified-job/blob URL", () => {
  const origin = localOrigin(12345),
    job = origin + "/api/jobs/" + "a".repeat(32) + "/download";
  assert.equal(isOwnedDownload(job, origin, origin), true);
  assert.equal(isOwnedDownload(`blob:${origin}/abc`, origin, origin), true);
  for (const value of [
    job + "?url=https://evil.com",
    origin + "/api/media/a/proxy",
    "https://example.com/file",
    "blob:http://127.0.0.1:8766/x",
  ])
    assert.equal(isOwnedDownload(value, origin, origin), false);
  assert.equal(isOwnedDownload(job, origin, "https://example.com"), false);
  assert.equal(isOwnedDownload(job, origin, ""), false);
});

test("identity and ownership are both mandatory", () => {
  assertOwnedStatus(
    { app: APP_IDENTITY, desktop_instance_token: "private" },
    "private",
  );
  for (const status of [
    { app: APP_IDENTITY },
    { app: "Lens Studio", desktop_instance_token: "private" },
    { app: APP_IDENTITY, desktop_instance_token: "other" },
    null,
  ])
    assert.throws(() => assertOwnedStatus(status, "private"));
});

test("only owned-page fullscreen is permitted; camera/microphone and other origins are denied", () => {
  const origin = localOrigin(12345);
  assert.equal(canGrantPermission("fullscreen", origin, origin), true);
  for (const permission of [
    "media",
    "geolocation",
    "notifications",
    "clipboard-read",
    "display-capture",
  ])
    assert.equal(canGrantPermission(permission, origin, origin), false);
  assert.equal(
    canGrantPermission("fullscreen", "https://example.com", origin),
    false,
  );
  assert.equal(
    canGrantPermission("fullscreen", "http://127.0.0.1:8766", origin),
    false,
  );
});

test("real spawned mock child receives private port/data paths and authenticated shutdown", async (t) => {
  const session = fixture(t);
  const status = await session.start();
  assert.equal(status.app, APP_IDENTITY);
  assert.equal(session.verified, true);
  assert.ok(session.alive());
  assert.notEqual(session.port, 8766);
  await assert.rejects(
    requestJSON(session.origin, "/api/shutdown", { method: "POST" }),
    /403/,
  );
  assert.ok(session.alive());
  await session.stop();
  assert.equal(session.alive(), false);
  await session.stop();
});

test("stop before or during port reservation prevents startup without creating an owned child", async (t) => {
  for (const phase of ["before", "during"]) {
    let enterReservation, releaseReservation, calls = 0;
    const entered = new Promise((resolve) => {
      enterReservation = resolve;
    });
    const reserved = new Promise((resolve) => {
      releaseReservation = resolve;
    });
    const session = fixture(t, {
      resolvePort: () => {
        calls++;
        enterReservation();
        return reserved;
      },
    });
    if (phase === "before") {
      await session.stop();
      await assert.rejects(session.start(), /startup was cancelled by stop/);
      assert.equal(calls, 0);
    } else {
      const startRejected = assert.rejects(
        session.start(),
        /startup was cancelled by stop/,
      );
      await entered;
      await session.stop();
      releaseReservation(12345);
      await startRejected;
      assert.equal(calls, 1);
    }
    assert.equal(session.child, null);
    assert.equal(session.alive(), false);
    assert.equal(session.verified, false);
    assert.equal(fs.existsSync(session.options.dataDir), false);
    assert.equal(fs.existsSync(session.options.logDir), false);
    await session.stop();
  }
});

test("an occupied saved port is skipped and its unrelated service survives", async (t) => {
  const stranger = http.createServer((_request, response) =>
    response.end("unrelated"),
  );
  await new Promise((resolve) => stranger.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => stranger.close(resolve)));
  const port = stranger.address().port;
  assert.notEqual(await freePort(port), port);
  const session = fixture(t, { preferredPort: port });
  await session.start();
  assert.notEqual(session.port, port);
  await session.stop();
  assert.equal(stranger.listening, true);
});

for (const variable of ["TEST_WRONG_APP", "TEST_WRONG_TOKEN", "TEST_UNREADY"]) {
  test(`${variable} fails closed and stops only its spawned child`, async (t) => {
    const session = fixture(t, { environment: { [variable]: "1" } });
    await assert.rejects(session.start(), /did not match|not ready/);
    assert.equal(session.alive(), false);
  });
}

test("shutdown timeout uses the owned child handle and remains idempotent", async (t) => {
  const session = fixture(t, {
    environment: { TEST_IGNORE_SHUTDOWN: "1" },
    shutdownGraceMs: 10,
  });
  await session.start();
  await Promise.all([session.stop(), session.stop()]);
  assert.equal(session.alive(), false);
});

test("status reader rejects oversized/malformed payloads without unbounded retention", async (t) => {
  const server = http.createServer((request, response) => {
    response.end(request.url === "/large" ? "x".repeat(70000) : "not-json");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const origin = localOrigin(server.address().port);
  await assert.rejects(requestJSON(origin, "/large"), /64 KiB/);
  await assert.rejects(requestJSON(origin, "/bad"), /invalid JSON/);
});

test("installer config is per-user, non-elevating, explicit arm64 and native resources only", () => {
  const config = require("../electron-builder.cjs");
  assert.equal(config.appId, "com.taylorkalin.videoeditor8k");
  assert.equal(config.productName, "8KLosslessVideoEditor");
  assert.equal(config.nsis.perMachine, false);
  assert.equal(config.nsis.allowElevation, false);
  assert.equal(config.nsis.packElevateHelper, false);
  assert.equal(config.nsis.deleteAppDataOnUninstall, false);
  assert.equal(config.mac.identity, process.env.EDITOR_CODESIGN_IDENTITY || "-");
  assert.equal(config.mac.minimumSystemVersion, "26.6.2");
  assert.deepEqual(config.mac.target[0].arch, ["arm64"]);
  assert.equal(config.publish, null);
});

test("Mac ad-hoc and Developer ID settings preserve Windows non-elevation", () => {
  const { execFileSync } = require("node:child_process");
  for (const identity of ["", "-", "Developer ID Application: Taylor Kalin (TESTTEAM)"]) {
    const config = JSON.parse(execFileSync(process.execPath, [
      "-e",
      "console.log(JSON.stringify(require('./electron-builder.cjs')))",
    ], {
      cwd: path.join(__dirname, ".."),
      env: { ...process.env, EDITOR_CODESIGN_IDENTITY: identity },
      encoding: "utf8",
    }));
    assert.equal(config.mac.identity, identity || "-");
    assert.equal(config.mac.hardenedRuntime, !!identity && identity !== "-");
    assert.equal(config.mac.minimumSystemVersion, "26.6.2");
    assert.equal(config.nsis.perMachine, false);
    assert.equal(config.nsis.allowElevation, false);
    assert.equal(config.nsis.packElevateHelper, false);
    assert.equal(config.win.requestedExecutionLevel, "asInvoker");
    assert.equal(config.copyright, "Copyright © 2026 Taylor Kalin");
  }
});

test("scoped patched minimatch consumers retain their actual build-tool APIs", () => {
  const { createRequire } = require("node:module");
  const builderRequire = createRequire(require.resolve("app-builder-lib"));
  const matching = builderRequire("minimatch");
  assert.equal(
    new matching.Minimatch("**/*.cjs", { dot: true }).match(
      "test/desktop.test.cjs",
    ),
    true,
  );
  assert.equal(
    new matching.Minimatch("**/*.cjs").match("test/source.mov"),
    false,
  );
  const { FileMatcher } = require("app-builder-lib/out/fileMatcher");
  const parsed = [];
  new FileMatcher("source", "target", (value) => value, [
    "**/*.cjs",
  ]).computeParsedPatterns(parsed);
  assert.equal(parsed[0].match("main.cjs"), true);
  const universalRequire = createRequire(
    require.resolve("@electron/universal"),
  );
  assert.equal(
    universalRequire("minimatch").minimatch("bin/worker", "**/worker"),
    true,
  );
  const filelistRequire = createRequire(require.resolve("filelist"));
  assert.deepEqual(
    filelistRequire("minimatch").match(["main.cjs", "source.mov"], "*.cjs"),
    ["main.cjs"],
  );
  // These legacy consumers call the module itself; an indiscriminate major
  // override would break ASAR packing and Mac directory comparison.
  for (const name of ["@electron/asar", "dir-compare"]) {
    const legacy = createRequire(require.resolve(name))("minimatch");
    assert.equal(typeof legacy, "function", name);
    assert.equal(legacy("main.cjs", "*.cjs"), true, name);
  }
});
