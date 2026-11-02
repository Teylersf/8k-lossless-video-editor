# Native desktop shell

8K Lossless Video Editor by **Taylor Kalin**. GPL-3.0-or-later with the owner-code NVIDIA permission in ../LICENSE-EXCEPTION; bundled Electron,
PyTorch, PyAV, FFmpeg and other components retain their respective licenses.
This folder packages the existing local editor rather than a second renderer.
The installed app requires no Node, Python, Homebrew or build toolchain.

The current arm64 DMG requires **macOS 26.6.2 or later**. This matches the
highest deployment target in its bundled native libraries. Builds for older
macOS versions need a matching native payload and an updated
`mac.minimumSystemVersion`.

The shell pins Electron **45.2.1** and electron-builder **26.18.0**, verified from
the official npm registry. Renderer sandboxing and context isolation remain on;
Node integration and webviews remain off. Only fullscreen on the owned page is
permitted; camera, microphone, location and other permissions are denied. No renderer IPC API is
exposed. External links open only validated HTTPS URLs in the system browser.
All editor/media network traffic remains on the owned loopback origin.

## Backend resource contract

Build the platform-native PyInstaller onedir tree first:

```text
build/backend/win32/8k-editor-backend.exe
build/backend/darwin/8k-editor-backend
  _internal/app/dist/index.html
  _internal/engine/...
  _internal/bin/ffmpeg[.exe]
  _internal/bin/ffprobe[.exe]
  _internal/licenses/...
```

`EDITOR_BACKEND_DIR` can override this source directory during packaging or
unpackaged development. Installed apps always launch their own
`resources/backend/8k-editor-backend[.exe] --server`.

The shell sets `EDITOR_DATA_DIR=<userData>/data`,
`EDITOR_LOG_DIR=<userData>/logs`, `EDITOR_PORT=<available loopback port>` and a
fresh `EDITOR_INSTANCE_TOKEN`. The prior successful port is preferred if free
to preserve browser recovery storage. A collision allocates another port; it
never adopts a listener already using the port. The tiny bind-to-spawn race is
handled by matching **both** the exact app identity and the fresh token.
`GET /api/status` must return `desktop_instance_token`. Shutdown includes
`X-Editor-Client: 8k-lossless-editor` and matching `X-Editor-Instance`.

The executable's `_internal/bin` and `bin` directories are prepended to PATH.
GPU selection defaults to `auto`; an explicit existing `STUDIO_DEVICE` remains
respected. App data, sources, projects, browser storage and logs are outside
read-only resources and are preserved by uninstall. Lens Studio on8766 and any
source-checkout editor on8770 are unrelated and are never stopped.

Shutdown first authenticates the same owned server and asks it to stop its
workers. After a bounded wait, fallback signals use only the spawned
ChildProcess handle. The shell does not discover or terminate processes by
name, a global PID search, or an occupied port.

## Build and tests

```sh
cd desktop
npm ci
npm test
npm run test:electron # actual Electron GUI + isolated mock backend, then clean shutdown
npm run pack
npm run dist:win   # on Windows; per-user x64 NSIS installer
npm run dist:mac   # on Apple Silicon macOS; arm64 app + DMG
```

Outputs go to `out/installers/`. `beforePack` rejects missing backend/frontend,
bin or license resources. Root packaging scripts prepare the backend; this
folder does not install Python dependencies or download model weights.
Optional native branding files are `assets/icon.png`, `icon.ico`, `icon.icns`.

Windows builds request `asInvoker`; NSIS is per-user and disallows elevation.
Mac builds request an ad-hoc signature (`identity: "-"`) and the normal JIT
entitlement needed by Electron. A certificate/notarization workflow can be
supplied by the release owner; local artifacts are **not** represented as a
verified publisher or notarized release. No OS security settings are changed.

`npm test` exercises real short-lived mock backend child processes, ownership,
occupied-port isolation, authenticated shutdown, failure cleanup, bounded
response parsing, URL/download policies, and installer settings. Actual
packaged Windows/Mac launch/render/install smoke tests are separate release
requirements; passing these tests alone does not claim a packaged installer.

The lock applies minimatch11.0.3 only to compatible version5..10 consumers;
the named/static APIs are covered by a real build-tool compatibility test.
Legacy ASAR, glob and dir-compare consumers still require the callable3.1 API.
The current npm audit reports **12 moderate build-time advisories**, zero high
or critical, and `npm audit --omit=dev` reports zero production dependencies
with advisories. No compatible3.x patch for
[globparts amplification](https://github.com/advisories/GHSA-g3hv-75wg-m249)
was available during this release. Build inputs/patterns are owned repository
files; these packaging tools are not included in the installed renderer.
No incompatible global override or unsupported builder downgrade is applied.

Primary implementation references:
[Electron security](https://www.electronjs.org/docs/latest/tutorial/security/),
[Electron downloads](https://www.electronjs.org/docs/latest/api/download-item),
[electron-builder NSIS](https://www.electron.build/nsis/),
[electron-builder Mac](https://www.electron.build/mac/).
