"use strict";
// GPL-2.0-only. Privileged native shell; renderer receives no Node/preload API.
const { app, BrowserWindow, Menu, dialog, screen, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { BackendSession } = require("./backend.cjs");
const {
  isOwnedURL,
  isSafeExternal,
  isOwnedDownload,
  canGrantPermission,
} = require("./security.cjs");

app.setName("8KLosslessVideoEditor");
app.enableSandbox();
const smokeMode = process.argv.includes("--desktop-smoke");
const mockBackend =
  smokeMode && !app.isPackaged && process.env.EDITOR_DESKTOP_TEST === "1";
if (mockBackend && process.env.EDITOR_DESKTOP_TEST_USER_DATA) {
  const testData = path.resolve(process.env.EDITOR_DESKTOP_TEST_USER_DATA);
  fs.mkdirSync(testData, { recursive: true });
  app.setPath("userData", testData);
}
const singleInstance = app.requestSingleInstanceLock();
let window = null;
let backend = null;
let starting = false;
let quitting = false;
let stopped = false;
let userData = "";
let logs = "";
let smokeReceipt = null;
let exitCode = 0;
const splash = pathToFileURL(
  path.join(__dirname, "assets", "splash.html"),
).href;

function log(message) {
  if (!logs) return;
  try {
    fs.appendFileSync(
      path.join(logs, "desktop.log"),
      `${new Date().toISOString()} ${message}\n`,
    );
  } catch {
    /* An unwritable log must not cause a second fatal exception. */
  }
}

function activeWindow() {
  return window && !window.isDestroyed() ? window : undefined;
}

function focusWindow() {
  const current = activeWindow();
  if (!current) return;
  if (current.isMinimized()) current.restore();
  current.show();
  current.focus();
}

function ownedPage() {
  return (
    !!backend?.verified &&
    !!activeWindow() &&
    isOwnedURL(window.webContents.getURL(), backend.origin)
  );
}

async function openExternal(url) {
  if (isSafeExternal(url)) {
    try {
      await shell.openExternal(url);
    } catch (error) {
      log(`Could not open external link: ${error.message}`);
    }
  }
}

async function reveal(directory) {
  const result = await shell.openPath(directory);
  if (result) dialog.showErrorBox("Could not open folder", result);
}

async function editorAction(action) {
  if (!ownedPage()) return;
  // Fixed commands only, executed in the verified local UI. No renderer IPC can
  // choose code, executable paths, file-system paths, process IDs or URLs.
  const commands = {
    open: `(() => { const input = document.querySelector('input[type="file"][accept*="json"]'); if (!input) throw new Error('Project chooser is unavailable'); input.click(); })()`,
    save: `window.dispatchEvent(new KeyboardEvent('keydown', {key:'s', ${process.platform === "darwin" ? "metaKey" : "ctrlKey"}:true, bubbles:true}))`,
  };
  if (!Object.hasOwn(commands, action)) return;
  try {
    await window.webContents.executeJavaScript(commands[action], true);
  } catch (error) {
    dialog.showErrorBox("Editor action could not finish", error.message);
  }
}

function installMenu() {
  const about = () =>
    dialog.showMessageBox(activeWindow(), {
      type: "info",
      title: "About 8K Lossless Video Editor",
      message: "8K Lossless Video Editor",
      detail: `Version ${app.getVersion()}\nBy Taylor Kalin\n\nLocal timeline editing and verified RGB16 masters.\nGPL-2.0-only. Bundled components retain their own licenses.\n\nLocal builds are unsigned/ad-hoc unless separately signed.`,
      buttons: ["OK"],
    });
  const template = [];
  if (process.platform === "darwin")
    template.push({
      label: app.name,
      submenu: [
        { label: "About 8K Lossless Video Editor", click: about },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    });
  template.push({
    label: "File",
    submenu: [
      {
        label: "Open Project JSON…",
        accelerator: "CmdOrCtrl+O",
        click: () => void editorAction("open"),
      },
      {
        label: "Save Project",
        accelerator: "CmdOrCtrl+S",
        click: () => void editorAction("save"),
      },
      { type: "separator" },
      {
        label: "Show Project & Media Folder",
        click: () => void reveal(path.join(userData, "data")),
      },
      { label: "Show Logs", click: () => void reveal(logs) },
      { type: "separator" },
      { role: "quit" },
    ],
  });
  template.push({
    label: "Edit",
    submenu: [
      { role: "cut" },
      { role: "copy" },
      { role: "paste" },
      { type: "separator" },
      { role: "selectAll" },
    ],
  });
  template.push({
    label: "View",
    submenu: [
      {
        label: "Reload Editor",
        accelerator: "CmdOrCtrl+R",
        click: () => {
          if (ownedPage()) window.webContents.reload();
        },
      },
      { type: "separator" },
      { role: "resetZoom" },
      { role: "zoomIn" },
      { role: "zoomOut" },
      { type: "separator" },
      { role: "togglefullscreen" },
    ],
  });
  template.push({ role: "windowMenu" });
  template.push({
    label: "Help",
    submenu: [
      { label: "About 8K Lossless Video Editor", click: about },
      {
        label: "Open Source on GitHub",
        click: () => void openExternal(require("./package.json").homepage),
      },
      {
        label: "Bundled Licenses",
        click: () => {
          const root = app.isPackaged
            ? path.join(process.resourcesPath, "backend")
            : path.join(__dirname, "..", "build", "backend", process.platform);
          void reveal(path.join(root, "_internal", "licenses"));
        },
      },
    ],
  });
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function secureWindow(current) {
  const session = current.webContents.session;
  session.setPermissionRequestHandler(
    (contents, permission, callback, details) =>
      callback(
        !!backend?.verified &&
          contents === current.webContents &&
          canGrantPermission(
            permission,
            details.requestingUrl || contents.getURL(),
            backend.origin,
          ),
      ),
  );
  session.setPermissionCheckHandler(
    (contents, permission, requestingOrigin) =>
      !!backend?.verified &&
      contents === current.webContents &&
      canGrantPermission(permission, requestingOrigin, backend.origin),
  );
  session.webRequest.onBeforeRequest((details, callback) => {
    let allowed = details.url === splash;
    if (backend?.verified) {
      allowed ||= isOwnedURL(details.url, backend.origin);
      try {
        allowed ||=
          new URL(details.url).protocol === "blob:" &&
          new URL(details.url).origin === backend.origin;
      } catch {
        /* Malformed URLs are denied. */
      }
    }
    callback({ cancel: !allowed });
  });
  session.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    if (backend?.verified && isOwnedURL(details.url, backend.origin))
      headers["X-Editor-Instance"] = backend.token;
    callback({ requestHeaders: headers });
  });
  session.webRequest.onHeadersReceived((details, callback) => {
    if (!backend?.verified || !isOwnedURL(details.url, backend.origin))
      return callback({});
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [
          "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self'",
        ],
      },
    });
  });
  current.webContents.on("will-navigate", (event, url) => {
    if (!backend?.verified || !isOwnedURL(url, backend.origin))
      event.preventDefault();
  });
  current.webContents.on("will-redirect", (event, url) => {
    if (!backend?.verified || !isOwnedURL(url, backend.origin))
      event.preventDefault();
  });
  current.webContents.on("will-attach-webview", (event) =>
    event.preventDefault(),
  );
  current.webContents.setWindowOpenHandler(({ url }) => {
    if (ownedPage()) void openExternal(url);
    return { action: "deny" };
  });
  session.on("will-download", (event, item, contents) => {
    if (
      !backend?.verified ||
      contents !== current.webContents ||
      !isOwnedDownload(item.getURL(), backend.origin, item.getInitiatorOrigin())
    ) {
      event.preventDefault();
      return;
    }
    item.setSaveDialogOptions({
      title: "Save editor output",
      defaultPath: path.join(
        app.getPath("downloads"),
        path.basename(item.getFilename()),
      ),
    });
    item.once("done", (_event, state) => {
      if (state === "interrupted" && !quitting)
        dialog.showErrorBox(
          "Download interrupted",
          "The output remains in your local project data. Retry its Download action.",
        );
    });
  });
}

function createWindow() {
  const area = screen.getPrimaryDisplay().workAreaSize;
  const icon = path.join(__dirname, "assets", "icon.png");
  window = new BrowserWindow({
    title: "8K Lossless Video Editor",
    width: Math.min(1440, area.width),
    height: Math.min(980, area.height),
    minWidth: 1000,
    minHeight: 720,
    show: false,
    backgroundColor: "#111820",
    icon: fs.existsSync(icon) ? icon : undefined,
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      webviewTag: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      navigateOnDragDrop: false,
      partition: "persist:8k-lossless-editor",
      devTools: !app.isPackaged,
    },
  });
  secureWindow(window);
  window.once("ready-to-show", () => {
    if (!quitting) focusWindow();
  });
  window.on("closed", () => {
    window = null;
  });
  void window.loadURL(splash);
}

function preferredPort() {
  try {
    const value = JSON.parse(
      fs.readFileSync(path.join(userData, "desktop-state.json"), "utf8"),
    ).port;
    return Number.isInteger(value) && value >= 1 && value <= 65535 ? value : 0;
  } catch {
    return 0;
  }
}

async function startBackend() {
  if (starting || quitting) return;
  starting = true;
  try {
    const directory = app.isPackaged
      ? path.join(process.resourcesPath, "backend")
      : path.resolve(
          process.env.EDITOR_BACKEND_DIR ||
            path.join(__dirname, "..", "build", "backend", process.platform),
        );
    backend = new BackendSession({
      executable: mockBackend
        ? process.execPath
        : path.join(
            directory,
            process.platform === "win32"
              ? "8k-editor-backend.exe"
              : "8k-editor-backend",
          ),
      ...(mockBackend
        ? {
            args: [path.join(__dirname, "test", "mock-backend.cjs")],
            environment: { ELECTRON_RUN_AS_NODE: "1" },
          }
        : {}),
      dataDir: path.join(userData, "data"),
      logDir: logs,
      preferredPort: preferredPort(),
    });
    backend.on("log-error", (error) =>
      log(`Backend log error: ${error.message}`),
    );
    backend.on("unexpected-exit", ({ code }) => {
      log(`Owned backend exited (${code})`);
      if (backend.verified && !quitting && !starting)
        dialog.showErrorBox(
          "Editing engine stopped",
          "Your saved projects are in the Project & Media Folder. Quit and reopen the app to restart its private engine. See Show Logs for details.",
        );
    });
    const status = await backend.start();
    if (quitting) return;
    fs.writeFileSync(
      path.join(userData, "desktop-state.json"),
      JSON.stringify({ port: backend.port }),
    );
    log(
      `Private backend ready on loopback:${backend.port}; device:${status.device || "unknown"}`,
    );
    await window.loadURL(backend.origin);
    focusWindow();
    if (smokeMode) {
      const page = await window.webContents.executeJavaScript(
        `({nodeUnavailable:typeof require==='undefined',processUnavailable:typeof process==='undefined',editorRendered:!!document.querySelector('.editor-shell')})`,
      );
      const preferences = window.webContents.getLastWebPreferences();
      smokeReceipt = {
        version: app.getVersion(),
        mock_backend: mockBackend,
        backend_origin: backend.origin,
        owned_identity_verified: backend.verified,
        backend_ready: status.ready,
        ...page,
        context_isolation: preferences.contextIsolation,
        sandbox: preferences.sandbox,
        node_integration: preferences.nodeIntegration,
        backend_stopped: false,
      };
      if (
        !page.nodeUnavailable ||
        !page.processUnavailable ||
        !page.editorRendered ||
        !preferences.contextIsolation ||
        !preferences.sandbox ||
        preferences.nodeIntegration
      ) {
        throw new Error("Desktop sandbox/render smoke assertion failed");
      }
      // Chromium's compositor may not have a capturable surface immediately
      // after DOM load (including remote/headless desktops). Image capture is
      // diagnostic, separate from the sandbox/ownership assertions above.
      for (let attempt = 0; attempt < 3; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        try {
          const image = await window.capturePage();
          fs.writeFileSync(path.join(logs, "desktop-smoke.png"), image.toPNG());
          smokeReceipt.screenshot_saved = true;
          break;
        } catch (error) {
          smokeReceipt.screenshot_error = error.message;
        }
      }
      app.quit();
    }
  } catch (error) {
    log(`Startup failed: ${error.message}`);
    if (smokeMode) {
      exitCode = 1;
      smokeReceipt = {
        ...smokeReceipt,
        error: error.message,
        mock_backend: mockBackend,
        backend_stopped: false,
      };
      app.quit();
    } else if (!quitting) {
      const result = await dialog.showMessageBox(activeWindow(), {
        type: "error",
        title: "Editor could not start",
        message: "The private editing engine could not start.",
        detail: `${error.message}\n\nNo existing editor or Lens Studio service was adopted or stopped.`,
        buttons: ["Quit", "Show Logs", "Retry"],
        defaultId: 0,
        cancelId: 0,
      });
      if (result.response === 1) {
        await reveal(logs);
        app.quit();
      } else if (result.response === 2) {
        starting = false;
        return startBackend();
      } else app.quit();
    }
  } finally {
    starting = false;
  }
}

if (!singleInstance) app.quit();
else {
  app.on("second-instance", focusWindow);
  app
    .whenReady()
    .then(async () => {
      userData = app.getPath("userData");
      logs = path.join(userData, "logs");
      fs.mkdirSync(logs, { recursive: true });
      createWindow();
      installMenu();
      await startBackend();
    })
    .catch((error) => {
      dialog.showErrorBox("Desktop startup failed", error.message);
      app.quit();
    });
  app.on("activate", focusWindow);
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (stopped) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    (async () => {
      try {
        await backend?.stop();
        if (smokeReceipt) {
          smokeReceipt.backend_stopped = !backend?.alive();
          fs.writeFileSync(
            path.join(logs, "desktop-smoke.json"),
            JSON.stringify(smokeReceipt, null, 2),
          );
        }
        stopped = true;
        if (smokeMode) app.exit(exitCode);
        else app.quit();
      } catch (error) {
        log(`Shutdown failed: ${error.message}`);
        quitting = false;
        dialog.showErrorBox("Editing engine is still stopping", error.message);
      }
    })();
  });
}
