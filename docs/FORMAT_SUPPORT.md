# Formats, samples and color

The app's **Format support** panel and `GET /api/formats` report the installed
catalogue. A decoder or encoder being installed is not proof that every codec
variant, container, raster, frame rate or hardware combination will work. The
actual source is inspected on import; exports are encoded and independently
decoded before publication. [FFmpeg's format documentation](https://ffmpeg.org/general.html)
describes its broader library support, which is larger than this editor's tested scope.

## Import families

| Family | Catalogue examples |
| --- | --- |
| Everyday video | MOV/MP4/M4V, Matroska/WebM, AVI, NUT; H.264/AVC, HEVC, VP8/9, AV1, FFV1, MJPEG, uncompressed video |
| Broadcast containers | MXF, GXF, MPEG program/transport streams, MTS/M2TS; supported MPEG-2, AVC-Intra/XDCAM and other installed codec variants |
| Film/interchange | ProRes, DNxHD/DNxHR, CineForm, JPEG 2000/MJ2 |
| Stills and sequences | DPX, EXR, 8/16-bit TIFF/PNG, JPEG/JPEG-LS/JPEG XL, BMP, TGA and WebP, according to installed decoders |
| Audio | WAV/BWF/RF64/Wave64, AIFF, FLAC, ALAC/M4A, MP3, AAC, Opus and Vorbis/Ogg |

An import copies source bytes into a private UUID folder and records SHA-256.
The browser receives display proxies/posters and real decoded waveform peaks,
not source filesystem paths. A sequence stores numbered copies in explicit
upload order, an explicit rational frame rate and a private frame manifest.
Current bounds are 64 GiB per import/sequence and 10,000 sequence frames.

### SDK and package boundaries

BRAW, RED R3D, ARRIRAW, Canon Cinema RAW and ProRes RAW are not made supported
by ordinary ProRes/rawvideo decoding. Vendor SDK adapters are not implemented;
the catalogue reports those paths as unavailable. For example, Blackmagic
publishes its own [Blackmagic RAW SDK](https://www.blackmagicdesign.com/developer/products/braw/sdk-and-software).
Import a supported interchange render instead.

DCP/IMF package assembly, CPL interpretation, encrypted assets and mastering
are not implemented. An individual supported MXF/JPEG 2000 asset is different
from opening a complete DCP or IMF package. Playlist/script/network demuxers
are refused; uploads cannot nominate arbitrary filesystem paths or URLs.

## Export contracts

### Original packet copy

This release accepts one unchanged whole video source with matching project
dimensions/rate. It copies the file bytes and preserves its original compressed
video/audio. Edits, fades, mixing, grades, transforms, speed changes, gaps,
overlap and partial/non-keyframe cuts are rejected. Multi-clip concatenation is
not offered as a verified lossless shortcut in this release.

### Lossless RGB16 master

The default is FFV1 in Matroska with float32 PCM timeline audio. The renderer
generates 16-bit integer RGB samples; an independent decode must recover their
SHA-256 exactly. FFV1 itself is a lossless intraframe format, documented in
[RFC 9043](https://www.rfc-editor.org/rfc/rfc9043.html). The app additionally
checks frame count, geometry, cadence and audio.

“RGB16 lossless” refers to those processed samples. Source decoding, color
conversion, grading, transforms and the conversion to a finite 16-bit integer
range can change information before encoding. It does not claim identical
source packets, original camera RAW data or arbitrary floating-point precision.
Uncompressed RGB16 and reversible JPEG 2000 are separate catalogue profiles;
actual installed support and verification still apply.

### NVIDIA HEVC codec-lossless

This profile probes NVENC at the requested geometry and pixel format. The
target is 10-bit YUV 4:2:0; independently decoded active YUV samples are checked.
RGB→YUV/chroma conversion is outside the codec-lossless claim. A driver, GPU
name or installed `hevc_nvenc` encoder alone is insufficient. NVIDIA's
[NVENC capability tables](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/index.html)
describe generation-dependent HEVC/lossless support; this app still requires
a successful actual encode and verification.

### Delivery, intermediate and broadcast profiles

The catalogue includes H.264/HEVC/AV1 delivery, ProRes variants, DNxHD/DNxHR,
MXF, CineForm and image-sequence profiles. Availability is dynamic. ProRes,
DNxHR and ordinary delivery modes are not labeled mathematically lossless.
DNxHD and MPEG-2 broadcast MXF have explicit HD raster/rate restrictions and
are not 8K export profiles. ProRes 4444/XQ uses opaque `yuv444p10le` here; it is
not a 12-bit/alpha-preserving compositor or export path.

Supported HEVC/H.264 delivery or previews may use NVENC or Apple's VideoToolbox
after probing, otherwise reported software encoders are used. VideoToolbox
delivery is not codec-lossless. Broadcast/intermediate audio commonly uses
24-bit PCM for compatibility; delivery may use AAC or 24-bit FLAC. The output
report describes the selected conversion.

### Image sequence exports

PNG16, TIFF16, DPX16 and linear float EXR profiles produce ZIP archives with
numbered rendered frames, `mix.wav` and `timeline.json`. Integer RGB profiles
and EXR float have different sample contracts. An EXR archive is not a camera
RAW decoder or a promise that graded pixels equal the original EXR pixels.

## Timeline, audio and color limits

Uncompressed NUT exports currently support SDR; this build cannot retain its
HDR colorimetry fields. Use the RGB16 Matroska master for embedded HDR tags.

- Projects support 16..8192 pixels per axis, up to 35,651,584 pixels, rational
  rates from 1..120 fps and timelines up to one day. UHD 8K and DCI 8192×4320
  fit this envelope. Unsupported codec-specific geometry fails explicitly.
- Rendered timelines are project-CFR. VFR sources are sampled to that cadence;
  the editor does not claim to preserve every original VFR timestamp after edits.
- Project audio supports 44.1/48/96 kHz and mono/stereo/5.1/7.1. Clips currently
  use the first source audio stream. Mixing, channel conversion, gain, fades
  and resampling change samples. Reverse audio is bounded to 60 source seconds
  per clip; mute it or split long reversed clips.
- BT.709, BT.2020 PQ and BT.2020 HLG are project spaces. Input interpretation
  includes linear/sRGB/BT.709/PQ/HLG, supported primaries and a reference-white
  control. Mixed sources can therefore undergo transfer/primary conversion.
- Float EXR values above 1 are retained through source decoding and into
  grading; unknown primaries require an explicit source or clip interpretation.
  ACEScg support is a primaries matrix with chromatic adaptation, not a full
  OCIO/ACES display, view or camera-IDT pipeline.
- Browser JPEG/playback assets are SDR viewing approximations. HDR/linear
  viewing tone maps do not overwrite the source. The regular Homebrew FFmpeg
  build can omit `zscale`; use [ffmpeg-full](https://formulae.brew.sh/formula/ffmpeg-full).
- The compositor produces opaque RGB. Alpha-channel preservation, interlaced
  mastering, calibrated Dolby Vision/HDR10+ metadata rebuilding and camera RAW
  development are not implemented. Interlaced sources require preprocessing.

Lens streaks, halation, ghosts and focus-band bokeh are artistic effects. Bokeh
does not estimate scene depth or reconstruct obscured detail. No export option
turns a lower-resolution source into additional captured detail.
