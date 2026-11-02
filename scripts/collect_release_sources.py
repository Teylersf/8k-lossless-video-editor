"""Collect pinned third-party source inputs beside a source snapshot of the app.

GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION in the application root.
Downloads are serialized, never execute upstream build scripts, and are cached
with SHA-256 receipts. A partial collection is useful evidence, but is explicitly
marked incomplete and exits 2. Nothing is fetched unless --collect is supplied.
"""
from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "release-sources"
BTBN_REV = "c482a075076c29edf6dd9c5a7c9d3113d0babed6"
FFMPEG_REV = "27b8d1a017de62b51c0f3a039327b5cc2d7ea13f"
PYAV_VENDOR_REV = "8.0.1-5"
BTBN_BINARY = {
    "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-02-28-12-59/ffmpeg-n8.0.1-66-g27b8d1a017-win64-lgpl-shared-8.0.zip",
    "sha256": "e7b1087c310cf8b91f5467b8ada6d7e47ce26f2777efa2317c7cc271087e5100",
    "bytes": 83378279,
}
PYAV_SDIST = {
    "url": "https://files.pythonhosted.org/packages/b2/eb/abca886df3a091bc406feb5ff71b4c4f426beaae6b71b9697264ce8c7211/av-17.0.0.tar.gz",
    "sha256": "c53685df73775a8763c375c7b2d62a6cb149d992a26a4b098204da42ade8c3df",
}
REBUILD_INPUTS = (
    ("nv-codec-headers", "https://github.com/FFmpeg/nv-codec-headers", "876af32a202d0de83bd1d36fe74ee0f7fcf86b0d", "5e83af9e86efe7a1f4393ef342a299e5e7ca9f57fe210307afed3ae0f39d4a3f", "BSD-2-Clause"),
    ("openh264", "https://github.com/cisco/openh264", "cf568c83f71a18778f9a16e344effaf40c11b752", "5e5efc74a0011f9853ccc3b1efeae92105dd5c5bea4af2432c6a9cc50e468007", "BSD-2-Clause"),
    ("dav1d", "https://code.videolan.org/videolan/dav1d", "60507bffc0b13e7a81753a51005dbbeba4b23018", "fa6c6ad6d9f57d86df90bc573c734c7342d4d3dc4fff1525f19344f6444c0b83", "BSD-2-Clause"),
    ("svtav1", "https://github.com/AOMediaCodec/SVT-AV1", "4ae9272b588a05ee6e77a43e8dfdac05f54c4ff0", "851250a3e5756eacfcd3e3368c8f25b4d1774205d5b7412768476d3788f4ed1f", "BSD-3-Clause-Clear"),
    ("zlib", "https://github.com/madler/zlib", "9f25df2c80c2b26c8b050fba880f0851d9d243ea", "af12bfe8e4d188ab7c1cd8a906e38ae578955a9f50ac4db36b5365d8d0cbaed5", "Zlib"),
    ("zimg", "https://github.com/sekrit-twc/zimg", "df9c1472b9541d0e79c8d02dae37fdf12f189ec2", "a230bc81ee53d166c636c228d20a27325ee4c6924dbc02023e30cde0f2d89944", "WTFPL"),
)
CHROMIUM_REV = "861389936bf33ea77529ee5dcc0605e68f6bb620"
ELECTRON_REV = "f81ca3d4f09f57667238120adacb01239d5ecd89"
ELECTRON_INPUTS = (
    ("electron-ffmpeg", "9db86ce5b5b454dfc96c435f9f0723012980c37f", "https://chromium.googlesource.com/chromium/third_party/ffmpeg/+archive/9db86ce5b5b454dfc96c435f9f0723012980c37f.tar.gz", "84d308463ae754c16531cdb7bd4676de82f6df96a56184cee4458adb72c5a28e", "LGPL-2.1-or-later"),
    ("electron-source", ELECTRON_REV, "https://codeload.github.com/electron/electron/tar.gz/" + ELECTRON_REV, "68553daae380ca6f13b55a83f629dc4b37b7e511faa9a5d22faef409e262e104", "MIT"),
    ("chromium-build", CHROMIUM_REV, "https://chromium.googlesource.com/chromium/src/+archive/" + CHROMIUM_REV + "/build.tar.gz", "194e5d42f785f144ab3b8f09af87c3eb1ba7762fd730756ec9ba018df6fefca7", "BSD-3-Clause"),
    ("chromium-ffmpeg-build", CHROMIUM_REV, "https://chromium.googlesource.com/chromium/src/+archive/" + CHROMIUM_REV + "/media/ffmpeg.tar.gz", "ee35bc94cf82f7cd604286f2ae1182caaa854f70b4839860bd55e13a15b90ecb", "BSD-3-Clause"),
    ("chromium-generate-stubs", CHROMIUM_REV, "https://chromium.googlesource.com/chromium/src/+archive/" + CHROMIUM_REV + "/tools/generate_stubs.tar.gz", "8331e615ed4a9f188f5866036ff1abfa03406ac1c0f28d35144b467a12b297b5", "BSD-3-Clause"),
)
# These licenses apply to the libraries, rather than merely optional CLI tools.
# The pinned BtbN recipes below supply each exact source revision and build edits.
COPYLEFT = {
    "20-libiconv": "LGPL-2.1-or-later",
    "25-fribidi": "LGPL-2.1-or-later",
    "25-gmp": "LGPL-3.0-or-later OR GPL-2.0-or-later",
    "45-libudfread": "LGPL-2.1-or-later",
    "50-aribb24/50-libaribb24": "LGPL-3.0-or-later",
    "50-chromaprint": "LGPL-2.1-or-later",
    "50-gme": "LGPL-2.1-or-later",
    "50-libbluray": "LGPL-2.1-or-later",
    "50-libmp3lame": "LGPL-2.0-or-later",
    "50-libplacebo": "LGPL-2.1-or-later",
    "50-libssh": "LGPL-2.1-or-later",
    "50-libzmq": "MPL-2.0",
    "50-openal": "LGPL-2.0-or-later",
    "50-soxr": "LGPL-2.1-or-later",
    "50-srt": "MPL-2.0",
    "50-twolame": "LGPL-2.1-or-later",
    "50-zvbi": "Mixed LGPL-2.0-or-later / GPL-2.0-only; original vendor source, omitted from the clean codec",
}
# Gitlink objects from the pinned libplacebo tree. URLs come from its .gitmodules.
LIBPLACEBO_MODULES = (
    ("3rdparty/Vulkan-Headers", "KhronosGroup/Vulkan-Headers", "450bd2232225d6c7728a4108055ac2e37cef6475"),
    ("3rdparty/fast_float", "fastfloat/fast_float", "97b54ca9e75f5303507699d27c6b4f4efe4641a1"),
    ("3rdparty/glad", "Dav1dde/glad", "73db193f853e2ee079bf3ca8a64aa2eaf6459043"),
    ("3rdparty/jinja", "pallets/jinja", "15206881c006c79667fe5154fe80c01c65410679"),
    ("3rdparty/markupsafe", "pallets/markupsafe", "297fc8e356e6836a62087949245d09a28e9f1b13"),
    ("demos/3rdparty/nuklear", "Immediate-Mode-UI/Nuklear", "242f35efa067a46c595645eeda7b1771ea1f83b1"),
)
USER_AGENT = "8K-Lossless-Video-Editor-source-collector/1"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def output_path(value: str | Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(OUT.resolve()) or path == OUT.resolve():
        raise ValueError("Output must be a new child directory of out/release-sources")
    return path


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip(".-")
    if not value or len(value) > 180:
        raise ValueError("Invalid source component name")
    return value


def fetch(url: str, cache: Path, name: str, expected: str | None = None) -> tuple[Path, dict]:
    """A successful cache entry is immutable; mismatches never get overwritten."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("Source downloads require HTTPS")
    name = safe_name(name)
    path = cache / name
    receipt_path = cache / (name + ".receipt.json")
    if path.exists():
        if not receipt_path.is_file():
            raise ValueError("Existing source has no verified receipt: " + name)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        actual = sha256(path)
        if receipt.get("url") != url or receipt.get("sha256") != actual or expected and actual != expected:
            raise ValueError("Cached source identity/checksum mismatch: " + name)
        return path, receipt
    cache.mkdir(parents=True, exist_ok=True)
    part = cache / (name + ".part")
    created = False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=45) as response, part.open("xb") as target:
            created = True
            value = hashlib.sha256()
            size = 0
            while data := response.read(1024 * 1024):
                size += len(data)
                if size > 1024 ** 3:
                    raise ValueError("Individual source archive exceeds 1 GiB limit")
                target.write(data)
                value.update(data)
            actual = value.hexdigest()
            if expected and actual != expected:
                raise ValueError("Downloaded source SHA-256 mismatch: " + name)
        receipt = {"url": url, "sha256": actual, "bytes": size}
        part.rename(path)
        with receipt_path.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(receipt, target, indent=2)
            target.write("\n")
        return path, receipt
    except BaseException:
        # Only this invocation's task-owned partial file is removed.
        if created and part.is_file():
            part.unlink()
        raise


def tar_members(path: Path) -> dict[str, bytes]:
    """Read small text files in memory; never extract an upstream archive."""
    result = {}
    with tarfile.open(path) as archive:
        members = [member for member in archive if member.isfile()]
        parsed = []
        for member in members:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in member.name or any(":" in part for part in relative.parts):
                raise ValueError("Unsafe source archive path")
            parsed.append(relative)
        # GitHub snapshots have one enclosing directory; Gitiles subtree
        # snapshots have files directly at their root. Preserve both layouts.
        strip_root = bool(parsed) and all(len(path.parts) >= 2 for path in parsed) and len({path.parts[0] for path in parsed}) == 1
        for member, relative in zip(members, parsed):
            if member.size > 2 * 1024 * 1024:
                continue
            stream = archive.extractfile(member)
            if stream is not None:
                result[str(PurePosixPath(*(relative.parts[1:] if strip_root else relative.parts)))] = stream.read()
    return result


def recipe_inventory(files: dict[str, bytes]) -> list[dict]:
    result = []
    for path, data in sorted(files.items()):
        if not path.startswith("scripts.d/") or not path.endswith(".sh"):
            continue
        text = data.decode("utf-8")
        variables = dict(re.findall(r'^SCRIPT_([A-Z0-9_]+)="([^"\n]+)"', text, re.M))
        if not variables.get("REPO"):
            continue
        result.append({"recipe": path, "repository": variables["REPO"],
                       "revision": variables.get("COMMIT") or variables.get("REV"),
                       "variables": variables,
                       "configure_flags": sorted(set(re.findall(r"--enable-[A-Za-z0-9_-]+", text))),
                       "source_archive_excludes_git_submodules": "submodule update" in text,
                       "generated_dependency_resolution": "git-sync-deps" in text or "cargo update" in text,
                       "dependencies": re.findall(r"^\s*echo ([A-Za-z0-9_-]+)\s*$", (re.search(r"ffbuild_depends\(\)\s*\{(.*?)\n\}", text, re.S) or [None, ""])[1], re.M),
                       "license": COPYLEFT.get(path[len("scripts.d/"):-3], "See upstream license; source URL retained in pinned recipe")})
    return result


def git_archive_urls(repository: str, revision: str) -> list[str]:
    repository = repository.removesuffix(".git")
    parsed = urllib.parse.urlparse(repository)
    path = parsed.path.strip("/")
    if parsed.netloc == "github.com":
        return [f"https://codeload.github.com/{path}/tar.gz/{revision}"]
    if parsed.netloc in {"gitlab.com", "code.videolan.org", "gitlab.freedesktop.org"}:
        project = path.rsplit("/", 1)[-1]
        return [f"https://{parsed.netloc}/{path}/-/archive/{revision}/{project}-{revision}.tar.gz"]
    if parsed.netloc.endswith(".googlesource.com"):
        return [f"https://{parsed.netloc}/{path}/+archive/{revision}.tar.gz"]
    if repository == "https://git.code.sf.net/p/soxr/code":
        # The maintainer's GitHub repository carries this identical Git object.
        return [f"https://codeload.github.com/chirlu/soxr/tar.gz/{revision}"]
    return []


def fetch_git(repository: str, revision: str, cache: Path, name: str) -> tuple[Path, dict]:
    """Fallback for hosts without snapshots; Git verifies the requested object ID.

    This creates only an isolated temporary bare repository in the source cache;
    it never checks out source or alters the application's repository/index.
    """
    if not repository.startswith("https://") or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Git fallback requires HTTPS and a full pinned object ID")
    path = cache / (safe_name(name + "-" + revision) + ".tar.gz")
    receipt_path = Path(str(path) + ".receipt.json")
    source_url = repository + "#" + revision
    if path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("url") != source_url or receipt.get("sha256") != sha256(path):
            raise ValueError("Git archive cache identity mismatch")
        return path, receipt
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="git-source-", dir=cache) as temporary:
        temp = Path(temporary).resolve()
        if not temp.is_relative_to(cache.resolve()):
            raise ValueError("Temporary Git source path escaped cache")
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        common = ["git", "-c", "credential.helper=", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never"]
        def run(command):
            return subprocess.run(common + command, cwd=temp, env=environment, check=True,
                                  capture_output=True, timeout=90).stdout
        run(["init", "--bare", "."])
        run(["fetch", "--no-tags", "--no-recurse-submodules", "--depth=1", repository, revision])
        actual = run(["rev-parse", "FETCH_HEAD"]).decode().strip()
        if actual != revision:
            raise ValueError("Fetched Git source object differs from pinned revision")
        run(["archive", "--format=tar", "--prefix=" + safe_name(name) + "/", "--output=" + str(temp / "source.tar"), revision])
        part = Path(str(path) + ".part")
        created = False
        try:
            with (temp / "source.tar").open("rb") as source, part.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
                created = True
                shutil.copyfileobj(source, target)
            part.rename(path)
        finally:
            if created and part.exists():
                part.unlink()
    receipt = {"url": source_url, "sha256": sha256(path), "bytes": path.stat().st_size,
               "provenance": "HTTPS Git fetch; FETCH_HEAD verified equal to pinned 40-character object ID; git archive; deterministic gzip"}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path, receipt


def fetch_lame_svn(cache: Path) -> tuple[Path, dict]:
    """Export revision6531 over read-only SVN/DAV without needing an SVN client."""
    base = "https://svn.code.sf.net/p/lame/svn/!svn/rvr/6531/trunk/lame/"
    path = cache / "lame-svn-r6531.tar.gz"
    receipt_path = Path(str(path) + ".receipt.json")
    if path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("url") != base or receipt.get("sha256") != sha256(path):
            raise ValueError("SVN archive cache identity mismatch")
        return path, receipt
    cache.mkdir(parents=True, exist_ok=True)
    file_cache = cache / "svn-lame-r6531"
    pending = [""]
    visited = set()
    records = []
    body = b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:" xmlns:S="http://subversion.tigris.org/xmlns/svn/"><D:prop><D:resourcetype/><S:executable/><S:externals/></D:prop></D:propfind>'
    total = 0
    while pending:
        directory = pending.pop(0)
        if directory in visited or len(visited) > 1000:
            raise ValueError("Unexpected repeated/excessive SVN directories")
        visited.add(directory)
        request = urllib.request.Request(base + urllib.parse.quote(directory, safe="/"), data=body, method="PROPFIND",
                                         headers={"Depth": "1", "Content-Type": "text/xml", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("SVN listing exceeds limit")
        for response in ET.fromstring(data).findall("{DAV:}response"):
            href = response.findtext("{DAV:}href")
            if not href:
                raise ValueError("SVN source entry has no path")
            url = urllib.parse.urljoin(base, href)
            if not url.startswith(base):
                raise ValueError("SVN source entry escaped pinned revision scope")
            relative = urllib.parse.unquote(url[len(base):])
            pieces = PurePosixPath(relative).parts
            if any(part in {"..", "."} for part in pieces) or len(pieces) > 32 or "\\" in relative:
                raise ValueError("Invalid SVN source path")
            if relative.rstrip("/") == directory.rstrip("/"):
                continue
            if PurePosixPath(relative).parent != PurePosixPath(directory or "."):
                raise ValueError("SVN depth-one response returned a nested/unrelated entry")
            collection = executable = False
            for propstat in response.findall("{DAV:}propstat"):
                if " 200 " not in (propstat.findtext("{DAV:}status") or ""):
                    continue
                prop = propstat.find("{DAV:}prop")
                if prop is None:
                    continue
                if prop.find("{http://subversion.tigris.org/xmlns/svn/}externals") is not None:
                    raise ValueError("SVN externals require separate pinned source collection")
                collection |= prop.find("{DAV:}resourcetype/{DAV:}collection") is not None
                executable |= prop.find("{http://subversion.tigris.org/xmlns/svn/}executable") is not None
            if collection:
                pending.append(relative.rstrip("/") + "/")
                continue
            if len(records) >= 10000:
                raise ValueError("SVN source exceeds file-count bound")
            local, receipt = fetch(url, file_cache, hashlib.sha256(url.encode()).hexdigest()[:20] + "-" + PurePosixPath(relative).name)
            total += receipt["bytes"]
            if total > 128 * 1024 * 1024:
                raise ValueError("SVN source exceeds byte bound")
            records.append({"file": relative, "mode": 0o755 if executable else 0o644,
                            "local": local, **receipt})
            if len(records) % 50 == 0:
                print("LAME SVN source files " + str(len(records)), flush=True)
    if len(records) < 100:
        raise ValueError("SVN source tree unexpectedly small")
    part = Path(str(path) + ".part")
    created = False
    try:
        with part.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            created = True
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for record in sorted(records, key=lambda value: value["file"]):
                    info = tarfile.TarInfo("lame-svn-r6531/" + record["file"])
                    info.mode, info.size = record["mode"], record["bytes"]
                    with record["local"].open("rb") as stream:
                        archive.addfile(info, stream)
                evidence = json.dumps([{key: value for key, value in record.items() if key != "local"} for record in records], indent=2).encode()
                info = tarfile.TarInfo("lame-svn-r6531/SVN-EXPORT-MANIFEST.json")
                info.mode, info.size = 0o644, len(evidence)
                archive.addfile(info, io.BytesIO(evidence))
        part.rename(path)
    finally:
        if created and part.exists():
            part.unlink()
    receipt = {"url": base, "sha256": sha256(path), "bytes": path.stat().st_size,
               "source_files": len(records), "provenance": "Read-only SVN/DAV revision6531 export; executable properties retained; per-file checksums included"}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path, receipt


def source_licenses(files: dict[str, bytes], destination: Path) -> int:
    count = 0
    for name, value in files.items():
        path = PurePosixPath(name)
        if re.match(r"(?i)^(license|licence|copying|copyright|notice|authors)([._-]|$)", path.name):
            target = destination / Path(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
            count += 1
    return count


def chromium_opus_source(cache: Path) -> tuple[Path, dict]:
    """Exact Chromium tree export, reusing upstream bytes only by Git blob ID."""
    tree_expected = "4b02723565af08f67e76c7b55fc7d1ece1b3b1ba"
    archive_expected = "480e1fb9f63a7b9b80e41650734dca1f51b0effe1ae0abe37b60bee2a2499c6f"
    base = "https://chromium.googlesource.com/chromium/src/+/" + CHROMIUM_REV + "/third_party/opus/"
    listing_url = base + "?format=JSON&recursive=1"
    name = "electron-chromium-opus-" + CHROMIUM_REV + ".tar.gz"
    output, receipt_path = cache / name, cache / (name + ".receipt.json")
    if output.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("git_tree") != tree_expected or receipt.get("url") != listing_url or sha256(output) != archive_expected:
            raise ValueError("Chromium Opus export identity mismatch")
        return output, receipt
    upstream, _ = fetch("https://codeload.github.com/xiph/opus/tar.gz/55513e81d8f606bd75d0ff773d2144e5f2a732f5", cache,
                        "electron-opus-upstream-55513e81d8f606bd75d0ff773d2144e5f2a732f5.tar.gz",
                        "d1fa095d69bedeab00f7d1d7c153a7b9fac8bb49f2a54d7d182acc17598ed7b8")
    def blob_id(data):
        return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    def tree_id(tree):
        chunks = []
        for key, value in sorted(tree.items(), key=lambda item: (item[0] + ("/" if isinstance(item[1], dict) else "")).encode()):
            mode, identity = (0o40000, tree_id(value)) if isinstance(value, dict) else value
            chunks.append(format(mode, "o").encode() + b" " + key.encode() + b"\0" + bytes.fromhex(identity))
        data = b"".join(chunks)
        return hashlib.sha1(b"tree " + str(len(data)).encode() + b"\0" + data).hexdigest()
    def read_url(url, limit=16000000):
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=25) as response:
                    data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Chromium source response exceeds bound")
                return data
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(1)
    reusable = {blob_id(data): data for data in tar_members(upstream).values()}
    raw = read_url(listing_url, 1000000)
    listing = json.loads(raw.decode().split("\n", 1)[1])
    if listing.get("id") != tree_expected or len(listing["entries"]) != 715:
        raise ValueError("Unexpected Chromium Opus source tree")
    files, root = [], {}
    reused = fetched = network_bytes = 0
    for entry in listing["entries"]:
        relative = PurePosixPath(entry["name"])
        if relative.is_absolute() or ".." in relative.parts or entry["type"] != "blob" or entry["mode"] not in (0o100644, 0o100755):
            raise ValueError("Unsafe Chromium source entry")
        data = reusable.get(entry["id"])
        if data is None:
            body = read_url(base + urllib.parse.quote(entry["name"], safe="/") + "?format=TEXT")
            data = base64.b64decode(body, validate=True); fetched += 1; network_bytes += len(body)
        else:
            reused += 1
        if blob_id(data) != entry["id"]:
            raise ValueError("Chromium Opus blob differs from pinned Git tree")
        files.append((entry, data))
        node = root
        for part in relative.parts[:-1]:
            node = node.setdefault(part, {})
        node[relative.name] = (entry["mode"], entry["id"])
    if tree_id(root) != tree_expected:
        raise ValueError("Reconstructed Chromium Opus root tree differs")
    partial = cache / (name + ".part")
    with partial.open("xb") as stream, gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for entry, data in sorted(files, key=lambda item: item[0]["name"]):
                member = tarfile.TarInfo("chromium-opus-" + CHROMIUM_REV + "/" + entry["name"])
                member.size, member.mode = len(data), entry["mode"] & 0o777
                member.mtime = member.uid = member.gid = 0; member.uname = member.gname = ""
                archive.addfile(member, io.BytesIO(data))
    if sha256(partial) != archive_expected:
        raise ValueError("Exact Chromium Opus archive differs from verified export")
    partial.rename(output)
    receipt = {"url": listing_url, "sha256": archive_expected, "bytes": output.stat().st_size,
               "kind": "gitiles-exact-tree-export", "revision": CHROMIUM_REV, "subtree": "third_party/opus",
               "git_tree": tree_expected, "file_count": 715, "reused_upstream_blobs": reused,
               "fetched_chromium_blobs": fetched, "network_body_bytes": network_bytes + len(raw)}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    return output, receipt


def collect_electron_sources(collector) -> None:
    for name, revision, url, expected, license in ELECTRON_INPUTS:
        collector.add(name, [url], revision=revision, expected=expected, license=license,
                      recipe="Electron45.2.1/Chromium156 pinned DEPS and FFmpeg dynamic-library build")
    try:
        path, receipt = chromium_opus_source(collector.cache)
        collector.store("electron-chromium-opus", path, receipt, CHROMIUM_REV, "Opus-Patent-BSD-3-Clause", "Chromium third_party/opus exact tree")
    except (OSError, ValueError, tarfile.TarError) as error:
        collector.missing.append({"component": "electron-chromium-opus", "reason": str(error)[:240]})


def snapshot_application(destination: Path) -> list[dict]:
    # Read-only Git inventory includes authorized new source files while honoring
    # .gitignore. No commit/checkout/index changes are performed.
    listing = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             cwd=ROOT, check=True, capture_output=True, timeout=30).stdout
    records = []
    for raw in sorted(set(listing.split(b"\0")) - {b""}):
        name = raw.decode("utf-8")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] in {"out", "data", "build", ".git"}:
            raise ValueError("Private/generated path appeared in public source inventory")
        source = ROOT.joinpath(*relative.parts)
        if source.is_symlink():
            raise ValueError("Public source snapshot refuses symlinks")
        if not source.is_file():
            continue
        if not source.resolve().is_relative_to(ROOT.resolve()) or source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Unexpected source file scope/size")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records.append({"file": name, "sha256": sha256(target), "bytes": target.stat().st_size})
    return records


def create_archive(directory: Path, archive_path: Path) -> dict:
    """Stable tar metadata; archive never stores workstation names or paths."""
    with archive_path.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(directory.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(directory).as_posix()
                info = tarfile.TarInfo("8k-editor-release-sources/" + relative)
                info.size = path.stat().st_size
                info.mode = 0o755 if path.suffix in {".sh", ".command"} else 0o644
                info.mtime = info.uid = info.gid = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
    return {"file": archive_path.name, "sha256": sha256(archive_path), "bytes": archive_path.stat().st_size}


def validate_native_receipt(receipt: dict) -> dict:
    if not isinstance(receipt, dict) or receipt.get("source_commit") != FFMPEG_REV:
        raise ValueError("Native override receipt must pin the exact FFmpeg source revision")
    if receipt.get("component") not in {"avformat-62.dll", "avcodec-62.dll"}:
        raise ValueError("Native override receipt has an unknown component")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("output_sha256", ""))) or not isinstance(receipt.get("output_bytes"), int) or receipt["output_bytes"] <= 0:
        raise ValueError("Native override receipt requires the actual binary hash and size")
    if receipt.get("library_license") != "LGPL version 3 or later":
        raise ValueError("Native override did not report the expected library license")
    configuration = receipt.get("library_configuration", "")
    if not isinstance(configuration, str) or any(flag in configuration.split() for flag in ("--enable-gpl", "--enable-nonfree", "--enable-chromaprint", "--enable-libzvbi")):
        raise ValueError("Native override configuration enables a rejected dependency")
    # Receipts are release material; workstation/private paths stay out.
    if re.search(r"(?i)(?:\b[a-z]:[\\/]+|/mnt/[a-z]/|/users/|/home/|/root/)", json.dumps(receipt)):
        raise ValueError("Native override receipt contains a private absolute path; supply the public build receipt")
    return receipt


class Collector:
    def __init__(self, directory: Path):
        self.directory = directory
        self.cache = OUT / "cache"
        self.records: list[dict] = []
        self.missing: list[dict] = []

    def store(self, name: str, cached: Path, receipt: dict, revision: str, license: str, recipe: str | None) -> dict[str, bytes]:
        target = self.directory / "third-party" / cached.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, target)
        files = tar_members(target)
        licenses = source_licenses(files, self.directory / "notices" / safe_name(name))
        self.records.append({"component": name, "revision": revision, "license": license,
                             "recipe": recipe, "file": target.relative_to(self.directory).as_posix(),
                             "license_files": licenses, **receipt})
        print("Collected " + name + " " + str(receipt["bytes"]) + " bytes", flush=True)
        return files

    def add(self, name: str, urls: list[str], *, revision: str, license: str, expected: str | None = None,
            recipe: str | None = None, repository: str | None = None) -> dict[str, bytes] | None:
        failures = []
        for index, url in enumerate(urls):
            try:
                url_path = urllib.parse.urlparse(url).path
                extension = next((suffix for suffix in (".tar.xz", ".tar.bz2", ".tar.gz", ".tgz") if url_path.endswith(suffix)), ".tar.gz")
                cached, receipt = fetch(url, self.cache, safe_name(name + "-" + revision) + extension, expected)
                return self.store(name, cached, receipt, revision, license, recipe)
            except (OSError, ValueError, tarfile.TarError) as error:
                failures.append({"url": url, "error": str(error)[:240]})
        if repository:
            try:
                cached, receipt = fetch_git(repository, revision, self.cache, name)
                return self.store(name, cached, receipt, revision, license, recipe)
            except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
                # Do not capture Git's stderr, which can contain workstation paths.
                failures.append({"url": repository, "error": type(error).__name__ + ": pinned Git source fetch failed"})
        self.missing.append({"component": name, "revision": revision, "recipe": recipe, "attempts": failures})
        print("Unavailable " + name, flush=True)
        return None


def collect(args) -> int:
    output = output_path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    directory = output / "contents"
    directory.mkdir()
    collector = Collector(directory)
    btbn = collector.add("btbn-build-recipes", git_archive_urls("https://github.com/BtbN/FFmpeg-Builds", BTBN_REV),
                         revision=BTBN_REV, license="MIT")
    collector.add("ffmpeg-windows", git_archive_urls("https://github.com/FFmpeg/FFmpeg", FFMPEG_REV),
                  revision=FFMPEG_REV, license="LGPL-3.0-or-later")
    collector.add("pyav", [PYAV_SDIST["url"]], revision="17.0.0", license="BSD-3-Clause", expected=PYAV_SDIST["sha256"])
    for name, repository, revision, expected, license in REBUILD_INPUTS:
        collector.add(name, git_archive_urls(repository, revision), revision=revision,
                      license=license, expected=expected, recipe="Application's targeted native rebuild script")
    collect_electron_sources(collector)
    inventory = recipe_inventory(btbn or {})
    for item in inventory:
        key = item["recipe"][len("scripts.d/"):-3]
        if key not in COPYLEFT:
            continue
        revision = item["revision"]
        urls = git_archive_urls(item["repository"], revision)
        if key == "50-libmp3lame":
            try:
                cached, receipt = fetch_lame_svn(collector.cache)
                collector.store(key, cached, receipt, revision, COPYLEFT[key], item["recipe"])
            except (OSError, ValueError, ET.ParseError, tarfile.TarError) as error:
                collector.missing.append({"component": key, "revision": revision, "reason": str(error)[:240]})
        else:
            collector.add(key, urls, revision=revision, license=COPYLEFT[key], recipe=item["recipe"], repository=item["repository"])
        # libiconv builds against an explicitly pinned embedded gnulib checkout.
        if key == "20-libiconv":
            variables = item["variables"]
            collector.add("libiconv-gnulib", git_archive_urls(variables["MIRROR2"], variables["COMMIT2"]),
                          revision=variables["COMMIT2"], license="See per-file LGPL/GPL exceptions", recipe=item["recipe"])
        if key == "50-libplacebo":
            for module_path, repository, module_rev in LIBPLACEBO_MODULES:
                collector.add("libplacebo-" + module_path, git_archive_urls("https://github.com/" + repository, module_rev),
                              revision=module_rev, license="See complete included upstream licenses",
                              recipe=item["recipe"] + ": gitlink " + module_path)
        elif item["source_archive_excludes_git_submodules"]:
            collector.missing.append({"component": key, "revision": revision,
                                      "reason": "Required git submodule inputs need collection at their pinned gitlink revisions"})
    if not inventory:
        collector.missing.append({"component": "btbn-recipes", "reason": "Could not inspect pinned build recipes"})
    if args.include_macos_pyav:
        collect_macos_pyav(collector)
    overrides = []
    for filename in args.native_override_receipt:
        receipt = validate_native_receipt(json.loads(Path(filename).read_text(encoding="utf-8")))
        overrides.append(receipt)
        (directory / "native-builds").mkdir(exist_ok=True)
        target = directory / "native-builds" / (safe_name(receipt.get("component", "native")) + ".json")
        if target.exists():
            raise ValueError("Duplicate native override component")
        target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    overridden = {str(item.get("component", "")).split("-", 1)[0] for item in overrides}
    if not {"avformat", "avcodec"}.issubset(overridden):
        collector.missing.append({"component": "clean-native-overrides", "reason": "Original BtbN avformat/avcodec contain transitive GPL code; supply both final public source-build receipts before publishing this source set with a GPU installer"})
    app_files = snapshot_application(directory / "application")
    native_config = None
    if args.native_config:
        source = Path(args.native_config)
        native_config = source.read_text(encoding="utf-8")
        (directory / "windows-ffmpeg-buildconf.txt").write_text(native_config, encoding="utf-8", newline="\n")
        if "--enable-gpl" in native_config or "--enable-nonfree" in native_config or "--enable-libx264" in native_config or "--enable-libx265" in native_config:
            raise ValueError("Expected the pinned true-LGPL native build, but found GPL/nonfree flags")
    else:
        collector.missing.append({"component": "native-binary-config", "reason": "Pass actual pinned binary --native-config evidence before claiming source coverage"})
    manifest = {
        "schema": 1, "application_revision": args.application_revision,
        "runtime_code_revision": args.runtime_code_revision,
        "application_snapshot": "Actual working-tree source bytes; file hashes are authoritative",
        "application_files": app_files, "windows_binary": BTBN_BINARY,
        "windows_build_recipe_revision": BTBN_REV, "windows_ffmpeg_revision": FFMPEG_REV,
        "sources": collector.records, "btbn_recipe_input_inventory": inventory,
        "electron": {"version": "45.2.1", "source_commit": ELECTRON_REV, "chromium_commit": CHROMIUM_REV,
                     "ffmpeg_commit": "9db86ce5b5b454dfc96c435f9f0723012980c37f", "ffmpeg_license": "LGPL-2.1-or-later",
                     "scope": "Exact fork/Opus source plus patch/build provenance; standalone byte-for-byte Chromium rebuild not claimed"},
        "native_source_build_overrides": overrides,
        "unresolved": collector.missing,
        "source_coverage": "incomplete" if collector.missing else "listed copyleft source inputs collected; final release dependency review required",
        "scope": "Application source, pinned build scripts, selected copyleft library sources, full extracted source notices; other source URLs retained in recipe inventory. This manifest is not a claim that every installer component has been audited.",
    }
    (directory / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    archive = create_archive(directory, output / "8k-editor-corresponding-source.tar.gz")
    (output / "SHA256SUMS").write_text(archive["sha256"] + "  " + archive["file"] + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({**archive, "unresolved": collector.missing, "source_count": len(collector.records)}, indent=2))
    return 2 if collector.missing else 0


def collect_macos_pyav(collector: Collector, *, native_pending=True) -> None:
    files = collector.add("pyav-ffmpeg-recipes", git_archive_urls("https://github.com/PyAV-Org/pyav-ffmpeg", PYAV_VENDOR_REV),
                          revision=PYAV_VENDOR_REV, license="See upstream repository")
    if not files:
        return
    # Read literals only: never import or execute the vendor's build script.
    tree = ast.parse(files["scripts/build-ffmpeg.py"])
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "Package":
            continue
        data = {keyword.arg: keyword.value.value for keyword in node.keywords
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)}
        if data.get("name") not in {"ffmpeg", "lame", "ogg", "opus", "speex", "vorbis", "dav1d", "libsvtav1", "vpx", "png", "webp", "openh264", "opencore-amr", "x264", "x265"}:
            continue
        url = data["source_url"].replace("http://", "https://", 1)
        urls = [url]
        if url.startswith("https://ffmpeg.org/"):
            urls.insert(0, url.replace("https://ffmpeg.org/", "https://www.ffmpeg.org/", 1))
        collector.add("mac-pyav-" + data["name"], urls, revision=data["sha256"][:16],
                      license="See complete included upstream license files", expected=data["sha256"], recipe="pyav-ffmpeg/scripts/build-ffmpeg.py")
    if native_pending:
        collector.missing.append({"component": "macos-native-ffmpeg", "reason": "Mac native FFmpeg source/formula/dylib receipt must be supplied separately; PyAV sources do not describe Homebrew FFmpeg"})



# Exact79-formula release capture; changes require reviewing its saved recipes.
MAC_NATIVE_INVENTORY_SHA256 = "559be72a8bb839e0b8084e427aa37fea0459fcf12acdc2fdf3da31ef4d607b4d"
MAC_HOMEBREW_AUXILIARY_PLAN = {'schema_version': 1,
 'capture_zip_sha256': '0643c3be55f7e824d2050e03e33380a2e1c38dc051fa739ad60595dd6e339c91',
 'required_resources': [{'formula': 'glib',
                         'kind': 'resource',
                         'name': 'gobject-introspection',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/glib/2.90.1/.brew/glib.rb',
                         'recipe_line': 51,
                         'url': 'https://download.gnome.org/sources/gobject-introspection/1.86/gobject-introspection-1.86.0.tar.xz',
                         'sha256': '920d1a3fcedeadc32acff95c2e203b319039dd4b4a08dd1a2dfd283d19c0b9ae',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'jpeg-xl',
                         'kind': 'resource',
                         'name': 'sjpeg',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/jpeg-xl/0.12.0_1/.brew/jpeg-xl.rb',
                         'recipe_line': 35,
                         'url': 'https://github.com/webmproject/sjpeg/archive/94e0df6d0f8b44228de5be0ff35efb9f946a13c9.tar.gz',
                         'sha256': 'ac94917fe745a674eabf1e044f23ec55cd5a548c9869c06ec4b19da14ee0227d',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'libplacebo',
                         'kind': 'resource',
                         'name': 'glad2',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libplacebo/7.387.0/.brew/libplacebo.rb',
                         'recipe_line': 23,
                         'url': 'https://files.pythonhosted.org/packages/6e/5a/d62b24fe1c7c2f34e15c2aa4418a5327a8550fdc272999a59e0dddebc3ee/glad2-2.0.8.tar.gz',
                         'sha256': 'b84079b9fa404f37171b961bdd1d8da21370e6c818defb8481c5b3fe3d6436da',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'libplacebo',
                         'kind': 'resource',
                         'name': 'jinja2',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libplacebo/7.387.0/.brew/libplacebo.rb',
                         'recipe_line': 28,
                         'url': 'https://files.pythonhosted.org/packages/df/bf/f7da0350254c0ed7c72f3e33cef02e048281fec7ecec5f032d4aac52226b/jinja2-3.1.6.tar.gz',
                         'sha256': '0137fb05990d35f1275a587e9aee6d56da821fc83491a0fb838183be43f66d6d',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'libplacebo',
                         'kind': 'resource',
                         'name': 'markupsafe',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libplacebo/7.387.0/.brew/libplacebo.rb',
                         'recipe_line': 33,
                         'url': 'https://files.pythonhosted.org/packages/38/9b/e422a865e1d5d57d0e509b4e0bf1c1a70a7f6382c29a5aa428df994c8bc8/markupsafe-3.0.4.tar.gz',
                         'sha256': '2e9ad7dd851bf45fab9f75cbff4cb493fee9979e8d8c7c9c3ee119022518edd6',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'shaderc',
                         'kind': 'resource',
                         'name': 'glslang',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/shaderc/2026.4/.brew/shaderc.rb',
                         'recipe_line': 10,
                         'url': 'https://github.com/KhronosGroup/glslang.git',
                         'git_revision': 'e1b562a8bed273a02f30b59b66a5d499793cede5',
                         'source_archive_url': 'https://codeload.github.com/KhronosGroup/glslang/tar.gz/e1b562a8bed273a02f30b59b66a5d499793cede5',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'shaderc',
                         'kind': 'resource',
                         'name': 'spirv-headers',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/shaderc/2026.4/.brew/shaderc.rb',
                         'recipe_line': 22,
                         'url': 'https://github.com/KhronosGroup/SPIRV-Headers.git',
                         'git_revision': '04fd3caa1e8267e4d95c806cad901181728e1006',
                         'source_archive_url': 'https://codeload.github.com/KhronosGroup/SPIRV-Headers/tar.gz/04fd3caa1e8267e4d95c806cad901181728e1006',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'shaderc',
                         'kind': 'resource',
                         'name': 'spirv-tools',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/shaderc/2026.4/.brew/shaderc.rb',
                         'recipe_line': 34,
                         'url': 'https://github.com/KhronosGroup/SPIRV-Tools.git',
                         'git_revision': 'ef96ed763b43b59b33b31b362f09a02b729fa1c9',
                         'source_archive_url': 'https://codeload.github.com/KhronosGroup/SPIRV-Tools/tar.gz/ef96ed763b43b59b33b31b362f09a02b729fa1c9',
                         'scope': 'stable build source/tool input'},
                        {'formula': 'tesseract',
                         'kind': 'resource',
                         'name': 'eng',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/tesseract/5.5.4/.brew/tesseract.rb',
                         'recipe_line': 33,
                         'url': 'https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/eng.traineddata',
                         'sha256': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
                         'scope': 'installed model data; not source'},
                        {'formula': 'tesseract',
                         'kind': 'resource',
                         'name': 'osd',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/tesseract/5.5.4/.brew/tesseract.rb',
                         'recipe_line': 38,
                         'url': 'https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/osd.traineddata',
                         'sha256': '9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff',
                         'scope': 'installed model data; not source'}],
 'required_remote_patches': [{'formula': 'gnutls',
                              'kind': 'patch',
                              'name': 'patch-49',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/gnutls/3.8.13_2/.brew/gnutls.rb',
                              'recipe_line': 49,
                              'url': 'https://gitlab.com/gnutls/gnutls/-/commit/29c2027c963cf559817d0da37c1fc2efd0c1bd6a.diff',
                              'sha256': 'c0bfc0164ff131c9d2e1c4d0367430f8b767b64c9c21e0f408bd634d21fb0302',
                              'scope': 'stable build/install patch'},
                             {'formula': 'libb2',
                              'kind': 'patch',
                              'name': 'patch-11',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libb2/0.98.1/.brew/libb2.rb',
                              'recipe_line': 11,
                              'url': 'https://raw.githubusercontent.com/Homebrew/formula-patches/03cf8088210822aa2c1ab544ed58ea04c897d9c4/libtool/configure-big_sur.diff',
                              'sha256': '35acd6aebc19843f1a2b3a63e880baceb0f5278ab1ace661e57a502d9d78c93c',
                              'scope': 'stable build/install patch'},
                             {'formula': 'libsamplerate',
                              'kind': 'patch',
                              'name': 'patch-15',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libsamplerate/0.2.2/.brew/libsamplerate.rb',
                              'recipe_line': 15,
                              'url': 'https://github.com/libsndfile/libsamplerate/commit/e4a0ab46887029e0f65f145ba3987cc592f18200.patch?full_index=1',
                              'sha256': '0826fb59d733188645f3dc207c938580970700381b9bc87058b137723cb30bba',
                              'scope': 'stable build/install patch'},
                             {'formula': 'libsamplerate',
                              'kind': 'patch',
                              'name': 'patch-21',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libsamplerate/0.2.2/.brew/libsamplerate.rb',
                              'recipe_line': 21,
                              'url': 'https://github.com/libsndfile/libsamplerate/commit/1abc639420b2df8b9ff2e0bdcc28cf6613c7c0d0.patch?full_index=1',
                              'sha256': 'b6fb61c763a0f072ef2ebb3a311037d2429a5e294141f6d5e552ccd25efabdc0',
                              'scope': 'stable build/install patch'},
                             {'formula': 'libsoxr',
                              'kind': 'patch',
                              'name': 'patch-20',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libsoxr/0.1.3/.brew/libsoxr.rb',
                              'recipe_line': 20,
                              'url': 'https://raw.githubusercontent.com/Homebrew/formula-patches/76868b36263be42440501d3692fd3a258f507d82/libsoxr/arm64_defines.patch',
                              'sha256': '9df5737a21b9ce70cc136c302e195fad9f9f6c14418566ad021f14bb34bb022c',
                              'scope': 'stable build/install patch'},
                             {'formula': 'openjpeg',
                              'kind': 'patch',
                              'name': 'patch-14',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/openjpeg/2.5.4_1/.brew/openjpeg.rb',
                              'recipe_line': 14,
                              'url': 'https://github.com/uclouvain/openjpeg/commit/91d08b11a72764f6d199f32fa0c1b1abd4edc2ad.patch?full_index=1',
                              'sha256': '40648d63bfbb0f6c3a41cb9425ac48c396772c3527fff4b2979b07f42aaea6b8',
                              'scope': 'stable build/install patch'},
                             {'formula': 'openjpeg',
                              'kind': 'patch',
                              'name': 'patch-22',
                              'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/openjpeg/2.5.4_1/.brew/openjpeg.rb',
                              'recipe_line': 22,
                              'url': 'https://github.com/uclouvain/openjpeg/commit/839936aa33eb8899bbbd80fda02796bb65068951.patch?full_index=1',
                              'sha256': 'd06af7a5e1681bb602e71bc1724718ff07f49aace9d841164658d72b1cfe7d5d',
                              'scope': 'stable build/install patch'}],
 'required_local_patches': [{'formula': 'glib',
                             'kind': 'patch',
                             'name': 'patch-61',
                             'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/glib/2.90.1/.brew/glib.rb',
                             'recipe_line': 61,
                             'file': 'Patches/glib/hardcoded-paths.diff',
                             'sha256': 'd846efd0bf62918350da94f850db33b0f8727fece9bfaf8164566e3094e80c97',
                             'url': 'https://raw.githubusercontent.com/Homebrew/homebrew-core/HEAD/Patches/glib/hardcoded-paths.diff',
                             'url_status': 'candidate official body URL; not downloaded; require exact '
                                           'captured SHA256',
                             'body_in_capture_zip': False,
                             'tap_git_head': None}],
 'inline_patches_already_captured': [{'formula': 'libvpx',
                                      'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libvpx/1.17.0/.brew/libvpx.rb',
                                      'recipe_line': 15,
                                      'body_present': True,
                                      'scope': 'inline patch captured in complete recipe'},
                                     {'formula': 'snappy',
                                      'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/snappy/1.3.1/.brew/snappy.rb',
                                      'recipe_line': 15,
                                      'body_present': True,
                                      'scope': 'inline patch captured in complete recipe'},
                                     {'formula': 'xvid',
                                      'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/xvid/1.3.7/.brew/xvid.rb',
                                      'recipe_line': 17,
                                      'body_present': True,
                                      'scope': 'inline patch captured in complete recipe'}],
 'excluded_resources': [{'formula': 'aom',
                         'kind': 'resource',
                         'name': 'homebrew-bus_qcif_15fps.y4m',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/aom/3.15.3/.brew/aom.rb',
                         'recipe_line': 19,
                         'url': 'https://media.xiph.org/video/derf/y4m/bus_qcif_15fps.y4m',
                         'sha256': '868fc3446d37d0c6959a48b68906486bd64788b2e795f0e29613cbb1fa73480e',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'dav1d',
                         'kind': 'resource',
                         'name': 'homebrew-00000000.ivf',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/dav1d/1.5.4/.brew/dav1d.rb',
                         'recipe_line': 23,
                         'url': 'https://code.videolan.org/videolan/dav1d-test-data/raw/1.1.0/8-bit/data/00000000.ivf',
                         'sha256': '52b4351f9bc8a876c8f3c9afc403d9e90f319c1882bfe44667d41c8c6f5486f3',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'ggml',
                         'kind': 'resource',
                         'name': 'test-backend-ops.cpp',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/ggml/0.28.0/.brew/ggml.rb',
                         'recipe_line': 35,
                         'url': 'https://raw.githubusercontent.com/ggml-org/ggml/refs/tags/v0.28.0/tests/test-backend-ops.cpp',
                         'sha256': 'cc939ea00c5208ca825ca1f20d3dbbe79fbec69537baf3ac75ed78e05f73584e',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'graphite2',
                         'kind': 'resource',
                         'name': 'testfont',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/graphite2/1.3.15/.brew/graphite2.rb',
                         'recipe_line': 27,
                         'url': 'https://scripts.sil.org/pub/woff/fonts/Simple-Graphite-Font.ttf',
                         'sha256': '7e573896bbb40088b3a8490f83d6828fb0fd0920ac4ccdfdd7edb804e852186a',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'harfbuzz',
                         'kind': 'resource',
                         'name': 'homebrew-test-ttf',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/harfbuzz/15.0.0_1/.brew/harfbuzz.rb',
                         'recipe_line': 28,
                         'url': 'https://github.com/harfbuzz/harfbuzz/raw/fc0daafab0336b847ac14682e581a8838f36a0bf/test/shaping/fonts/sha1sum/270b89df543a7e48e206a2d830c0e10e5265c630.ttf',
                         'sha256': '9535d35dab9e002963eef56757c46881f6b3d3b27db24eefcc80929781856c77',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'libogg',
                         'kind': 'resource',
                         'name': 'oggfile',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libogg/1.3.6/.brew/libogg.rb',
                         'recipe_line': 30,
                         'url': 'https://upload.wikimedia.org/wikipedia/commons/c/c8/Example.ogg',
                         'sha256': 'f57b56d8aae4c847cf01224fb45293610d801cfdac43d932b5eeab1cd318182a',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'libpng',
                         'kind': 'resource',
                         'name': 'pngtest.png',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libpng/1.6.59/.brew/libpng.rb',
                         'recipe_line': 26,
                         'url': 'https://src.fedoraproject.org/rpms/libpng/raw/49e9a06ca115aaa911dd3419ee79c1870d1428fb/f/pngtest.png',
                         'sha256': 'f925a657a5343cfb724414c01e87afd4d60b1f82a46edc0e11f016a126f84064',
                         'scope': 'Linux-only build/test input; excluded for Mac'},
                        {'formula': 'libvorbis',
                         'kind': 'resource',
                         'name': 'oggfile',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/libvorbis/1.3.7/.brew/libvorbis.rb',
                         'recipe_line': 27,
                         'url': 'https://upload.wikimedia.org/wikipedia/commons/c/c8/Example.ogg',
                         'sha256': 'f57b56d8aae4c847cf01224fb45293610d801cfdac43d932b5eeab1cd318182a',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'rav1e',
                         'kind': 'resource',
                         'name': 'homebrew-bus_qcif_7.5fps.y4m',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/rav1e/0.8.1/.brew/rav1e.rb',
                         'recipe_line': 27,
                         'url': 'https://media.xiph.org/video/derf/y4m/bus_qcif_7.5fps.y4m',
                         'sha256': '1f5bfcce0c881567ea31c1eb9ecb1da9f9583fdb7d6bb1c80a8c9acfc6b66f6b',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'shaderc',
                         'kind': 'resource',
                         'name': 'glslang',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/shaderc/2026.4/.brew/shaderc.rb',
                         'recipe_line': 50,
                         'url': 'https://github.com/KhronosGroup/glslang.git',
                         'scope': 'head-only; excluded for captured stable bottles'},
                        {'formula': 'shaderc',
                         'kind': 'resource',
                         'name': 'spirv-tools',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/shaderc/2026.4/.brew/shaderc.rb',
                         'recipe_line': 54,
                         'url': 'https://github.com/KhronosGroup/SPIRV-Tools.git',
                         'scope': 'head-only; excluded for captured stable bottles'},
                        {'formula': 'shaderc',
                         'kind': 'resource',
                         'name': 'spirv-headers',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/shaderc/2026.4/.brew/shaderc.rb',
                         'recipe_line': 58,
                         'url': 'https://github.com/KhronosGroup/SPIRV-Headers.git',
                         'scope': 'head-only; excluded for captured stable bottles'},
                        {'formula': 'svt-av1',
                         'kind': 'resource',
                         'name': 'homebrew-testvideo',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/svt-av1/4.2.0/.brew/svt-av1.rb',
                         'recipe_line': 33,
                         'url': 'https://github.com/grusell/svt-av1-homebrew-testdata/raw/main/video_64x64_yuv420p_25frames.yuv',
                         'sha256': '0c5cc90b079d0d9c1ded1376357d23a9782a704a83e01731f50ccd162e246492',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'tesseract',
                         'kind': 'resource',
                         'name': 'homebrew-test_resource',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/tesseract/5.5.4/.brew/tesseract.rb',
                         'recipe_line': 43,
                         'url': 'https://raw.githubusercontent.com/tesseract-ocr/test/6dd816cdaf3e76153271daf773e562e24c928bf5/testing/eurotext.tif',
                         'sha256': '7b9bd14aba7d5e30df686fbb6f71782a97f48f81b32dc201a1b75afe6de747d6',
                         'scope': 'test-only; excluded from production build/install inputs'},
                        {'formula': 'x265',
                         'kind': 'resource',
                         'name': 'homebrew-test_video',
                         'recipe_zip_path': 'mac-homebrew-capture-v1/formulae/x265/4.3/.brew/x265.rb',
                         'recipe_line': 71,
                         'url': 'https://raw.githubusercontent.com/fraunhoferhhi/vvenc/master/test/data/RTn23_80x44p15_f15.yuv',
                         'sha256': 'ecd2ef466dd2975f4facc889e0ca128a6bea6645df61493a96d8e7763b6f3ae9',
                         'scope': 'test-only; excluded from production build/install inputs'}]}

def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in value or any(":" in x for x in path.parts):
        raise ValueError("Unsafe captured source path")
    return path


def capture_recipes(directory, inventory_bytes, recipe_root):
    recipe_root = recipe_root.resolve()
    manifest_path = recipe_root / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["published_summary_binding"]["sha256"] != hashlib.sha256(inventory_bytes).hexdigest():
        raise ValueError("Mac capture does not bind the supplied native inventory")
    by_path = {record["path"]: record for record in manifest["files"]}
    if len(by_path) != len(manifest["files"]):
        raise ValueError("Duplicate Mac captured paths")
    for formula in json.loads(inventory_bytes)["formulae"]:
        for saved in formula["saved_files"]:
            record = by_path.get(saved["saved"])
            if not record or record["captured_original_sha256"] != saved["sha256"]:
                raise ValueError("Mac original capture identity mismatch")
    copied = []
    for record in manifest["files"]:
        relative = safe_relative(record["path"])
        source = recipe_root.joinpath(*relative.parts)
        if source.is_symlink() or not source.resolve().is_relative_to(recipe_root) or not source.is_file():
            raise ValueError("Mac captured body is missing/out of scope")
        if source.stat().st_size != record["archived_bytes"] or sha256(source) != record["archived_sha256"]:
            raise ValueError("Mac captured body checksum mismatch")
        if not record["byte_identical_to_captured_original"]:
            if record["kind"] != "sanitized_install_receipt" or not record["redactions"] or any(item["field"] not in ("/source/path", "/built_prefix", "/changed_files/11", "/changed_files/12") for item in record["redactions"]):
                raise ValueError("Unexpected Mac capture redaction")
            receipt = json.loads(source.read_bytes())
            for redaction in record["redactions"]:
                value = receipt
                for key in redaction["field"].split("/")[1:]:
                    value = value[int(key)] if isinstance(value, list) else value[key]
                if value != redaction["sanitized_value"] or not any(marker in value for marker in ("<user-home>", "<homebrew-prefix>")):
                    raise ValueError("Mac receipt redaction value differs from declared normalization")
        elif record["archived_sha256"] != record["captured_original_sha256"]:
            raise ValueError("Mac capture falsely claims original bytes")
        target = directory / "mac-homebrew-recipes" / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"file": target.relative_to(directory).as_posix(),
                       "sha256": record["archived_sha256"], "bytes": record["archived_bytes"],
                       "captured_original_sha256": record["captured_original_sha256"],
                       "redactions": record["redactions"]})
    for name in ("capture-manifest.json", "README.md"):
        source = recipe_root / name
        target = directory / "mac-homebrew-recipes" / name
        shutil.copy2(source, target)
        copied.append({"file": target.relative_to(directory).as_posix(), "sha256": sha256(target), "bytes": target.stat().st_size})
    return copied


def store_file(collector, component, url, expected, *, extension, recipe):
    cached, receipt = fetch(url, collector.cache, safe_name(component + "-" + expected[:16]) + extension, expected)
    target = collector.directory / "third-party" / cached.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, target)
    collector.records.append({"component": component, "revision": expected, "license": "See pinned recipe/upstream terms",
                              "recipe": recipe, "file": target.relative_to(collector.directory).as_posix(), **receipt})
    print("Collected " + component + " " + str(receipt["bytes"]) + " bytes", flush=True)


def auxiliary_git_urls(item):
    urls = git_archive_urls(item["url"], item["git_revision"])
    if item.get("source_archive_url") not in urls:
        raise ValueError("Auxiliary archive URL differs from captured repository/ref")
    return urls


def collect_auxiliary(collector, recipe_root):
    plan = json.loads(json.dumps(MAC_HOMEBREW_AUXILIARY_PLAN))
    # Every auxiliary URL/ref/hash is checked against the exact saved recipe.
    for key in ("required_resources", "required_remote_patches", "required_local_patches"):
        for item in plan[key]:
            rel = PurePosixPath(item["recipe_zip_path"])
            recipe = recipe_root.joinpath(*rel.parts[1:])
            body = recipe.read_text()
            if item["kind"] == "resource":
                if item["url"] not in body or item.get("sha256", item.get("git_revision")) not in body:
                    raise ValueError("Auxiliary source does not match captured recipe")
            elif key == "required_remote_patches":
                if item["url"] not in body or item["sha256"] not in body:
                    raise ValueError("Patch does not match captured recipe")
            else:
                api = json.loads((recipe.parent.parent / "cached-api-entry.json").read_text())
                if api.get("local_patch_checksums", {}).get(item["file"]) != item["sha256"]:
                    raise ValueError("Local patch checksum is not in captured API data")
            component = "mac-homebrew-extra-" + item["formula"] + "-" + item["name"]
            metadata = rel.as_posix() + ":" + str(item["recipe_line"])
            if item.get("git_revision"):
                urls = auxiliary_git_urls(item)
                collector.add(component, urls, revision=item["git_revision"],
                              repository=item["url"], license="See full included upstream notices", recipe=metadata)
            elif item["kind"] == "resource" and not item["url"].endswith(".traineddata"):
                collector.add(component, [item["url"]], revision=item["sha256"][:16], expected=item["sha256"],
                              license="See full included upstream notices", recipe=metadata)
            else:
                try:
                    store_file(collector, component, item["url"], item["sha256"],
                               extension=".traineddata" if item["url"].endswith(".traineddata") else ".patch", recipe=metadata)
                except (OSError, ValueError) as error:
                    collector.missing.append({"component": component, "reason": str(error)[:240]})
    # Preserve the selection/exclusion reasoning beside the original recipes.
    (collector.directory / "mac-homebrew-auxiliary-plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def cargo_registry_inputs(raw):
    crates = []
    for item in tomllib.loads(raw.decode())["package"]:
        if "source" not in item:
            continue
        if item["source"] != "registry+https://github.com/rust-lang/crates.io-index" or not re.fullmatch(r"[0-9a-f]{64}", item.get("checksum", "")):
            raise ValueError("Unpinned/unsupported Cargo source")
        name, version = item["name"], item["version"]
        if safe_name(name) != name or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,99}", version):
            raise ValueError("Unsafe Cargo source identity")
        url = "https://static.crates.io/crates/" + name + "/" + name + "-" + version + ".crate"
        crates.append({"name": name, "version": version, "sha256": item["checksum"], "url": url})
    if len({safe_name(item["name"] + "-" + item["version"]) for item in crates}) != len(crates):
        raise ValueError("Cargo identities collide after cache filename normalization")
    return crates


def collect_crates(collector):
    source = collector.cache / "mac-homebrew-rav1e-0.8.1.tar.gz"
    raw = tar_members(source)["Cargo.lock"]
    if hashlib.sha256(raw).hexdigest() != "861482385bdef579e207bb371937dd49cb9ff47d9406c40660b760fa672d0589":
        raise ValueError("Unexpected rav1e Cargo.lock")
    crates = cargo_registry_inputs(raw)
    if len(crates) != 271:
        raise ValueError("Unexpected rav1e registry source count")
    for item in crates:
        path, receipt = fetch(item["url"], collector.cache, "mac-rav1e-crate-" + item["name"] + "-" + item["version"] + ".crate", item["sha256"])
        collector.store("mac-rav1e-crate-" + item["name"], path, receipt, item["version"], "See included per-crate upstream notices", "rav1e0.8.1/Cargo.lock checksum")
    (collector.directory / "mac-rav1e-crates.json").write_text(json.dumps({"scope": "Full lockfile superset, including optional/dev/platform inputs; not a claim all crates were linked", "crates": crates}, indent=2) + "\n", encoding="utf-8")


def collect_macos_sources(args) -> int:
    output = output_path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    directory = output / "contents"; directory.mkdir()
    collector = Collector(directory)
    inventory_path = Path(args.mac_native_inventory)
    inventory_bytes = inventory_path.read_bytes()
    inventory = json.loads(inventory_bytes)
    if inventory.get("architecture") != "arm64" or hashlib.sha256(inventory_bytes).hexdigest() != MAC_NATIVE_INVENTORY_SHA256:
        raise ValueError("Expected the exact reviewed arm64 release inventory; new versions require a new recipe/resource review")
    (directory / "mac-native-dependencies.json").write_bytes(inventory_bytes)
    formula_records = []
    for formula in inventory["formulae"]:
        name = formula["formula"]; source = formula["source_archive"]
        version = formula["installed_package_version"]
        url = source["url_args"][0]
        license = "\n".join(line["text"].strip() for line in formula["source_declarations_from_saved_recipe"] if line["text"].lstrip().startswith("license "))
        recipe = "Homebrew saved formula " + name + " " + version
        if source["source_kind"] == "pinned_git":
            revision = source.get("git_revision")
            if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
                collector.missing.append({"component": name, "reason": "Missing exact Git source revision"})
                continue
            collector.add("mac-homebrew-" + name, git_archive_urls(url, revision), revision=revision,
                          license=license or "See original source notices", recipe=recipe, repository=url)
        else:
            expected = source.get("sha256")
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                collector.missing.append({"component": name, "reason": "Missing exact source archive checksum"})
                continue
            collector.add("mac-homebrew-" + name, [url], revision=version, expected=expected,
                          license=license or "See original source notices", recipe=recipe)
        formula_records.append({"formula": name, "version": version, "source": source})
    # These are distinct libraries from the Homebrew subprocess media tools.
    collector.add("pyav", [PYAV_SDIST["url"]], revision="17.0.0", license="BSD-3-Clause", expected=PYAV_SDIST["sha256"])
    collect_macos_pyav(collector, native_pending=False)
    collect_electron_sources(collector)
    recipes = []
    if args.mac_recipe_directory:
        recipe_root = Path(args.mac_recipe_directory).resolve()
        recipes = capture_recipes(directory, inventory_bytes, recipe_root)
        collect_auxiliary(collector, recipe_root)
        collect_crates(collector)
    else:
        collector.missing.append({"component": "mac-homebrew-recipes", "reason": "Provide --mac-recipe-directory with the verified314-member captured formulae/ directory; it supplies required recipe/resource/patch provenance"})
    app_files = snapshot_application(directory / "application")
    manifest = {"schema": 1, "platform": "macos-arm64", "application_revision": args.application_revision,
                "runtime_code_revision": args.runtime_code_revision,
                "native_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
                "sources": collector.records, "homebrew_main_inputs": formula_records, "homebrew_saved_files": recipes,
                "application_files": app_files, "unresolved": collector.missing,
                "scope": "Actual Mac native source inputs, original PyAV vendor sources, Electron media sources, application snapshot,314 captured recipe/receipt files,18 auxiliary recipe inputs and the271-crate rav1e lockfile superset. Optional/dev crates are included without claiming every crate was linked. Build scripts are preserved; byte-identical binary rebuilding is not claimed.",
                "source_coverage": "incomplete" if collector.missing else "listed source inputs collected; final release review required"}
    (directory / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    archive = create_archive(directory, output / "8k-editor-macos-corresponding-source.tar.gz")
    (output / "SHA256SUMS").write_text(archive["sha256"] + "  " + archive["file"] + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({**archive, "source_count": len(collector.records), "unresolved": collector.missing}, indent=2))
    return 2 if collector.missing else 0


def selected_recipes(inventory: list[dict], configuration: str) -> list[dict]:
    generic = {"--enable-static", "--enable-shared", "--enable-pic", "--enable-pthreads", "--enable-maintainer-mode"}
    flags = set(re.findall(r"--enable-[A-Za-z0-9_-]+", configuration)) - generic
    selected = {item["recipe"] for item in inventory if flags.intersection(item["configure_flags"])}
    # Include each selected recipe's named static-library/build dependencies.
    excluded_build_groups = {"base", "x11", "vaapi", "pulseaudio", "mingw", "xorg-macros"}
    while True:
        previous = set(selected)
        dependencies = {name for item in inventory if item["recipe"] in selected for name in item.get("dependencies", [])}
        groups = {str(PurePosixPath(path).parent) for path in selected if str(PurePosixPath(path).parent) != "scripts.d"}
        for item in inventory:
            tail = item["recipe"][len("scripts.d/"):]
            group = tail.split("/", 1)[0].removesuffix(".sh").split("-", 1)[-1]
            if group in dependencies - excluded_build_groups or str(PurePosixPath(item["recipe"]).parent) in groups:
                selected.add(item["recipe"])
        if selected == previous:
            break
    return [item for item in inventory if item["recipe"] in selected]


def collect_native_notices(args) -> int:
    """Collect exact Git blob notice bytes, without downloading large sources."""
    if not args.native_config:
        raise ValueError("--collect-native-notices requires exact --native-config")
    source = OUT / "cache" / ("btbn-build-recipes-" + BTBN_REV + ".tar.gz")
    inventory = recipe_inventory(tar_members(source))
    selected = selected_recipes(inventory, Path(args.native_config).read_text(encoding="utf-8"))
    destination = ROOT / "packaging/notices/ffmpeg-windows"
    destination.mkdir(parents=True, exist_ok=True)
    collected, missing = [], []
    # Existing complete sources supply notices for the LGPL/MPL dependencies.
    source_manifest = Path(args.source_manifest or OUT / "windows-lgpl8-second/contents/SOURCE-MANIFEST.json")
    full_sources = json.loads(source_manifest.read_text(encoding="utf-8"))
    sources_by_recipe = {item["recipe"]: item for item in full_sources["sources"] if item.get("recipe")}
    mirror = {
        "https://gitlab.freedesktop.org/freetype/freetype.git": "freetype/freetype",
        "https://gitlab.freedesktop.org/fontconfig/fontconfig.git": "fontconfig/fontconfig",
        "https://aomedia.googlesource.com/aom": "mozilla/aom",
        "https://code.videolan.org/videolan/dav1d.git": "videolan/dav1d",
        "https://code.videolan.org/rist/librist.git": "rist/librist",
        "https://chromium.googlesource.com/webm/libvpx": "webmproject/libvpx",
        "https://chromium.googlesource.com/webm/libwebp": "webmproject/libwebp",
        "https://git.code.sf.net/p/opencore-amr/code": "BelledonneCommunications/opencore-amr",
        "https://gitlab.com/AOMediaCodec/SVT-AV1.git": "AOMediaCodec/SVT-AV1",
    }
    def write_identical(path: Path, data: bytes):
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("Refusing to replace a different pinned notice: " + path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    def api(repository, path):
        cache_path = OUT / "cache/notice-api" / (hashlib.sha256((repository + "/" + path).encode()).hexdigest() + ".json")
        if cache_path.is_file():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        result = subprocess.run(["gh", "api", "repos/" + repository + "/" + path],
                                cwd=ROOT, check=True, capture_output=True, timeout=45)
        if len(result.stdout) > 16 * 1024 * 1024:
            raise ValueError("Notice-tree metadata exceeds bound")
        data = json.loads(result.stdout)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(result.stdout)
        return data
    notice_pattern = re.compile(r"(?i)^(licen[sc]e|copying|notice|copyright|authors|patents)([._-]|$)")
    for item in selected:
        name = item["recipe"][len("scripts.d/"):-3]
        target = destination / safe_name(name)
        entry = {"component": name, "upstream_repository": item["repository"], "revision": item["revision"], "recipe": item["recipe"], "files": []}
        if item["recipe"] in sources_by_recipe:
            original = source_manifest.parent / "notices" / safe_name(name)
            for path in original.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(original)
                    data = path.read_bytes()
                    write_identical(target / relative, data)
                    entry["files"].append({"file": relative.as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "source": "Exact source archive; see corresponding-source manifest"})
        else:
            repository = item["repository"].removesuffix(".git")
            github = repository[len("https://github.com/"):] if repository.startswith("https://github.com/") else mirror.get(item["repository"])
            try:
                if not github:
                    raise ValueError("No exact source notice endpoint mapped")
                tree = api(github, "git/trees/" + item["revision"])
                entry["notice_mirror"] = "https://github.com/" + github
                entry["tree_sha"] = tree["sha"]
                blobs = [blob for blob in tree["tree"] if blob["type"] == "blob" and notice_pattern.match(blob["path"])]
                if name == "50-ffnvcodec":
                    # This header-only project puts its complete license grants
                    # in each public header instead of a root LICENSE file.
                    headers = api(github, "git/trees/" + item["revision"] + "?recursive=1")
                    blobs += [blob for blob in headers["tree"] if blob["type"] == "blob" and blob["path"].startswith("include/ffnvcodec/") and blob["path"].endswith(".h")]
                # OpenMPT publishes dependency notices in a dedicated directory.
                for subtree in tree["tree"]:
                    if subtree["type"] == "tree" and subtree["path"].lower() in {"licenses", "licences"}:
                        extra = api(github, "git/trees/" + subtree["sha"] + "?recursive=1")
                        blobs += [{**blob, "path": subtree["path"] + "/" + blob["path"]} for blob in extra["tree"] if blob["type"] == "blob"]
                for blob in blobs:
                    if blob.get("size", 0) > 2 * 1024 * 1024:
                        raise ValueError("Unexpectedly large license text")
                    url = "https://raw.githubusercontent.com/" + github + "/" + item["revision"] + "/" + blob["path"]
                    cached, receipt = fetch(url, OUT / "cache/notice-text", safe_name(name) + "-" + hashlib.sha256(url.encode()).hexdigest()[:16] + ".txt")
                    data = cached.read_bytes()
                    actual_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                    if actual_blob != blob["sha"]:
                        raise ValueError("License text does not match pinned Git blob")
                    write_identical(target / Path(blob["path"]), data)
                    entry["files"].append({"file": blob["path"], "sha256": receipt["sha256"], "git_blob": blob["sha"], "source": url})
            except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
                # Some canonical projects have no GitHub mirror. Fetch their
                # small exact source snapshot only to preserve actual notices.
                try:
                    cached = receipt = None
                    for url in git_archive_urls(item["repository"], item["revision"]):
                        try:
                            cached, receipt = fetch(url, OUT / "cache", safe_name(name + "-notice-source-" + item["revision"]) + ".tar.gz")
                            break
                        except (OSError, ValueError):
                            continue
                    if cached is None:
                        cached, receipt = fetch_git(item["repository"], item["revision"], OUT / "cache", name + "-notice-source")
                    files = tar_members(cached)
                    for filename, data in files.items():
                        if notice_pattern.match(PurePosixPath(filename).name):
                            write_identical(target / Path(filename), data)
                            entry["files"].append({"file": filename, "sha256": hashlib.sha256(data).hexdigest(), "source": receipt["url"]})
                    entry["notice_source_archive"] = receipt
                except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
                    missing.append({"component": name, "revision": item["revision"], "reason": type(error).__name__ + ": pinned upstream notice collection failed"})
        if not entry["files"]:
            missing.append({"component": name, "revision": item["revision"], "reason": "No upstream copyright/license notice captured"})
        collected.append(entry)
        print("Notices " + name + " " + str(len(entry["files"])), flush=True)
    record = {"schema": 1, "btbn_revision": BTBN_REV, "binary": BTBN_BINARY, "components": collected, "unresolved": missing,
              "scope": "Pinned top-level/component license notices. Additional bundled subcomponent notices require source/dependency review; original source archive notices remain in the corresponding-source bundle."}
    (destination / "NOTICE-MANIFEST.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"components": len(collected), "files": sum(len(item["files"]) for item in collected), "unresolved": missing}, indent=2))
    return 2 if missing else 0


def self_test() -> int:
    import unittest

    class Tests(unittest.TestCase):
        def test_scope(self):
            with self.assertRaises(ValueError): output_path(ROOT / "data")
            with self.assertRaises(ValueError): output_path(OUT)
            self.assertEqual(output_path(OUT / "fixture"), (OUT / "fixture").resolve())

        def test_recipe_literals(self):
            inventory = recipe_inventory({"scripts.d/20-libiconv.sh": b'SCRIPT_REPO="https://example.com/lib.git"\nSCRIPT_COMMIT="abc"\necho --enable-iconv\n'})
            self.assertEqual(inventory[0]["revision"], "abc")
            self.assertEqual(inventory[0]["configure_flags"], ["--enable-iconv"])

        def test_mac_auxiliary_archive_bound_to_repository_revision(self):
            item = next(item for item in MAC_HOMEBREW_AUXILIARY_PLAN["required_resources"] if item.get("git_revision"))
            self.assertEqual(auxiliary_git_urls(item), [item["source_archive_url"]])
            with self.assertRaises(ValueError):
                auxiliary_git_urls({**item, "source_archive_url": "https://codeload.github.com/other/project/tar.gz/" + item["git_revision"]})

        def test_mac_cargo_exact_urls_checksums_and_semver_metadata(self):
            versions = ("0.7.1+libdav1d.1.4.3", "0.18.1+1.9.0", "0.11.1+wasi-snapshot-preview1", "0.14.2+wasi-0.2.4")
            raw = "\n".join('[[package]]\nname="crate' + str(i) + '"\nversion="' + version + '"\nsource="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="' + "a" * 64 + '"' for i, version in enumerate(versions)).encode()
            inputs = cargo_registry_inputs(raw)
            self.assertEqual([item["version"] for item in inputs], list(versions))
            for item in inputs:
                self.assertEqual(item["url"], "https://static.crates.io/crates/" + item["name"] + "/" + item["name"] + "-" + item["version"] + ".crate")
                self.assertNotIn("+", safe_name(item["name"] + "-" + item["version"]))
            for changed in (raw.replace(b"registry+https://github.com/rust-lang/crates.io-index", b"git+https://example.com/unpinned"),
                            raw.replace(b"a" * 64, b"g" * 64), raw.replace(b'"crate0"', b'"../unsafe"')):
                with self.assertRaises(ValueError):
                    cargo_registry_inputs(changed)

        def test_mac_capture_original_and_redacted_hashes(self):
            with tempfile.TemporaryDirectory() as value:
                root = Path(value); recipes = root / "recipes"; recipes.mkdir()
                relative = "formulae/fixture/1/INSTALL_RECEIPT.json"
                receipt_path = recipes / relative; receipt_path.parent.mkdir(parents=True)
                receipt = b'{"source":{"path":"<user-home>/Library/Caches/Homebrew/api.json"}}'
                receipt_path.write_bytes(receipt)
                archived = hashlib.sha256(receipt).hexdigest()
                inventory = json.dumps({"formulae": [{"saved_files": [{"saved": relative, "sha256": "b" * 64}]}]}).encode()
                manifest = {"published_summary_binding": {"sha256": hashlib.sha256(inventory).hexdigest()}, "files": [{
                    "path": relative, "kind": "sanitized_install_receipt", "captured_original_sha256": "b" * 64,
                    "archived_sha256": archived, "archived_bytes": len(receipt), "byte_identical_to_captured_original": False,
                    "redactions": [{"field": "/source/path", "sanitized_value": "<user-home>/Library/Caches/Homebrew/api.json"}]}]}
                (recipes / "capture-manifest.json").write_text(json.dumps(manifest))
                (recipes / "README.md").write_text("fixture")
                files = capture_recipes(root / "output", inventory, recipes)
                self.assertEqual(files[0]["captured_original_sha256"], "b" * 64)
                self.assertEqual(files[0]["sha256"], archived)
                receipt_path.write_bytes(receipt + b"corrupt")
                with self.assertRaises(ValueError): capture_recipes(root / "corrupted", inventory, recipes)
                receipt_path.write_bytes(receipt)
                manifest["files"][0]["captured_original_sha256"] = "c" * 64
                (recipes / "capture-manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ValueError): capture_recipes(root / "wrong-original", inventory, recipes)
                for path in ("../escape", "C:/private", "/absolute", "folder\\escape"):
                    with self.assertRaises(ValueError): safe_relative(path)

        def test_selected_recipe_dependency_groups(self):
            items = [
                {"recipe": "scripts.d/50-a/50-main.sh", "configure_flags": ["--enable-liba"], "dependencies": ["shared"]},
                {"recipe": "scripts.d/50-a/40-helper.sh", "configure_flags": [], "dependencies": []},
                {"recipe": "scripts.d/25-shared.sh", "configure_flags": ["--enable-static"], "dependencies": []},
                {"recipe": "scripts.d/50-gpl.sh", "configure_flags": ["--enable-libx264"], "dependencies": []},
            ]
            selected = {item["recipe"] for item in selected_recipes(items, "--enable-liba --disable-libx264 --enable-static")}
            self.assertEqual(selected, {item["recipe"] for item in items[:3]})

        def test_native_receipt_identity_license_and_privacy(self):
            good = {"source_commit": FFMPEG_REV, "component": "avcodec-62.dll", "output_sha256": "a" * 64,
                    "output_bytes": 123, "library_license": "LGPL version 3 or later",
                    "library_configuration": "--disable-gpl --disable-nonfree --disable-chromaprint --disable-libzvbi",
                    "source_url": "https://github.com/FFmpeg/FFmpeg"}
            self.assertEqual(validate_native_receipt(good), good)
            for change in ({"source_commit": "b" * 40}, {"component": "other.dll"}, {"output_sha256": "bad"},
                           {"output_bytes": 0}, {"library_license": "GPL version 3 or later"},
                           {"library_configuration": "--enable-chromaprint"}, {"private": "C:/Users/private/source"}):
                with self.assertRaises(ValueError): validate_native_receipt({**good, **change})

        def test_safe_archive(self):
            with tempfile.TemporaryDirectory() as value:
                path = Path(value) / "input.tar.gz"
                with tarfile.open(path, "w:gz") as archive:
                    member = tarfile.TarInfo("root/../../outside")
                    member.size = 1
                    archive.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError): tar_members(path)

        def test_gitiles_root_layout(self):
            with tempfile.TemporaryDirectory() as value:
                path = Path(value) / "root.tar.gz"
                with tarfile.open(path, "w:gz") as archive:
                    for name in ("LICENSE", "lib/source.c"):
                        member = tarfile.TarInfo(name); member.size = 1
                        archive.addfile(member, io.BytesIO(b"x"))
                self.assertEqual(tar_members(path), {"LICENSE": b"x", "lib/source.c": b"x"})

        def test_reproducible_archive_and_content(self):
            with tempfile.TemporaryDirectory() as value:
                root = Path(value); content = root / "content"; content.mkdir()
                (content / "source.py").write_bytes(b"print('fixture')\n")
                one = create_archive(content, root / "one.tar.gz")
                os.utime(content / "source.py", (12345, 12345))
                two = create_archive(content, root / "two.tar.gz")
                self.assertEqual(one["sha256"], two["sha256"])
                self.assertEqual(tar_members(root / "one.tar.gz")["source.py"], b"print('fixture')\n")
                with self.assertRaises(FileExistsError): create_archive(content, root / "one.tar.gz")

        def test_cache_tampering_refused(self):
            with tempfile.TemporaryDirectory() as value:
                cache = Path(value); file = cache / "fixture.tar.gz"; file.write_bytes(b"bad")
                (cache / "fixture.tar.gz.receipt.json").write_text(json.dumps({"url": "https://example.com/source", "sha256": "0" * 64}))
                with self.assertRaises(ValueError): fetch("https://example.com/source", cache, file.name)
                self.assertEqual(file.read_bytes(), b"bad")
                with self.assertRaises(ValueError): fetch("http://example.com/source", cache, "other")

        def test_download_hash_and_preexisting_partial_preserved(self):
            from unittest.mock import patch
            with tempfile.TemporaryDirectory() as value:
                cache = Path(value)
                (cache / "fixture.part").write_bytes(b"prior interrupted work")
                with patch("urllib.request.urlopen", return_value=io.BytesIO(b"download")):
                    with self.assertRaises(FileExistsError): fetch("https://example.com/source", cache, "fixture")
                self.assertEqual((cache / "fixture.part").read_bytes(), b"prior interrupted work")
                with patch("urllib.request.urlopen", return_value=io.BytesIO(b"download")):
                    with self.assertRaises(ValueError): fetch("https://example.com/source", cache, "new", "0" * 64)
                self.assertFalse((cache / "new").exists())
                self.assertFalse((cache / "new.part").exists())

    return 0 if unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests)).wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="Fetch sources serially and create a new archive")
    parser.add_argument("--self-test", action="store_true", help="Run offline fixture tests, no downloads")
    parser.add_argument("--collect-native-notices", action="store_true", help="Collect pinned native component notices into packaging/notices/ffmpeg-windows")
    parser.add_argument("--collect-macos-sources", action="store_true", help="Collect exact source inputs from the actual Mac native inventory")
    parser.add_argument("--mac-native-inventory", default="docs/validation/mac-native-dependencies.json")
    parser.add_argument("--mac-recipe-directory", help="Captured original Mac source-provenance directory containing formulae/")
    parser.add_argument("--source-manifest", help="Existing source manifest supplies already collected copyleft notices")
    parser.add_argument("--output", default=str(OUT / "windows-lgpl8"))
    parser.add_argument("--application-revision", default="working-tree-uncommitted")
    parser.add_argument("--runtime-code-revision", help="Separate compiled runtime source revision; documentation/collector snapshots can be newer")
    parser.add_argument("--native-config", help="Captured -buildconf output from the exact pinned BtbN binary")
    parser.add_argument("--native-override-receipt", action="append", default=[], help="Public exact-source rebuild receipt; repeat for avformat and avcodec")
    parser.add_argument("--include-macos-pyav", action="store_true", help="Also collect PyAV17's original vendor sources for a separate Mac build")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.collect_native_notices:
        return collect_native_notices(args)
    if args.collect_macos_sources:
        return collect_macos_sources(args)
    if not args.collect:
        print(json.dumps({"fetches_sources": False, "btbn_build_recipe": BTBN_REV, "ffmpeg": FFMPEG_REV,
                          "windows_binary": BTBN_BINARY, "copyleft_recipe_inputs": COPYLEFT,
                          "next": "Pass --collect; incomplete source coverage exits 2 and remains explicitly listed"}, indent=2))
        return 0
    return collect(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print("Source collection failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
