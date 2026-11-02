"use strict";
// Deterministic test child, never a camera/media process or production backend.
const http = require("node:http");
const server = http.createServer((request, response) => {
  const answer = (code, body) => {
    response.writeHead(code, { "Content-Type": "application/json" });
    response.end(JSON.stringify(body));
  };
  if (request.url === "/") {
    response.writeHead(200, { "Content-Type": "text/html" });
    response.end(
      '<!doctype html><html><head><title>Desktop test fixture</title></head><body style="background:#15212c;color:#d9c28d;font:20px sans-serif;padding:50px"><main class="editor-shell"><h1>8K native shell smoke test</h1><p>Isolated mock backend. This is not the production editor.</p></main></body></html>',
    );
    return;
  }
  if (request.url === "/api/status")
    return answer(200, {
      app: process.env.TEST_WRONG_APP
        ? "Another service"
        : "8K Lossless Video Editor",
      desktop_instance_token: process.env.TEST_WRONG_TOKEN
        ? "not-this-instance"
        : process.env.EDITOR_INSTANCE_TOKEN,
      ready: !process.env.TEST_UNREADY,
      reason: "Test runtime is not ready",
      device: "cpu",
    });
  if (request.url === "/api/shutdown" && request.method === "POST") {
    if (
      request.headers["x-editor-client"] !== "8k-lossless-editor" ||
      request.headers["x-editor-instance"] !== process.env.EDITOR_INSTANCE_TOKEN
    )
      return answer(403, { detail: "Not owned" });
    answer(200, { stopping: true });
    if (!process.env.TEST_IGNORE_SHUTDOWN) server.close(() => process.exit(0));
    return;
  }
  answer(404, {});
});
server.listen(Number(process.env.EDITOR_PORT), "127.0.0.1");
