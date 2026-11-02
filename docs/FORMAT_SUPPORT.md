# A format for every stage of the edit

**Cinema frames. Editing intermediates. Broadcast containers. Delivery files.**

8K Lossless Video Editor brings those choices into one local timeline. Its
catalogue has **24 explicit export profiles**, with installed decoder/encoder
availability shown in **Format support** and `GET /api/formats`. The tables
below describe the current implementation; actual file variants and requested
geometry are checked during import/export.

## The cinema-master workflow

Create a UHD 8K **7680×4320** or DCI 8K **8192×4320** project, cut and composite
the picture, set the source interpretation, finish the color, and export a
**DPX16**, **EXR float**, **PNG16** or **TIFF16** frame sequence. Each archive
contains numbered frames, `mix.wav` and `timeline.json`.

For a single-file master, choose **RGB16 FFV1/Matroska** or reversible
**JPEG 2000 RGB16/MOV**. For editorial interchange, choose the appropriate
ProRes, DNxHR or CineForm profile. For viewing/delivery, choose an explicitly
labeled H.264, HEVC or AV1 encode.

### All 24 export profiles

| # | Profile ID | Output | Picture contract | Audio |
| ---: | --- | --- | --- | --- |
| 1 | `lossless` | MKV / FFV1 | Verified processed RGB16 integer master | Float32 PCM |
| 2 | `nvenc-lossless` | MKV / HEVC NVENC | Verified target 10-bit 4:2:0 YUV; actual hardware probe | Float32 PCM |
| 3 | `copy` | Original container | One unchanged whole source; byte-identical copy | Original audio |
| 4 | `hevc` | MP4 / H.265 | High-quality 10-bit 4:2:0 delivery | AAC |
| 5 | `h264` | MP4 / AVC | High-quality 8-bit 4:2:0 delivery | AAC |
| 6 | `av1` | MKV / AV1 | High-quality 10-bit 4:2:0 delivery | 24-bit FLAC |
| 7 | `prores` | MOV / ProRes 422 HQ | 10-bit 4:2:2 editing intermediate | 24-bit PCM |
| 8 | `prores-proxy` | MOV / ProRes Proxy | 10-bit 4:2:2 editing intermediate | 24-bit PCM |
| 9 | `prores-lt` | MOV / ProRes 422 LT | 10-bit 4:2:2 editing intermediate | 24-bit PCM |
| 10 | `prores-422` | MOV / ProRes 422 | 10-bit 4:2:2 editing intermediate | 24-bit PCM |
| 11 | `prores-4444` | MOV / ProRes 4444 | Opaque 10-bit 4:4:4 in this implementation | 24-bit PCM |
| 12 | `prores-xq` | MOV / ProRes 4444 XQ | Opaque 10-bit 4:4:4 in this implementation | 24-bit PCM |
| 13 | `dnxhr` | MOV / DNxHR HQX | 10-bit 4:2:2 editing intermediate | 24-bit PCM |
| 14 | `dnxhr-444` | MOV / DNxHR 444 | 10-bit RGB editing intermediate | 24-bit PCM |
| 15 | `dnxhd` | MOV / DNxHD | Explicit 1080p broadcast raster/rate restrictions | 24-bit PCM |
| 16 | `mxf` | MXF OP1a / DNxHR HQX | 10-bit 4:2:2 interchange | 24-bit PCM |
| 17 | `mxf-mpeg2` | MXF / MPEG-2 | 4:2:2, 50 Mb/s, supported HD rasters | 24-bit PCM |
| 18 | `cineform` | MOV / CineForm | 12-bit RGB editing intermediate | 24-bit PCM |
| 19 | `jpeg2000` | MOV / reversible JPEG 2000 | Verified processed RGB16 | Float32 PCM |
| 20 | `uncompressed` | NUT / rawvideo | Uncompressed RGB16; SDR in this build | Float32 PCM |
| 21 | `png-sequence` | ZIP / PNG frames | Verified 16-bit RGB sequence | Float32 PCM WAV |
| 22 | `tiff-sequence` | ZIP / TIFF frames | Verified 16-bit RGB sequence | Float32 PCM WAV |
| 23 | `dpx-sequence` | ZIP / DPX frames | Verified 16-bit RGB sequence | Float32 PCM WAV |
| 24 | `exr-sequence` | ZIP / EXR frames | Rendered linear float32 sequence; explicit interpretation | Float32 PCM WAV |

All available profiles are selectable from the same export drawer. An absent
encoder is reported as unavailable. An installed hardware encoder still has
to open and encode the requested geometry; a GPU label is not the probe.
DNxHD and MPEG-2 broadcast MXF are HD-specific profiles, not 8K export options.
ProRes, DNxHR, CineForm and ordinary delivery profiles are not labeled
mathematically lossless.

## Bring your source material

### Cinema, editorial and interchange

| Source family | Containers / files |
| --- | --- |
| ProRes Proxy, LT, 422, HQ, 4444 and XQ | Supported MOV/MXF variants |
| DNxHD and DNxHR | Supported MOV/MXF variants |
| GoPro CineForm | MOV / AVI |
| JPEG 2000 | MOV / MXF / MJ2 / J2K / JP2 |
| FFV1 and uncompressed RGB | Matroska / NUT / supported AVI variants |

### Everyday and broadcast video

| Family | Formats and codec families |
| --- | --- |
| QuickTime / MPEG-4 | MOV, MP4, M4V, 3GP; installed H.264/HEVC and other supported variants |
| Matroska / WebM | MKV, WebM; H.264, HEVC, VP8, VP9, AV1, FFV1 |
| AVI | MPEG-4, MJPEG and uncompressed variants |
| MPEG transport / program | TS, MTS, M2TS, M2T, MPG, MPEG, VOB; MPEG-2/H.264/HEVC variants |
| Broadcast interchange | MXF OP1a/OP-Atom, GXF; installed DNxHD/DNxHR, ProRes, MPEG-2, AVC-Intra/XDCAM, JPEG 2000 and DV variants |
| Other everyday containers | WMV, ASF, FLV with installed WMV/VC-1/FLV/MPEG decoders |

Container names do not guarantee every compression variant is supported.
The editor uses the actual installed FFmpeg/PyAV decoders. The
[upstream FFmpeg format documentation](https://ffmpeg.org/general.html)
describes its broader library support, not a blanket promise for this app.

### Stills and image sequences

**DPX 10/12/16-bit · OpenEXR float · TIFF 8/16-bit · PNG 8/16-bit · JPEG ·
JPEG-LS · JPEG XL · BMP · TGA · WebP**

DPX/EXR/high-bit-depth images use native FFmpeg/PyAV inspection and decoding.
The original image is retained; the 8-bit JPEG poster is only a viewing asset.
Float EXR values above 1 survive decoding into grading. Source/clip
interpretation supplies known transfer and primaries when those are missing.

Sequence imports preserve explicit upload order at a rational frame rate.
The UI sends natural filename order. Current bounds are **10,000 frames** and
**64 GiB** per import/sequence. The private manifest records each owned frame;
the browser cannot submit arbitrary server paths or a wildcard pattern.

### Audio

**WAV · BWF · RF64 · Wave64 · AIFF · FLAC · ALAC/M4A · MP3 · AAC · Opus · Ogg/Vorbis**

Project sample rates are **44.1, 48 and 96 kHz**; layouts are **mono, stereo,
5.1 and 7.1**. The first source audio stream of each clip contributes to the
mix. Gain, clip-local volume keyframes, fades, overlaps, timing, speed and
channel conversion are applied to the rendered program. Browser waveforms
come from real decoded peaks.

Float32 PCM master audio preserves the processed mix without forcing a
24-bit integer intermediate. Mezzanine/broadcast profiles use 24-bit PCM where
specified; delivery AAC and 24-bit FLAC follow the table above. None of those
mixing or conversion choices imply unchanged original audio packets.

## Three distinct promises of preservation

| What you need | Choose | What is verified |
| --- | --- | --- |
| Keep the original file exactly | Original packet copy | One unchanged whole video source, copied byte-for-byte |
| Keep the processed RGB picture exactly | RGB16 master or another verified RGB16 profile | Independently decoded integer RGB samples match the rendered samples |
| Keep the chosen hardware-codec samples exactly | NVIDIA HEVC codec-lossless | Independently decoded active target YUV10 samples match |

Source imports are copied and hashed; editing does not overwrite those bytes.
Rendering color, compositing, transforms, transfer conversion and audio mixing
changes samples before encoding. RGB→YUV conversion and chroma subsampling
are outside the NVENC codec-lossless claim. RGB16 is integer data, not arbitrary
float EXR precision.

FFV1's lossless intraframe format is specified in
[RFC 9043](https://www.rfc-editor.org/rfc/rfc9043.html). NVIDIA documents its
generation-dependent HEVC/lossless capabilities in the
[NVENC application note](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/index.html).
The editor also checks its own actual outputs; codec specifications alone do
not validate a render.

## Compact compatibility notes

- **SDK / package paths:** BRAW, RED R3D, ARRIRAW, Canon Cinema RAW and ProRes
  RAW are not implemented through the ordinary ProRes/rawvideo paths. They
  need the relevant SDK/decoder integration. Blackmagic, for example, publishes
  its own [RAW SDK](https://www.blackmagicdesign.com/developer/products/braw/sdk-and-software).
  DCP/IMF CPL/package assembly, encryption and mastering are not implemented;
  supported individual MXF/JPEG 2000 assets remain separate import paths.
- **Original copy:** one whole unchanged source only. Edits, partial/non-keyframe
  cuts, transforms, grades, mixing, fades, gaps and multi-clip concatenation
  are rejected by this mode.
- **Raster and timing:** projects support 16..8192 pixels per axis, up to
  35,651,584 pixels, rational 1..120 fps and up to one day. The output is
  project-CFR; VFR sources are sampled onto it. Each codec has its own additional
  restrictions. Uncompressed NUT is SDR-only here; use an RGB16 Matroska master
  for embedded HDR colorimetry.
- **Color:** project spaces are BT.709, BT.2020 PQ and BT.2020 HLG. Input transfer,
  primaries and reference white can be explicit. ACEScg support uses a primaries
  matrix and chromatic adaptation; it is not a complete OCIO/ACES display/view/IDT
  pipeline. Dolby Vision/HDR10+ mastering metadata is not rebuilt after effects.
- **Alpha / interlace:** the compositor produces opaque RGB. ProRes 4444/XQ
  currently outputs 10-bit 4:4:4 without alpha preservation. Interlaced sources
  require preprocessing; interlaced mastering is not implemented.
- **Hardware:** CUDA, Metal/MPS and CPU are available rendering paths, with
  processing split across CPU and accelerator as needed. VideoToolbox delivery
  is not labeled lossless. Installed codecs and short 8K proofs do not establish
  real-time playback or sustained performance.
- **Viewing:** posters and playback proxies are SDR viewing approximations,
  separate from original sources and final masters. HDR viewing needs `zscale`
  and `tonemap`; on Mac use [ffmpeg-full](https://formulae.brew.sh/formula/ffmpeg-full).
  Lens effects are artistic approximations; oval focus falloff does not estimate
  scene depth. Reverse audio is bounded to 60 source seconds per clip.

[Return to the product overview](../README.md) · [Validation and hardware evidence](VALIDATION.md)
