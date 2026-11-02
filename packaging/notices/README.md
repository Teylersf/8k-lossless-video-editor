# Original dependency notices

These files preserve upstream terms for the native desktop distribution and
its reviewed build inputs. They do not change the application license in the
repository root or the scoped `LICENSE-EXCEPTION` permission.

- `nvidia/`: CUDA 12.6 and cuDNN 9.10.2 terms, with source and file checksums.
- `intel-onemkl-2025.3/`: Intel's original ISSL license and all eight versioned
  third-party notice files from its official `onemkl-license` wheel.
- `magma-2.5.4/`: the original MAGMA BSD copyright reported by the actual
  Windows PyTorch build configuration.
- `gcc-runtime/`: GPLv3, GCC Runtime Library Exception 3.1, and the original
  GCC/MinGW package copyright notices for retained vendor runtime code and the
  earlier compiler audit.
- `llvm-mingw-20261020/`: the actual clean DLL build's LLVM/MinGW runtime terms,
  including LLVM exceptions and original x86_64 runtime copyrights.
- `electron-45.2.1/`: Electron's MIT license and complete original Chromium
  component-license HTML from the Windows runtime distribution.
- `ffmpeg-windows/`: exact pinned upstream component notices and public codec
  header licenses. `NOTICE-MANIFEST.json` records source revisions, Git blob
  identities where available, SHA-256 hashes and collection scope.

The FFmpeg notice inventory conservatively includes original vendor build
inputs and audit material. A notice file's presence is not a claim that its
code is linked into the final executable. In particular, the release's
targeted native rebuilds remove Chromaprint/FFTW and libzvbi from the affected
DLLs; original vendor input notices can still document that audit history.
The final binary/source manifests and actual native build receipts identify
what is distributed. Preserve the complete original texts, including their
separate third-party sections.

The release source collector snapshots this directory with the application
source and preserves the exact pinned source archives separately. See
[`docs/THIRD_PARTY.md`](../../docs/THIRD_PARTY.md) for the component inventory,
source-distribution method and platform-specific scope.
