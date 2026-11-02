# Validation and release scope

Version **0.1.0-local** is an initial source beta. Included tests exercise actual
small media processing; availability flags are not substituted for codec
round trips. Results for a separate Lens Studio project do not establish this
editor's end-to-end performance.

## Completed local checks

Windows checks passed the client build/lint, 19 editor state tests, 25 engine
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
GPU-memory measurement was unavailable; no peak-VRAM claim is made. Local raw
receipts include private paths and are not copied into this documentation.

## Mac and browser scope

The actual Windows browser flow passed import, sequence settings, split,
undo/redo, title keyframes, lens effects, save/recovery, full four-second program
preview, queued cancellation and download of a verified 96-frame RGB16/PCM
master. The downloaded file's SHA256 matched the server's verified output.
Source-to-program switching passed after a finite-metadata seeking fix.

On 2026-11-01 (America/Los_Angeles), an actual **M4 Mac mini with 16 GiB RAM**,
macOS 26.6.2, native ARM64 Python 3.13.18, PyTorch 2.11.0 and FFmpeg-full 9.0.2
passed a **24-frame, two-clip 7680×4320 / 24 fps offline proof** at commit
`336abdaceea469dd471850fc9f64f6f5ea804948`. All decoded RGB16 samples matched
the original integer fixtures, including low-bit differences and sharp edges;
all 48,000 float32 PCM samples per stereo channel matched exactly. A separate
two-frame lens/title/overlay master recovered its processed RGB16 samples.
Metal/MPS was selected for supported effects; decoding, compositing and audio
also use CPU.

| Case | Render + engine verification | Additional independent verification |
| --- | ---: | ---: |
| 24-frame FFV1 RGB16 master | 94.593 s | 58.359 s |
| Two-frame effects, title and overlay | 28.388 s | 6.795 s |
| 24-frame PNG16 sequence + float32 PCM archive | 85.559 s | 15.801 s |

The PNG16 archive independently recovered all 24 ordered RGB16 frames and exact
stereo audio; its sequence manifest declares the 24 fps grid. PNG files do not
carry individual video timestamps. The FFV1/MKV frame timestamps passed CFR
checks within its millisecond time-base tolerance of 0.501 ms. Source file
hashes remained unchanged. Frontend build/lint and all 23 client, 20 API,
25 engine and four format-catalogue tests passed locally.

The native FFV1/effects run's internal and external samplers observed maximum
summed process-tree RSS of 4.61 and 4.52 GiB; the separate PNG16 run observed
6.16 GiB. Sampled RSS may double-count shared pages and miss transient peaks.
These measurements are not Metal allocator or dedicated GPU-memory peaks;
allocator safeguards remained enabled. See the [sanitized receipt](validation/mac-m4-native-8k.json).

A separate actual Mac browser proof used two original 1080p sources: import,
split, undo/redo, title/color edits, save/reopen with the correct date, full
two-second rendered-program playback and two Source→Program switches all
passed without console errors. A finite 0.5-second seek worked after remount.
The downloaded 48-frame RGB16/FFV1 master matched the verified published file's
SHA256; independent decoding recovered the rendered RGB16 samples and all
96,000 concatenated float32 PCM samples per stereo channel exactly. Source
hashes remained unchanged. This browser proof supplements the separate
native-8K raster/precision results above.

These are short synthetic offline results. Long-form 8K stability, interactive
frame rate and broad real-world camera/interchange variant coverage remain
separate validation work.

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
