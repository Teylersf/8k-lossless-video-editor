# Validation and release scope

Version **0.1.0-beta.1** is an initial beta. Included tests exercise actual
small media processing; availability flags are not substituted for codec
round trips. Results for a separate Lens Studio project do not establish this
editor's end-to-end performance.

## Completed local checks

Windows checks passed the client build/lint, 23 editor state tests, 26 engine
tests, four catalogue tests and 20 API tests, including precise MOV stream
duration and NUT final-frame regressions. These are small synthetic cases,
not sustained 8K benchmarks.

Coverage includes:

- Timeline trim/sequence, overlap/compositing, opacity keyframes, titles,
  transforms/crop, grade, speed/reverse and actual lens rendering.
- Independent decoded RGB16 sample hashes; high-frequency identity samples;
  project frame cadence; float32 audio mixing including six-channel samples
  below 24-bit quantization; explicit output audio settings.
- Actual catalogue profile encode/decode tests. Optional absent encoders are
  explicitly skipped; FFV1 remains required. A skipped codec is not verified.
- Native PNG16, float EXR values above 1, explicit image-sequence frame order,
  DNxHR/MXF, audio/video browser assets and real waveform data.
- Header/origin/host restrictions, bounded request bodies, private asset paths,
  source hashes/unchanged bytes, malformed import cleanup and metadata-only
  source interpretation.
- Project save/reload/offline relinking, frozen render snapshots, queue limits,
  queued/running cancellation, owned preview shutdown, interrupted restart and
  completed-download integrity checks.
- A real API→owned preview→queued CPU FFV1 render→download→independent decode.
- NUT packet extents include the last picture duration, including VFR gaps;
  the regression does not infer duration from frame count/nominal FPS.
- MOV duration prefers positive stream ticks × time base over rounded decimal
  text; a 349-frame 30000/1001 fixture checks this distinction.

## Native NVIDIA 8K offline proof

On 2026-11-02 UTC, the local validation script passed a two-clip, two-frame
7680×4320 / 24 fps run on an NVIDIA RTX 3090. The FFV1 master independently
recovered exact RGB16 samples, and stereo float32 PCM recovered all 4,000
samples per channel exactly. The NVENC HEVC result recovered its canonical
target YUV10 samples. A separate effects/title/overlay render also decoded and
verified. Source hashes remained unchanged.

| Case | Render + engine verification | Additional independent verification |
| --- | ---: | ---: |
| RGB16 FFV1 master | 31.825 s | 12.604 s |
| NVENC HEVC codec-lossless YUV10 | 14.701 s | 1.666 s |
| Effects, title and overlay | 46.606 s | 19.270 s |

The complete script took 146.98 seconds including fixture generation and
verification. These timings cover just two output frames. They establish a
native-8K raster/sample result, not sustained frame rate or long-form stability.
GPU-memory measurement was unavailable; no peak-VRAM claim is made. The
[sanitized public receipt](validation/windows-rtx3090-native-8k.json) records
the tested scope without personal source paths.

## Actual editor: 48-frame 8K DPX16 cinema master

The actual GUI rendered a 48-frame **7680×4320 / 24 fps / two-second DPX16**
sequence with lens/color treatment, animated composition, titles and a float32
PCM mix. The result was a 9,556,010,570-byte ZIP archive. Independent verification
decoded all 48 DPX frames, matched every RGB16 frame hash and the complete
sample-stream hash against the worker manifest, checked BT.709 metadata and ZIP
CRC, and confirmed the imported DPX and audio sources remained unchanged.
Independent verification took **84.868 seconds**.

The [public receipt](validation/cinema-8k-dpx16.json),
[actual editor screenshot](images/editor-8k-dpx.png) and
[output-frame viewing JPEG](images/cinema-8k-dpx-frame.jpg) accompany this proof.
The scene derives from a licensed high-resolution JPEG photograph, cropped and
downsampled into the demo DPX source. It demonstrates processed 16-bit delivery,
not 16-bit camera acquisition or native motion capture. The job did not record
an exact code-file provenance snapshot, so the receipt makes no such claim.

## Consumer Apple Silicon: native M4 8K proof

The [published Apple M4 receipt](validation/mac-m4-native-8k.json) records a
native ARM64 Mac mini with 16 GiB memory, macOS 26.6.2 and PyTorch 2.11.0/MPS.
At tested revision `336abdaceea469dd471850fc9f64f6f5ea804948`, a two-clip,
**24-frame 7680×4320 / 24 fps** RGB16 FFV1 master independently recovered every
expected frame in sequence order and all 48,000 float32 PCM samples per stereo
channel. Source hashes were unchanged.

| M4 case | Render + engine verification | Additional independent verification |
| --- | ---: | ---: |
| 24-frame RGB16 FFV1 master | 94.593 s | 58.36 s |
| Two-frame effects/title/overlay proof | 28.39 s | 6.80 s |

The complete native validation script took **280.567 seconds**, including
fixture generation and verification. The same public receipt includes separate
PNG16 sequence and real browser editing/export checks. MPS was selected for
supported effects; decoding, compositing and audio can use CPU. There was no
NVENC test or measured Metal allocator peak. This is a one-second offline
master proof, not a sustained 8K playback benchmark.

## Mac and browser scope

The actual Windows browser flow passed import, sequence settings, split,
undo/redo, title keyframes, lens effects, save/recovery, full four-second program
preview, queued cancellation and download of a verified 96-frame RGB16/PCM
master. The downloaded file's SHA256 matched the server's verified output.
Source-to-program switching passed after a finite-metadata seeking fix.

Long-form 8K stability, interactive frame rate and broad real-world
camera/interchange variant coverage remain separate validation work.

The native macOS CI job asserts ARM64 and reports actual MPS availability. It
runs small actual Metal renders only when MPS is available; otherwise it reports
a skip. A hosted/paravirtual GPU result is not a consumer-Mac or sustained 8K
benchmark. Windows CPU/CUDA results do not prove Metal/VideoToolbox behavior.
Public CI status is available on the repository's Actions page once a run has
completed; a checked-in workflow alone is not a passing run.

Browser QA should cover import, source/program views, drag/trim/split,
undo/redo, source interpretation, title/audio/keyframes, save/reopen/relink,
full-timeline preview, cancellation and export/download. Responsive controls
do not imply a phone can render 8K; processing occurs on the local host.

## Installed desktop applications

The Windows x64 NSIS package installs per user and identifies **Taylor Kalin**
as its publisher. The installer and installed Electron app were launched with
PATH restricted to Windows System32. The native GUI imported two 7680×4320
RGB16 clips and float32 PCM audio, saved a two-frame sequence, exported FFV1,
and downloaded it through the native Save As dialog. Independent FFmpeg 7
decoding recovered all original RGB16 pixels and stereo float32 samples
exactly. Normal quit stopped only its owned backend; relaunch recovered the
project and media. The installer is an **unsigned beta**.

- [Installed Windows package receipt](validation/windows-native-installer.json)
- [Frozen CUDA engine, NVENC, effects and AV1 conversion receipt](validation/windows-native-desktop.json)
- [Installed Windows export screenshot](images/editor-windows-native-export.png)

The final Apple Silicon v5 app also passed installed AV1 export, native
download, independent decode/audio checks and quit/relaunch with the saved
project intact. Its two-frame 7680×4320 AV1 delivery test uses lossy video and
FLAC carrying a documented 24-bit conversion of the float32 timeline mix.
Successful exports show a green completion state; all seven processing notes
remain available in a collapsed disclosure. All 52 Python, 26 frontend and
16 desktop checks passed on the M4 Mac. The current bundled DMG requires
**macOS 26.6.2 or later** and uses **ad-hoc signing without notarization**.

- [Installed Mac app and final DMG receipt](validation/mac-native-desktop.json)
- [Verified Mac AV1 export screenshot](images/editor-mac-av1-export.jpg)

These short native tests establish the stated raster, samples and app flows;
they do not establish real-time playback or long-form 8K stability. The earlier
24-frame M4 and 48-frame DPX16 proofs above remain separate results.

## Reproduce the checks

Use the project environment's Python. On Windows replace
`.venv-editor/bin/python` with `.venv-editor\Scripts\python.exe`.

```bash
npm --prefix app ci
npm --prefix app run build
npm --prefix app run lint
npm --prefix app test
STUDIO_DEVICE=cpu .venv-editor/bin/python -m unittest discover -s . -p 'test_*.py' -v
STUDIO_DEVICE=cpu .venv-editor/bin/python -m unittest discover -s engine -p 'test_*.py' -v
```

The tests use temporary source/output folders and TestClient; they do not open
a persistent browser service or operate camera/card hardware. Integration
workers are actual subprocesses. The queue/cancellation test doubles isolate
API race conditions; separate tests exercise real codec workers.

CI uses standard `ubuntu-24.04` and `macos-15` runners, with Python 3.13,
Node 22 and PyTorch 2.11.0. Linux installs the CPU wheel; Mac uses native ARM64
packages and the keg-only FFmpeg-full PATH. These are standard public runners,
not paid `-large` runners. GitHub's [runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
currently lists `macos-15` as ARM64; the job additionally checks at runtime.

## Evidence for a hardware claim

Record the app revision, OS, Python/PyTorch/PyAV/FFmpeg versions, actual device,
source raster/pixel format/rate, project edits, export profile, frame count,
decoded sample/audio verification, source-before/after hashes, output hash and
elapsed time. State clip duration and peak-memory scope. Two rendered frames
can establish a sample/raster result; they cannot establish sustained playback
or long-form stability. Keep personal footage, local source paths and raw logs
outside the public repository.

See [Format support](FORMAT_SUPPORT.md) for the distinction between original
packets, rendered RGB16, codec-lossless YUV and delivery/intermediate formats.
