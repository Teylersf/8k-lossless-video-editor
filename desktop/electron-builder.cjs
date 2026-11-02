"use strict";
// GPL-2.0-only. Python/FFmpeg must be built separately before packaging.
const fs = require("node:fs");
const path = require("node:path");
const backend = path.resolve(
  process.env.EDITOR_BACKEND_DIR ||
    path.join(__dirname, "..", "build", "backend", process.platform),
);
const macIdentity = process.env.EDITOR_CODESIGN_IDENTITY || "-";
const optionalIcon = (name) => {
  const icon = path.join(__dirname, "assets", name);
  return fs.existsSync(icon) ? icon : undefined;
};

module.exports = {
  appId: "com.taylorkalin.videoeditor8k",
  productName: "8KLosslessVideoEditor",
  copyright: "Copyright © 2026 Taylor Kalin",
  buildVersion: "0.1.0.0",
  asar: true,
  compression: "maximum",
  directories: { output: "../out/installers", buildResources: "assets" },
  artifactName: "8KLosslessVideoEditor-${version}-${os}-${arch}.${ext}",
  files: [
    "main.cjs",
    "backend.cjs",
    "security.cjs",
    "assets/splash.html",
    "assets/icon.*",
    "package.json",
  ],
  extraResources: [{ from: backend, to: "backend", filter: ["**/*"] }],
  beforePack: async (context) => {
    const binary =
      context.electronPlatformName === "win32"
        ? "8k-editor-backend.exe"
        : "8k-editor-backend";
    for (const required of [
      binary,
      "_internal/app/dist/index.html",
      "_internal/bin",
      "_internal/licenses",
    ]) {
      if (!fs.existsSync(path.join(backend, required)))
        throw new Error(
          `Build the complete native backend before packaging: missing ${path.join(backend, required)}`,
        );
    }
  },
  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    icon: optionalIcon("icon.ico"),
    requestedExecutionLevel: "asInvoker",
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: "8K Lossless Video Editor",
    uninstallDisplayName: "8K Lossless Video Editor",
    deleteAppDataOnUninstall: false,
    license: "../LICENSE",
    runAfterFinish: true,
  },
  mac: {
    target: [{ target: "dmg", arch: ["arm64"] }],
    category: "public.app-category.video",
    bundleShortVersion: "0.1.0",
    bundleVersion: "0.1.0",
    icon: optionalIcon("icon.icns"),
    identity: macIdentity,
    // Hardened library validation needs a shared signing Team ID. Ad-hoc
    // builds have none; Developer ID builds enable runtime hardening.
    hardenedRuntime: macIdentity !== "-",
    entitlements: "assets/entitlements.mac.plist",
    entitlementsInherit: "assets/entitlements.mac.plist",
  },
  dmg: {
    title: "8K Lossless Video Editor",
    window: { width: 560, height: 360 },
    contents: [
      { x: 150, y: 175, type: "file" },
      { x: 410, y: 175, type: "link", path: "/Applications" },
    ],
  },
  publish: null,
};
