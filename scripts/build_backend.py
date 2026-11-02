"""Build a native, self-contained PyInstaller onedir backend. GPL-2.0-only.

Run in the existing application venv after building app/dist. This script never
installs packages or downloads binaries, and never starts a persistent server.
Windows retains the environment's CUDA-enabled Torch. macOS recursively bundles
and relocates FFmpeg's non-system dylibs instead of depending on Homebrew paths.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PYINSTALLER_VERSION = "6.22.3"


def command(args, timeout=60):
    result = subprocess.run([str(a) for a in args], check=True, capture_output=True, timeout=timeout, text=True)
    return result.stdout


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while data := stream.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def raw_executable(path):
    path = Path(path).resolve(strict=True)
    if os.name == "nt" and path.stat().st_size < 1024 * 1024:
        # Chocolatey's shim documents this switch and does not run its target.
        # ShimGen's help may return -1 even though it successfully describes
        # its target. Only the parsed target's real version probe is trusted.
        info_result = subprocess.run([str(path), "--shimgen-help"], capture_output=True, timeout=15, text=True)
        info = info_result.stdout + info_result.stderr
        match = re.search(r"Target: '([^']+)'", info)
        if match:
            path = Path(match.group(1)).resolve(strict=True)
        else:
            raise ValueError("Provide the real static FFmpeg bin directory with --ffmpeg-dir; a small wrapper/shim cannot be bundled")
    return path


def locate_tools(directory=None):
    suffix = ".exe" if os.name == "nt" else ""
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        candidate = Path(directory) / (name + suffix) if directory else shutil.which(name)
        if not candidate:
            raise ValueError(f"{name} is missing; provide --ffmpeg-dir with native full-build binaries")
        tools[name] = raw_executable(candidate)
        output = command([tools[name], "-hide_banner", "-version"])
        if not output.startswith(name + " version"):
            raise ValueError("Selected native media executable failed its version identity")
    listing = command([tools["ffmpeg"], "-hide_banner", "-filters"])
    filters = {parts[1] for line in listing.splitlines() if len(parts := line.split()) >= 3}
    missing = {"zscale", "tonemap"} - filters
    if missing:
        raise ValueError("Desktop FFmpeg must include HDR preview filters: " + ", ".join(sorted(missing)))
    return tools


def license_files(directory, target):
    if not directory.is_dir():
        return
    for item in directory.iterdir():
        if item.is_file() and re.match(r"(?i)^(license|licence|copying|copyright|notice)", item.name):
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target / item.name)


def python_notices(target):
    packages = {}
    for name in ("torch", "av", "numpy", "Pillow", "fastapi", "starlette", "uvicorn", "pydantic", "pydantic_core",
                 "python-multipart", "httpx", "httpcore", "anyio", "annotated-doc", "typing_extensions", "filelock",
                 "fsspec", "jinja2", "sympy", "networkx", "mpmath", "h11", "click", "idna", "certifi"):
        distribution = importlib.metadata.distribution(name)
        packages[name] = dict(version=distribution.version,
                              project_urls=distribution.metadata.get_all("Project-URL", []))
        for item in distribution.files or []:
            relative = str(item).replace("\\", "/")
            if ".dist-info/" in relative and any(word in relative.lower() for word in ("license", "licence", "copying", "notice")):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    output = target / "python" / name / Path(relative.split(".dist-info/", 1)[1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, output)
    (target / "python-distributions.json").write_text(json.dumps(packages, indent=2), encoding="utf-8")


def mac_dependencies(binary):
    lines = command(["/usr/bin/otool", "-L", binary]).splitlines()[1:]
    return [line.strip().split(" (", 1)[0] for line in lines if " (" in line]


def mac_rpaths(binary):
    lines = command(["/usr/bin/otool", "-l", binary]).splitlines()
    paths = []
    active = False
    for line in lines:
        if line.strip() == "cmd LC_RPATH":
            active = True
        elif active and line.strip().startswith("path "):
            paths.append(line.strip()[5:].split(" (offset", 1)[0])
            active = False
    return paths


def resolve_mac_dependency(name, source, executable, inherited_rpaths=()):
    def expand(value):
        return value.replace("@loader_path", str(source.parent)).replace("@executable_path", str(executable.parent))
    if name.startswith("@rpath/"):
        tail = name[len("@rpath/"):]
        choices = [Path(expand(path)) / tail for path in (*mac_rpaths(source), *inherited_rpaths)]
        choices.append(source.parent / tail)
    else:
        choices = [Path(expand(name))]
    for candidate in choices:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(f"Cannot resolve bundled media dependency {name} required by {source.name}")


def stage_mac(tools, stage):
    binaries, mapping, edges = [], {}, {}
    for name, source in tools.items():
        target = stage / "bin" / name
        mapping[source] = target
        paths = tuple(path.replace("@loader_path", str(source.parent)).replace("@executable_path", str(source.parent)) for path in mac_rpaths(source))
        binaries.append((source, source, paths))
    index = 0
    while index < len(binaries):
        source, executable, inherited = binaries[index]
        index += 1
        edges[source] = []
        for dependency in mac_dependencies(source):
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            resolved = resolve_mac_dependency(dependency, source, executable, inherited)
            # A dylib's LC_ID_DYLIB appears in otool -L as its first entry.
            if resolved == source:
                continue
            if resolved not in mapping:
                mapping[resolved] = stage / "ffmpeg-libs" / (digest(resolved)[:12] + "-" + resolved.name)
                own_paths = tuple(path.replace("@loader_path", str(source.parent)).replace("@executable_path", str(executable.parent)) for path in mac_rpaths(source))
                binaries.append((resolved, executable, (*own_paths, *inherited)))
            edges[source].append((dependency, resolved))
    for source, target in mapping.items():
        architecture = command(["/usr/bin/lipo", "-archs", source]).strip().split()
        if "arm64" not in architecture:
            raise ValueError(f"Media dependency {source.name} has no arm64 architecture")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode | 0o200)
    for source, target in mapping.items():
        for dependency, resolved in edges[source]:
            relative = os.path.relpath(mapping[resolved], target.parent).replace(os.sep, "/")
            command(["/usr/bin/install_name_tool", "-change", dependency, "@loader_path/" + relative, target])
        if target.suffix == ".dylib":
            command(["/usr/bin/install_name_tool", "-id", "@loader_path/" + target.name, target])
        for path in mac_rpaths(source):
            if path.startswith("/") and not path.startswith(("/usr/lib/", "/System/Library/")):
                command(["/usr/bin/install_name_tool", "-delete_rpath", path, target])
        command(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", target])
        for dependency in mac_dependencies(target):
            if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                raise ValueError(f"Unrelocated external dependency remains: {dependency}")
        for ancestor in source.parents:
            if ancestor.parent.name == "Cellar":
                # Homebrew formula directories contain version children.
                continue
            if ancestor.parent.parent.name == "Cellar":
                license_files(ancestor, stage / "licenses" / "native" / ancestor.parent.name)
                break
    environment = dict(os.environ, PATH="/usr/bin:/bin", DYLD_LIBRARY_PATH="", DYLD_FALLBACK_LIBRARY_PATH="")
    for name in tools:
        subprocess.run([str(stage / "bin" / name), "-hide_banner", "-version"], env=environment,
                       check=True, capture_output=True, timeout=30)
    return [dict(file=str(target.relative_to(stage)), sha256=digest(target), source_name=source.name) for source, target in mapping.items()]


def stage_windows(tools, stage):
    outputs = []
    for name, source in tools.items():
        if source.stat().st_size < 5 * 1024 * 1024:
            raise ValueError("Use a full static Windows FFmpeg build; wrappers/small dynamically linked frontends are unsupported")
        target = stage / "bin" / (name + ".exe")
        shutil.copy2(source, target)
        outputs.append(dict(file="bin/" + target.name, sha256=digest(target), source_name=source.name))
        for directory in (source.parent, source.parent.parent, source.parent.parent.parent):
            license_files(directory, stage / "licenses" / "native" / "ffmpeg")
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-dir", type=Path, help="Native full-build bin directory; never a Chocolatey shim directory")
    parser.add_argument("--plan", action="store_true", help="Inspect dependencies and paths without copying or building")
    args = parser.parse_args()
    system = platform.system()
    if system not in ("Windows", "Darwin") or system == "Darwin" and platform.machine().lower() != "arm64":
        raise ValueError("Build natively on 64-bit Windows or arm64 Apple Silicon macOS")
    if sys.maxsize <= 2**32:
        raise ValueError("A native 64-bit Python environment is required")
    if importlib.metadata.version("pyinstaller") != PYINSTALLER_VERSION:
        raise ValueError("Build with the reviewed pyinstaller==" + PYINSTALLER_VERSION)
    if not (ROOT / "app/dist/index.html").is_file():
        raise ValueError("Build app/dist with npm run build before packaging")
    import torch
    import av
    tools = locate_tools(args.ffmpeg_dir)
    target = "win32" if system == "Windows" else "darwin"
    destination, stage = ROOT / "build/backend" / target, ROOT / "build/native-stage" / target
    details = dict(platform=target, architecture=platform.machine(), pyinstaller=PYINSTALLER_VERSION,
                   python=sys.version.split()[0], torch=torch.__version__, cuda_runtime=torch.version.cuda,
                   av=av.__version__, tools={name: str(path) for name, path in tools.items()}, output=str(destination))
    print(json.dumps(details, indent=2), flush=True)
    if args.plan:
        return 0
    if destination.exists() or stage.exists():
        raise FileExistsError("Build destination/staging already exists; preserve or explicitly remove the prior build before retrying")
    required_free = (12 if system == "Windows" else 3) * 1024**3
    if shutil.disk_usage(ROOT).free < required_free:
        raise ValueError("Insufficient free space for native staging and onedir output")
    (stage / "bin").mkdir(parents=True)
    (stage / "licenses").mkdir()
    shutil.copy2(ROOT / "LICENSE", stage / "licenses/PROJECT-COPYING")
    python_notices(stage / "licenses")
    native = stage_windows(tools, stage) if system == "Windows" else stage_mac(tools, stage)
    (stage / "licenses/native-binaries.json").write_text(json.dumps(native, indent=2), encoding="utf-8")
    environment = dict(os.environ, EDITOR_NATIVE_STAGE=str(stage))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(ROOT / "build/backend"),
                    "--workpath", str(ROOT / "build/pyinstaller-work" / target), str(ROOT / "packaging/backend.spec")],
                   cwd=ROOT, env=environment, check=True)
    if system == "Darwin":
        for path in (destination / "_internal/bin").iterdir():
            path.chmod(path.stat().st_mode | 0o111)
    executable = destination / ("8k-editor-backend.exe" if system == "Windows" else "8k-editor-backend")
    result = command([executable, "--self-test"], timeout=180)
    receipt = json.loads(result)
    if not receipt.get("ready") or not receipt.get("frozen") or not receipt.get("static_assets"):
        raise ValueError("Packaged runtime self-test did not validate the frozen backend/static application")
    (destination / "build-receipt.json").write_text(json.dumps(dict(build=details, self_test=receipt), indent=2), encoding="utf-8")
    print(result)
    print("Native backend assembled. Test a frozen import/render before publishing any installer.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as error:
        print("Build failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
