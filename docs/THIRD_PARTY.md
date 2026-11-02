# Third-party software, notices, and source

8K Lossless Video Editor is created by **Taylor Kalin**. The application's own license is in [LICENSE](../LICENSE). Bundled dependencies keep their original copyrights and licenses; the application's license does not replace them. NVIDIA runtime libraries, when included in a Windows GPU build, remain under NVIDIA's terms.

This inventory records the native dependencies inspected during the **0.1.0-beta.1** release preparation. The [actual Mac native inventory](validation/mac-native-dependencies.json) separately records 79 installed formula versions and source pins, 102 original/relocated media-binary hash checks, and 24 PyAV native-dylib hash checks. Source collection for that inventory is distinct from the Windows collection. A dependency's project-level license alone does not describe every library inside its binary wheel.

## Where to find notices

The desktop package provides **Help → Bundled Licenses**. Its backend notice directory is `resources/backend/_internal/licenses`; Electron's notices also accompany the Electron runtime. Preserve complete upstream license and notice files, including embedded third-party sections. This document is an index, not a substitute for those files.

| Component | Inspected version | License and notice material to retain |
| --- | --- | --- |
| CPython | 3.13 runtime; exact patch version recorded per build | Full CPython `LICENSE.txt`: PSF license and the included historical and third-party notices. [Python license](https://docs.python.org/3.13/license.html). |
| PyTorch | `2.11.0+cu126` on Windows | Full wheel `LICENSE` and `NOTICE`, not just the top-level BSD summary. These files contain notices for vendored components. [PyTorch source and license](https://github.com/pytorch/pytorch/tree/v2.11.0). |
| Intel oneMKL / oneDNN / MAGMA | Actual Windows Torch configuration: oneMKL 2025.3, oneDNN 3.10.2, MAGMA 2.5.4 | oneMKL's Intel Simplified Software License and complete versioned third-party notices are included separately; oneDNN's Apache-2.0 notices remain in Torch's aggregate; MAGMA's BSD-3-Clause copyright is included separately. |
| NVIDIA CUDA / cuDNN | CUDA 12.6; runtime cuDNN version `91002` (9.10.2) | Applicable CUDA and cuDNN EULAs, redistributable notices, and any separate component terms. These are proprietary redistributables, not relicensed application source. See the NVIDIA section below. |
| PyAV | 17.0.0 | Wrapper `LICENSE.txt` (BSD-3-Clause) and `AUTHORS.rst`; separately account for every bundled FFmpeg/codec/runtime library. [PyAV v17 source](https://github.com/PyAV-Org/PyAV/tree/v17.0.0). |
| FFmpeg / FFprobe and Windows PyAV native libraries | BtbN `n8.0.1-66-g27b8d1a017`, LGPL shared build | LGPL-3.0-or-later native FFmpeg, matching source/build recipes, and external-library notices. Windows packaging replaces the official PyAV wheel's bundled native libraries with this separately verified dependency set. |
| NumPy | 2.2.1 | Full wheel `LICENSE.txt`, including its bundled numerical-library and runtime notices. [NumPy source](https://github.com/numpy/numpy/tree/v2.2.1). |
| Pillow | 11.3.0 | Full wheel `LICENSE`, including bundled imaging-library notices. Pillow's own license is MIT-CMU. [Pillow source and licenses](https://github.com/python-pillow/Pillow/tree/11.3.0). |
| FastAPI / Starlette / Uvicorn | 0.139.0 / 0.52.1 / 0.40.0 | FastAPI MIT and Starlette/Uvicorn BSD-3-Clause license files; preserve their dependencies' notices as well. |
| HTTPX / python-multipart | 0.28.1 / 0.0.22 | HTTPX BSD-3-Clause and python-multipart Apache-2.0 notices. |
| React / React DOM | 19.3.1 | Both MIT `LICENSE` files from the installed packages, although their code is bundled into frontend JavaScript. [React source](https://github.com/facebook/react). |
| Lucide React | 1.66.0 | Entire package `LICENSE`: Lucide ISC terms **and** MIT terms for Feather-derived icons. [Lucide license](https://github.com/lucide-icons/lucide/blob/main/LICENSE). |
| Electron | 45.2.1 selected by desktop lockfile | Electron's MIT license and the distribution's full `LICENSES.chromium.html`, covering Chromium, Node.js, and other embedded components. [Electron license](https://github.com/electron/electron/blob/v45.2.1/LICENSE). |
| PyInstaller | 6.22.3 build tool / bundled bootloader | Full `COPYING.txt`, including the bootloader exception. Its exception permits distributing generated applications under other licenses. [PyInstaller license and exception](https://pyinstaller.org/en/stable/license.html). |

The generated Python distribution inventory and native-binary hashes identify the exact build. Transitive packages and bundled C/C++ libraries retain their own notices even when omitted from this summary. Build-only Node packages are governed by their package licenses; the frontend and desktop lockfiles record their versions.

## Windows FFmpeg: pinned LGPL shared distribution

Windows packaging selects the [BtbN February 28, 2026 release](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-02-28-12-59), not the development machine's older Gyan/Chocolatey binaries. The pinned archive is:

```text
ffmpeg-n8.0.1-66-g27b8d1a017-win64-lgpl-shared-8.0.zip
Bytes: 83378279
SHA256: e7b1087c310cf8b91f5467b8ada6d7e47ce26f2777efa2317c7cc271087e5100
```

The source revisions are [FFmpeg `27b8d1a017de62b51c0f3a039327b5cc2d7ea13f`](https://github.com/FFmpeg/FFmpeg/commit/27b8d1a017de62b51c0f3a039327b5cc2d7ea13f) and [BtbN build recipes `c482a075076c29edf6dd9c5a7c9d3113d0babed6`](https://github.com/BtbN/FFmpeg-Builds/tree/c482a075076c29edf6dd9c5a7c9d3113d0babed6). The recipes preserve exact library revisions, patches, toolchain setup, and compile/install commands.

The vendor configuration enables shared libraries and reports LGPL version 3, and explicitly disables libx264/libx265. Source inspection additionally found **GPL FFTW3 through Chromaprint**, and source/disassembly inspection found retained **GPL-2.0-only libzvbi packet-830 code**. The vendor's LGPL filename/runtime string therefore does not establish an LGPL-only dependency set. The affected avformat/avcodec libraries are replaced by exact-revision LLVM/MinGW **UCRT** source builds without those components. The corrected pair passed actual file I/O and 51 codec/catalog/API regressions; frozen GPU checks remain a separate release requirement. Keep the DLLs replaceable and retain the wrapper-import patch script and native build instructions with the application source.

Some external codec/filter libraries are statically incorporated inside these shared FFmpeg DLLs. Their notices and any applicable source obligations still matter. Follow [FFmpeg's distribution checklist](https://ffmpeg.org/legal.html); do not substitute a current upstream tarball for the pinned source revision.

## PyAV wheels: inspect the libraries, not just the wrapper

PyAV 17.0.0's official build workflow points to the [pyav-ffmpeg `8.0.1-5` release](https://github.com/PyAV-Org/pyav-ffmpeg/releases/tag/8.0.1-5). Its [pinned build recipe](https://github.com/PyAV-Org/pyav-ffmpeg/blob/8.0.1-5/scripts/build-ffmpeg.py) contains source archive URLs and SHA-256 hashes; its repository also contains the build helper, workflow, and patches. Keep those files together with the actual source archives, not only hyperlinks to them.

The inspected Windows wheel contains these FFmpeg library versions:

```text
libavutil     60.8.100       libavcodec    62.11.100
libavformat   62.3.100       libavdevice   62.1.100
libavfilter   11.4.100       libswscale    9.1.100
libswresample 6.1.100
```

Their runtime license strings say LGPL version 3 or later. However, the vendor recipe enables **libx264 and libx265**, and its [FFmpeg patch](https://github.com/PyAV-Org/pyav-ffmpeg/blob/8.0.1-5/patches/ffmpeg.patch) moves those libraries out of FFmpeg's GPL configuration list. A changed configuration label does not grant different rights to the encoder code. FFmpeg's own [external-library documentation](https://ffmpeg.org/general.html#x264) describes GPL requirements for those encoders. Treat this dependency combination as requiring GPL accounting; do not advertise the unmodified official wheel as a verified LGPL-only build.

The Windows packaging path replaces those native dependencies with a reviewed shared-library set derived from the pinned BtbN build and targeted source rebuilds, rewrites only the wrapper's native import names in an isolated copy, and checks imported symbols and actual loaded library identities. The installed global PyAV package remains unchanged. Include the PyAV source and reproducible import-name patch script with the release sources. An exception granted by this application's copyright holder cannot change the rights of third-party GPL encoder authors.

The pinned vendor source inputs include FFmpeg 8.0.1:

```text
Source: https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.xz
SHA256: 05ee0b03119b45c0bdb4df654b96802e909e0a752f72e4fe3794f487229e5a41
```

This upstream FFmpeg archive alone is not all corresponding source for the vendor's linked codec libraries. The release assets contain binary archives; GitHub's automatically generated repository archive contains recipes and patches, not those downloaded dependencies.

## PyTorch CPU/GPU runtime terms and the application license

The inspected Windows PyTorch wheel reports CUDA **12.6** and cuDNN **9.10.2**. It contains CUDA runtime, BLAS, FFT, random-number, sparse/solver, runtime-compiler/JIT, cuDNN, and profiling-related DLLs. The complete wheel `LICENSE`/`NOTICE` must remain intact, but they do not substitute for the separate proprietary runtime terms.

Use the [CUDA 12.6 EULA and redistributable attachment](https://docs.nvidia.com/cuda/archive/12.6.0/eula/index.html) and [cuDNN 9.10.2 EULA](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.10.2/reference/eula.html). The [cuDNN redistributable manifest](https://developer.download.nvidia.com/compute/cudnn/redist/redistrib_9.10.2.json) records official archive identity. Preserve applicable third-party notices and map each shipped DLL to its distribution terms. Windows packaging excludes the unused `nvperf_host.dll` after an import/delay-import audit found no importers; it is not assumed to be covered by the CUDA runtime redistribution entry.

The actual Windows Torch configuration also reports **Intel oneMKL 2025.3** statically incorporated into `torch_cpu.dll`. Its [official versioned license package](https://pypi.org/project/onemkl-license/2025.3.0/) supplies the Intel Simplified Software License (October 2022) and eight complete third-party notice files. Original bytes, wheel provenance and checksums are preserved under `packaging/notices/intel-onemkl-2025.3`. This proprietary math runtime is distinct from the Apache-licensed oneDNN code in Torch's aggregate notices. [Intel's license FAQ](https://www.intel.com/content/www/us/en/developer/articles/tool/onemkl-license-faq.html) identifies the applicable redistribution terms. MAGMA 2.5.4's [official BSD copyright](https://github.com/icl-utk-edu/magma/blob/v2.5.4/COPYRIGHT) is retained separately as well.

The application's [GPL-3.0-or-later license](../LICENSE) and [scoped PyTorch runtime permission](../LICENSE-EXCEPTION) permit the specified CPU/GPU runtime combination for application code owned by Taylor Kalin. GNU documents an [additional permission under GPLv3 section 7](https://www.gnu.org/licenses/gpl-faq.en.html#GPLIncompatibleLibs) for this situation. The permission does not relicense NVIDIA or Intel software or extend to unrelated third-party GPL code.

LGPLv3 dependencies also require a compatible application license; [GNU's license list](https://www.gnu.org/licenses/license-list.en.html#LGPLv3) identifies the GPL-2.0-only incompatibility. Never infer that the entire installer has one license from its top-level application metadata.

## Corresponding-source release assets

For a downloadable binary release, publish source beside the installer with equal access, using the applicable [GPLv3 section 6](https://www.gnu.org/licenses/gpl-3.0.html#section6) / [LGPLv3](https://www.gnu.org/licenses/lgpl-3.0.html) terms. The release should carry a versioned source archive and SHA-256 manifest containing:

1. The exact application revision, frontend/desktop source, lockfiles, packaging scripts, and build instructions used for that installer.
2. Exact FFmpeg source for both the subprocess tools and in-process libraries; all required linked dependency sources; patches; configuration; and scripts controlling compilation and installation.
3. PyAV source for the wrapper and the actual native-library build recipes. Include downloaded source inputs rather than depending on mutable upstream availability.
4. Complete license/notice texts and a machine-readable inventory mapping shipped binaries to version, source input, build recipe, and checksum. For LGPL shared libraries, preserve the ability to replace them with compatible modified versions as required by the chosen compliance route.

Do not promise a written source offer without actually operating it. A project homepage, a current upstream tarball, or a vendor binary checksum alone does not establish complete corresponding source for a different binary. GNU's [redistributed-binary FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#UnchangedJustBinary) explains that unchanged downloaded GPL binaries still carry source-distribution obligations.

The [source collector](../scripts/collect_release_sources.py) downloads inputs serially, verifies pinned source identities/checksums, preserves upstream notices, snapshots actual public application source, and creates a reproducible-metadata archive with `SOURCE-MANIFEST.json` and `SHA256SUMS`. Its offline fixture tests cover unsafe paths, cache tampering and archive reproducibility. It never executes downloaded build scripts. A collection with unresolved required inputs is explicitly marked incomplete and exits unsuccessfully.

```bash
python scripts/collect_release_sources.py --self-test
python scripts/collect_release_sources.py --collect \
  --output out/release-sources/windows-release \
  --application-revision RELEASE_COMMIT \
  --native-config build/vendor-lgpl/ffmpeg-buildconf.txt \
  --native-override-receipt build/vendor-lgpl/clean-ffmpeg/avformat-62.dll.receipt.json \
  --native-override-receipt build/vendor-lgpl/clean-ffmpeg/avcodec-62.dll.receipt.json
```

The Windows collection includes actual source archives for the pinned FFmpeg, PyAV, build recipes, LGPL/MPL dependency inputs, libiconv's pinned gnulib, libplacebo's six pinned submodules, and the targeted rebuild's OpenH264/NVIDIA headers/dav1d/SVT-AV1/zlib inputs. LAME is exported from exact SVN revision 6531 with executable properties and per-file hashes. Other recipe inputs retain source-URL/revision provenance. The release must refresh its application snapshot after source freeze, carry the final component notices, and verify that its manifest describes the installer actually published. Build receipts describe source/binary identity; they do not replace real file I/O, codec and frozen GPU tests. macOS's native FFmpeg/Homebrew sources require a separate platform-specific collection.

The native notice collection additionally preserves pinned component copyright/license files and NVIDIA's public codec-header grants. GCC/MinGW runtime notices include GPLv3 with the **GCC Runtime Library Exception 3.1**, plus the original compiler/runtime package copyright files. Those exceptions are third-party terms and are not replaced by the application's own permission.

## Showcase artwork

The Taipei skyline demonstration derives from **etonline's** [Taipei Skyline at Night 20210710](https://commons.wikimedia.org/wiki/File:Taipei_Skyline_at_Night_20210710.jpg), licensed under [CC BY-SA 2.0](https://creativecommons.org/licenses/by-sa/2.0/). Demo modifications include cropping, downsampling, grading, lens effects, and titles. Photo-derived showcase media remains under CC BY-SA 2.0; the photograph is not part of the application's source-code license. The original is a JPEG photograph, not RAW motion footage or a 16-bit camera capture.
