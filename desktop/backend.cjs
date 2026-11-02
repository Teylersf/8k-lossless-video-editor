"use strict";
// GPL-2.0-only. Own a spawned child and token, never a process found by name/port.
const { spawn } = require("node:child_process");
const { randomBytes } = require("node:crypto");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const { performance } = require("node:perf_hooks");
const { localOrigin, assertOwnedStatus } = require("./security.cjs");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function requestJSON(
  origin,
  route,
  { method = "GET", headers = {}, timeout = 2500 } = {},
) {
  return new Promise((resolve, reject) => {
    const request = http.request(
      new URL(route, origin),
      { method, headers, timeout },
      (response) => {
        const chunks = [];
        let length = 0;
        response.on("data", (chunk) => {
          length += chunk.length;
          if (length > 65536)
            response.destroy(
              new Error("Local status response exceeded 64 KiB"),
            );
          else chunks.push(chunk);
        });
        response.on("error", reject);
        response.on("end", () => {
          if (response.statusCode !== 200)
            return reject(
              new Error(`Local service returned HTTP ${response.statusCode}`),
            );
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
          } catch {
            reject(new Error("Local service returned invalid JSON"));
          }
        });
      },
    );
    request.on("timeout", () =>
      request.destroy(new Error("Local service request timed out")),
    );
    request.on("error", reject);
    request.end();
  });
}

async function freePort(preferred = 0) {
  const reserve = (port) =>
    new Promise((resolve, reject) => {
      const server = net.createServer();
      server.once("error", reject);
      server.listen({ host: "127.0.0.1", port, exclusive: true }, () => {
        const value = server.address().port;
        server.close((error) => (error ? reject(error) : resolve(value)));
      });
    });
  try {
    return await reserve(preferred);
  } catch (error) {
    if (preferred && error.code === "EADDRINUSE") return reserve(0);
    throw error;
  }
}

class BackendSession extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.child = null;
    this.exited = false;
    this.stopping = false;
    this.verified = false;
    this.token = randomBytes(32).toString("hex");
    this.origin = "";
    this.log = null;
  }

  alive() {
    return (
      !!this.child &&
      !this.exited &&
      this.child.exitCode === null &&
      this.child.signalCode === null
    );
  }

  async start() {
    const {
      executable,
      dataDir,
      logDir,
      preferredPort = 0,
      resolvePort = freePort,
    } = this.options;
    if (this.stopping)
      throw new Error("Desktop backend startup was cancelled by stop");
    if (this.child) throw new Error("A desktop backend can only start once");
    if (!fs.statSync(executable).isFile())
      throw new Error("The bundled backend executable is missing");
    const port = await resolvePort(preferredPort);
    // A quit during reservation must not spawn after stop() has already resolved.
    if (this.stopping)
      throw new Error("Desktop backend startup was cancelled by stop");
    fs.mkdirSync(dataDir, { recursive: true });
    fs.mkdirSync(logDir, { recursive: true });
    this.port = port;
    this.origin = localOrigin(this.port);
    const directory = path.dirname(executable);
    const environment = {
      ...process.env,
      ...this.options.environment,
      EDITOR_PORT: String(this.port),
      EDITOR_INSTANCE_TOKEN: this.token,
      EDITOR_DATA_DIR: dataDir,
      EDITOR_LOG_DIR: logDir,
      PATH: [
        path.join(directory, "_internal", "bin"),
        path.join(directory, "bin"),
        process.env.PATH || "",
      ].join(path.delimiter),
      STUDIO_DEVICE:
        this.options.environment?.STUDIO_DEVICE ||
        process.env.STUDIO_DEVICE ||
        "auto",
    };
    this.log = fs.createWriteStream(path.join(logDir, "backend.log"), {
      flags: "a",
    });
    this.log.on("error", (error) => this.emit("log-error", error));
    this.child = spawn(executable, this.options.args || ["--server"], {
      cwd: dataDir,
      env: environment,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let spawnError = null;
    this.child.once("error", (error) => {
      spawnError = error;
      this.exited = true;
    });
    this.child.once("exit", (code, signal) => {
      this.exited = true;
      if (!this.stopping) this.emit("unexpected-exit", { code, signal });
    });
    this.closed = new Promise((resolve) =>
      this.child.once("close", () => this.log.end(resolve)),
    );
    this.child.stdout.pipe(this.log, { end: false });
    this.child.stderr.pipe(this.log, { end: false });
    const deadline =
      performance.now() + (this.options.startupTimeoutMs ?? 180000);
    try {
      while (performance.now() < deadline) {
        if (!this.alive())
          throw (
            spawnError ||
            new Error(
              "The bundled backend exited during startup. See backend.log.",
            )
          );
        let status;
        try {
          status = await requestJSON(this.origin, "/api/status");
        } catch {
          await delay(this.options.pollIntervalMs ?? 250);
          continue;
        }
        assertOwnedStatus(status, this.token);
        this.verified = true;
        if (!status.ready)
          throw new Error(
            status.reason ||
              "The bundled backend is not ready. Check the bundled FFmpeg/runtime files.",
          );
        return status;
      }
      throw new Error(
        "The bundled backend exceeded its startup deadline. See backend.log.",
      );
    } catch (error) {
      await this.stop();
      throw error;
    }
  }

  async waitForExit(milliseconds) {
    const deadline = performance.now() + milliseconds;
    while (this.alive() && performance.now() < deadline) await delay(50);
    return !this.alive();
  }

  async stop() {
    if (this.stopPromise) return this.stopPromise;
    this.stopping = true;
    this.stopPromise = (async () => {
      if (this.alive() && this.verified) {
        try {
          assertOwnedStatus(
            await requestJSON(this.origin, "/api/status"),
            this.token,
          );
          await requestJSON(this.origin, "/api/shutdown", {
            method: "POST",
            timeout: 5000,
            headers: {
              "X-Editor-Client": "8k-lossless-editor",
              "X-Editor-Instance": this.token,
            },
          });
        } catch {
          /* Never send an unauthenticated fallback shutdown. */
        }
      }
      if (this.alive())
        await this.waitForExit(
          this.verified ? (this.options.shutdownGraceMs ?? 20000) : 0,
        );
      // ChildProcess.kill uses this owned child's process handle. No taskkill,
      // global PID/name/port search, Studio8766 request, or unrelated process signal.
      if (this.alive()) {
        this.child.kill("SIGTERM");
        await this.waitForExit(3000);
      }
      if (this.alive()) {
        this.child.kill("SIGKILL");
        await this.waitForExit(3000);
      }
      if (this.alive())
        throw new Error(
          "The owned backend has not exited; consult the desktop logs.",
        );
      if (this.closed) await Promise.race([this.closed, delay(1000)]);
    })();
    return this.stopPromise;
  }
}

module.exports = { BackendSession, requestJSON, freePort };
