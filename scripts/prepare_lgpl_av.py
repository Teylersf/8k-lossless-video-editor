"""Prepare an isolated, pinned LGPL FFmpeg runtime for PyAV 17 on Windows.

GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION. The PyAV wrapper and
FFmpeg libraries retain their separate licenses. Never changes installed av.
"""
from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "build/vendor-lgpl"
NAME = "ffmpeg-n8.0.1-66-g27b8d1a017-win64-lgpl-shared-8.0"
URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-02-28-12-59/" + NAME + ".zip"
SHA256 = "e7b1087c310cf8b91f5467b8ada6d7e47ce26f2777efa2317c7cc271087e5100"
SIZE = 83_378_279
SOURCE_SHA256 = '87a695bc61e16f4aa8437c2ab6c0bbe1ca572ceccec4a2e86013e36f49899b1e'
DLLS = {"avcodec": 62, "avdevice": 62, "avfilter": 11, "avformat": 62,
        "avutil": 60, "swresample": 6, "swscale": 9}
VERSIONS = {"libavcodec": [62, 11, 100], "libavdevice": [62, 1, 100],
            "libavfilter": [11, 4, 100], "libavformat": [62, 3, 101],
            "libavutil": [60, 8, 100], "libswresample": [6, 1, 100], "libswscale": [9, 1, 100]}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while data := stream.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def checked_archive(path):
    if path.stat().st_size != SIZE or digest(path) != SHA256:
        raise ValueError("Pinned FFmpeg archive size/SHA256 mismatch")


def write_private_record(site):
    record = site / 'av-17.0.0.dist-info/RECORD'
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    for path in sorted(site.rglob('*')):
        if path.is_file() and path != record:
            raw = path.read_bytes()
            value = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode('ascii')
            writer.writerow([path.relative_to(site).as_posix(), 'sha256=' + value, len(raw)])
    writer.writerow([record.relative_to(site).as_posix(), '', ''])
    record.write_text(output.getvalue(), encoding='utf-8', newline='')


def run(args, **kwargs):
    result = subprocess.run([str(a) for a in args], check=False, capture_output=True,
                            timeout=kwargs.pop("timeout", 120), text=True, **kwargs)
    if result.returncode:
        raise RuntimeError("Runtime check failed: " + result.stderr[-6000:])
    return result.stdout


def runtime_check(site):
    """Runs only in a fresh process with the private directory first on PATH."""
    import av
    site = Path(site).resolve(strict=True)
    if Path(av.__file__).resolve().parent != site / "av" or av.__version__ != "17.0.0":
        raise ValueError("PyAV did not load exclusively from the private wrapper")
    actual = {key: list(value) for key, value in av.library_versions.items()}
    if actual != VERSIONS:
        raise ValueError("Unexpected native library ABI: " + repr(actual))
    handles = []
    libraries = {}
    for name, major in DLLS.items():
        path = site / "av.libs" / f"{name}-{major}.dll"
        lib = ctypes.CDLL(str(path))
        handles.append(lib)
        fields = {}
        for suffix in ("license", "configuration"):
            function = getattr(lib, name + "_" + suffix)
            function.restype = ctypes.c_char_p
            fields[suffix] = function().decode("utf-8")
        if "LGPL" not in fields["license"]:
            raise ValueError("Non-LGPL native library: " + str(path))
        config = fields["configuration"]
        if any(flag in config.split() for flag in ("--enable-gpl", "--enable-nonfree", "--enable-libx264", "--enable-libx265")):
            raise ValueError("Forbidden native configuration: " + str(path))
        if name == 'avcodec' and '--enable-libzvbi' in config.split():
            raise ValueError('Unmodified vendor avcodec embeds GPL ZVBI code; the audited clean rebuild is required')
        if name == 'avformat' and '--enable-chromaprint' in config.split():
            raise ValueError('Unmodified vendor avformat embeds GPL FFTW via Chromaprint; the audited clean rebuild is required')
        libraries[path.name] = dict(path=str(path), sha256=digest(path), **fields)
    for name in ("libx264", "libx265"):
        try:
            av.codec.Codec(name, "w")
        except (ValueError, av.error.FFmpegError):
            pass
        else:
            raise ValueError("Unexpected GPL encoder: " + name)
    for name in ("libopenh264", "ffv1", "hevc_nvenc", "prores_ks", "dnxhd", "dpx", "exr"):
        av.codec.Codec(name, "w")
    av.codec.Codec('libsvtav1', 'w')
    av.codec.Codec('libdav1d', 'r')
    # Imported symbols and version numbers do not prove compatible CRT file
    # descriptors. Exercise an actual file-backed encode/open/decode boundary.
    from fractions import Fraction
    with tempfile.TemporaryDirectory(prefix='editor-native-abi-') as folder:
        path = Path(folder) / 'probe.mkv'
        with av.open(str(path), 'w', format='matroska') as output:
            stream = output.add_stream('ffv1', rate=24)
            stream.width, stream.height, stream.pix_fmt = 16, 16, 'gbrp16le'
            stream.time_base = stream.codec_context.time_base = Fraction(1, 24)
            frame = av.VideoFrame(16, 16, 'gbrp16le')
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            frame.pts, frame.time_base = 0, Fraction(1, 24)
            for packet in list(stream.encode(frame)) + list(stream.encode()):
                output.mux(packet)
        with av.open(str(path)) as source:
            decoded = list(source.decode(video=0))
        if len(decoded) != 1 or (decoded[0].width, decoded[0].height) != (16, 16):
            raise ValueError('Native file-backed encode/decode ABI smoke failed')
    # Prove the Windows loader did not also bring in the original hashed DLLs.
    from ctypes import wintypes
    modules = (wintypes.HMODULE * 4096)()
    needed = wintypes.DWORD()
    kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    process = kernel.GetCurrentProcess()
    if not psapi.EnumProcessModules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed)) or needed.value > ctypes.sizeof(modules):
        raise OSError("Unable to audit loaded native modules")
    loaded = []
    for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
        buffer = ctypes.create_unicode_buffer(32768)
        if not psapi.GetModuleFileNameExW(process, module, buffer, len(buffer)):
            raise OSError("Unable to resolve native module")
        path = Path(buffer.value).resolve()
        if re.match(r"(?:avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)-", path.name, re.I):
            if path.parent != site / "av.libs" or path.name not in libraries:
                raise ValueError("Unexpected FFmpeg DLL loaded: " + str(path))
            loaded.append(str(path))
    if len(loaded) != len(DLLS):
        raise ValueError("Incomplete or duplicate native FFmpeg module set")
    return dict(av_version=av.__version__, av_path=av.__file__, versions=actual,
                libraries=libraries, loaded_modules=sorted(loaded))


def prepare(archive, verify_only=False, native_overrides=None):
    import pefile
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise ValueError("This pinned runtime is Windows x86-64 only")
    if not archive.exists():
        if verify_only:
            raise FileNotFoundError(archive)
        archive.parent.mkdir(parents=True, exist_ok=True)
        part = archive.with_suffix(".zip.partial")
        with urllib.request.urlopen(URL, timeout=60) as response, part.open("xb") as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        checked_archive(part)
        part.rename(archive)
    checked_archive(archive)
    overrides = Path(native_overrides or BASE / 'clean-ffmpeg').resolve(strict=True)
    rebuilt = json.loads((overrides / 'build-receipt.json').read_text(encoding='utf-8'))
    if rebuilt.get('source_archive_sha256') != SOURCE_SHA256 or rebuilt.get('source_commit') != '27b8d1a017de62b51c0f3a039327b5cc2d7ea13f':
        raise ValueError('Clean rebuild source pin does not match the audited FFmpeg source')
    if rebuilt.get('crt') != 'UCRT':
        raise ValueError('The replacement FFmpeg libraries must use UCRT consistently with the retained vendor libraries')
    native_components = {item['component']: item for item in rebuilt['components']}
    if set(native_components) != {'avcodec-62.dll', 'avformat-62.dll'}:
        raise ValueError('Exactly the audited avcodec/avformat replacement pair is required')
    for name, component in native_components.items():
        if digest(overrides / name) != component['output_sha256'] or (overrides / name).stat().st_size != component['output_bytes']:
            raise ValueError('Clean native override changed: ' + name)
    distribution = importlib.metadata.distribution("av")
    if distribution.version != "17.0.0":
        raise ValueError("Only the audited PyAV 17.0.0 wrapper is accepted")
    # A new generation avoids modifying a loaded or concurrently collected v1
    # prototype. The original BtbN archive/extraction remains unchanged.
    site = BASE / "site-packages-v2"
    receipt = BASE / "receipt-v2.json"
    if verify_only or site.exists():
        manifest = json.loads(receipt.read_text(encoding="utf-8"))
        for relative, expected in manifest["private_files"].items():
            if digest(site / relative) != expected:
                raise ValueError("Private runtime file changed: " + relative)
    else:
        extracted = BASE / "ffmpeg-dist"
        if not (extracted / NAME / "bin/ffmpeg.exe").is_file():
            with zipfile.ZipFile(archive) as zipped:
                for item in zipped.infolist():
                    target = (extracted / item.filename).resolve()
                    if not target.is_relative_to(extracted.resolve()) or item.external_attr >> 16 & 0o170000 == 0o120000:
                        raise ValueError("Unsafe archive member")
                zipped.extractall(extracted)
        binary_dir = extracted / NAME / "bin"
        av_source = Path(distribution.locate_file("av")).resolve()
        shutil.copytree(av_source, site / "av", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(Path(distribution._path), site / "av-17.0.0.dist-info", ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (site / "av.libs").mkdir()
        exports = {}
        for name, major in DLLS.items():
            dll_name = f"{name}-{major}.dll"
            source = overrides / dll_name if dll_name in native_components else binary_dir / dll_name
            pe = pefile.PE(str(source))
            if pe.FILE_HEADER.Machine != 0x8664:
                raise ValueError("Non-x64 native DLL")
            if dll_name in native_components and any(entry.dll.lower() == b'msvcrt.dll' for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])):
                raise ValueError('A clean override imports legacy MSVCRT instead of UCRT: ' + dll_name)
            exports[dll_name] = {s.name if s.name else s.ordinal for s in pe.DIRECTORY_ENTRY_EXPORT.symbols}
            pe.close()
            shutil.copy2(source, site / "av.libs" / dll_name)
        # Check the complete native closure as well as the wrapper imports.
        for path in (site / 'av.libs').glob('*.dll'):
            pe = pefile.PE(str(path))
            for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
                imported = entry.dll.decode('ascii').lower()
                if imported in exports:
                    for symbol in entry.imports:
                        key = symbol.name if symbol.name is not None else symbol.ordinal
                        if key not in exports[imported]:
                            raise ValueError(f'Missing native dependency symbol {key!r} in {imported} required by {path.name}')
            pe.close()
        patches = []
        for path in sorted((site / "av").rglob("*.pyd")):
            pe = pefile.PE(str(path))
            if pe.FILE_HEADER.Machine != 0x8664:
                raise ValueError("Non-x64 PyAV extension")
            contents = bytearray(path.read_bytes())
            original = digest(path)
            changes = []
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                old = entry.dll.decode("ascii")
                match = re.fullmatch(r"(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)-(\d+)-[a-f0-9]+\.dll", old)
                if not match:
                    continue
                name, major = match.groups()
                new = f"{name}-{major}.dll"
                if new not in exports:
                    raise ValueError("Unexpected imported FFmpeg ABI: " + old)
                for symbol in entry.imports:
                    key = symbol.name if symbol.name is not None else symbol.ordinal
                    if key not in exports[new]:
                        raise ValueError(f"Missing imported symbol {key!r} in {new}")
                offset = pe.get_offset_from_rva(entry.struct.Name)
                if contents[offset:offset + len(old) + 1] != old.encode() + b"\0":
                    raise ValueError("Import table storage mismatch")
                contents[offset:offset + len(old) + 1] = new.encode() + bytes(len(old) + 1 - len(new))
                changes.append(dict(old=old, new=new, imported_symbols=len(entry.imports)))
            pe.close()
            path.write_bytes(contents)
            patches.append(dict(file=str(path.relative_to(site)), original_sha256=original,
                                modified_sha256=digest(path), import_changes=changes))
        # Explicit modified-wrapper hashes replace the original wheel RECORD.
        write_private_record(site)
        # ffmpeg writes configuration to stderr; retain both streams.
        cli_dir = BASE / 'ffmpeg-runtime-v2/bin'
        cli_dir.mkdir(parents=True)
        for path in (site / 'av.libs').glob('*.dll'):
            shutil.copy2(path, cli_dir / path.name)
        for name in ('ffmpeg.exe', 'ffprobe.exe'):
            shutil.copy2(binary_dir / name, cli_dir / name)
        probe = subprocess.run([str(cli_dir / "ffmpeg.exe"), "-hide_banner", "-buildconf"], capture_output=True, check=True, text=True, timeout=30)
        (BASE / "ffmpeg-buildconf-v2.txt").write_text(probe.stdout + probe.stderr, encoding="utf-8")
        manifest = dict(schema_version=2, archive_url=URL, archive_sha256=SHA256, archive_bytes=SIZE,
                        ffmpeg_source="27b8d1a017", pyav_version="17.0.0", wrapper_source=str(av_source),
                        ffmpeg_bin=str(cli_dir.resolve()), patches=patches, native_overrides=rebuilt,
                        cli_files={p.name: digest(p) for p in sorted(cli_dir.iterdir()) if p.is_file()},
                        abi_note="avformat 62.3.100 -> 62.3.101 is a backward-compatible micro patch; all other versions match exactly; all imported symbols audited.",
                        abi_policy="https://github.com/FFmpeg/FFmpeg/blob/n8.0.1/libavutil/avutil.h#L46-L72",
                        private_files={str(p.relative_to(site)): digest(p) for p in sorted(site.rglob("*")) if p.is_file()})
        receipt.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if manifest.get('native_overrides') != rebuilt:
        raise ValueError('Private runtime was prepared with a different native override receipt')
    for name, expected in manifest['cli_files'].items():
        if digest(Path(manifest['ffmpeg_bin']) / name) != expected:
            raise ValueError('Private CLI runtime changed: ' + name)
    if not (site / 'av-17.0.0.dist-info/RECORD').is_file():
        write_private_record(site)
        manifest['private_files'] = {str(p.relative_to(site)): digest(p) for p in sorted(site.rglob('*')) if p.is_file()}
    environment = dict(os.environ, PYTHONPATH=str(site), PYTHONDONTWRITEBYTECODE="1")
    verified = json.loads(run([sys.executable, Path(__file__).resolve(), "--runtime-check", site], env=environment))
    manifest["runtime_verification"] = verified
    receipt.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name, component in native_components.items():
        library = verified['libraries'][name]
        public = {key: rebuilt[key] for key in ('source_commit', 'source_archive_sha256', 'compiler', 'assembler', 'crt', 'toolchain', 'configure_args', 'build_args', 'external_sources', 'omitted_external_libraries', 'recipe')}
        public.update(component= name, output_sha256=component['output_sha256'], output_bytes=component['output_bytes'],
                      library_license=library['license'], library_configuration=library['configuration'])
        (overrides / (name + '.receipt.json')).write_text(json.dumps(public, indent=2), encoding='utf-8')
    return dict(site_packages=str(site.resolve()), receipt=str(receipt.resolve()), ffmpeg_bin=manifest["ffmpeg_bin"], runtime_verification=verified)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=BASE / "downloads" / (NAME + ".zip"))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument('--native-overrides', type=Path, help='Audited clean avcodec/avformat pair and build-receipt.json')
    parser.add_argument("--runtime-check", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    result = runtime_check(args.runtime_check) if args.runtime_check else prepare(args.archive, args.verify_only, args.native_overrides)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
