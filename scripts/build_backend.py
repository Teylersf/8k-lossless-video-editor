"""Build a native, self-contained PyInstaller onedir backend.

GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION.

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
import sysconfig

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


def raw_executable(path, allow_shared=False):
    path = Path(path).resolve(strict=True)
    if os.name == "nt" and path.stat().st_size < 1024 * 1024 and not allow_shared:
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


def locate_tools(directory=None, allow_shared=False):
    suffix = ".exe" if os.name == "nt" else ""
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        candidate = Path(directory) / (name + suffix) if directory else shutil.which(name)
        if not candidate:
            raise ValueError(f"{name} is missing; provide --ffmpeg-dir with native full-build binaries")
        tools[name] = raw_executable(candidate, allow_shared)
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
        license_files(Path(distribution._path) / 'licenses', target / 'python' / name / 'licenses')
    (target / "python-distributions.json").write_text(json.dumps(packages, indent=2), encoding="utf-8")
    # CPython's full notice includes historical licenses and is not a pip dist.
    python_license = next((path for path in (Path(sys.base_prefix) / 'LICENSE.txt',
        Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt', Path(sys.base_prefix) / 'Resources/LICENSE.txt')
        if path.is_file()), None)
    if python_license is None:
        raise FileNotFoundError("CPython full LICENSE.txt is required for redistribution")
    shutil.copy2(python_license, target / "CPython-LICENSE.txt")
    for name in ("react", "react-dom", "scheduler", "lucide-react"):
        directory = ROOT / "app/node_modules" / name
        if not directory.is_dir():
            raise FileNotFoundError("Frontend dependency notices missing: " + name)
        license_files(directory, target / "frontend" / name)
    notices = ROOT / "packaging/notices"
    if notices.is_dir():
        shutil.copytree(notices, target / "release-notices")


def prepare_mac_runtime(tools, stage, supplied=None, supplied_audit=None):
    from bundle_macos_ffmpeg import audit_payload, bundle
    if supplied is None:
        runtime = stage / "ffmpeg-runtime"
        audit = bundle(tools["ffmpeg"], tools["ffprobe"], runtime, arch="arm64")["audit"]
    else:
        runtime = Path(supplied).resolve(strict=True)
        audit = supplied_audit or audit_payload(runtime, arch="arm64")
    if not (runtime / "licenses").is_dir():
        raise ValueError("The private Mac FFmpeg payload must include its actual license inventory")
    native = [dict(file=item["path"], sha256=item["sha256"], source_name=Path(item["path"]).name)
              for item in audit["binaries"]]
    return runtime, audit, native


def install_mac_runtime(runtime, destination, source_audit):
    """Copy only after COLLECT, preserving the private load commands and aliases."""
    from bundle_macos_ffmpeg import audit_payload
    internal = destination / "_internal"
    if not internal.is_dir():
        raise ValueError("PyInstaller did not produce the expected _internal directory")
    copies = [(runtime / "bin", internal / "bin"), (runtime / "lib", internal / "lib"),
              (runtime / "licenses", internal / "licenses" / "native" / "ffmpeg")]
    for source, target in copies:
        if not source.is_dir():
            raise ValueError(f"Private media payload directory is missing: {source}")
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Refusing to overwrite a collected backend path: {target}")
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=True)
    for directory in (internal / "bin", internal / "lib"):
        for path in directory.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o111)
    audit = audit_payload(internal, arch="arm64", verify_signatures=True)
    before = {item["path"]: item["sha256"] for item in source_audit["binaries"]}
    after = {item["path"]: item["sha256"] for item in audit["binaries"]}
    if before != after or source_audit["symlinks"] != audit["symlinks"]:
        raise ValueError("Post-COLLECT private FFmpeg payload differs from its audited source")
    return audit


def sign_mac_frameworks(destination):
    """Seal complete frameworks after COLLECT has installed their resources."""
    if platform.system() != "Darwin":
        raise ValueError("Mac framework signing is supported only on macOS")
    destination = Path(destination).resolve(strict=True)
    if not (destination / "_internal").is_dir():
        raise ValueError("Expected the completed frozen backend _internal directory")
    magics = {bytes.fromhex(value) for value in (
        "feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}
    frameworks, binaries = [], []
    for directory, directories, files in os.walk(destination, followlinks=False):
        folder = Path(directory)
        frameworks.extend(folder / name for name in directories
                          if name.endswith(".framework") and not (folder / name).is_symlink())
        for name in files:
            path = folder / name
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                if stream.read(4) in magics:
                    binaries.append(path)
    identity = os.environ.get("EDITOR_CODESIGN_IDENTITY") or "-"
    signing_options = ["--timestamp=none"] if identity == "-" else ["--timestamp", "--options=runtime"]
    records = []
    # Each framework receives a resource seal at its final location. Nested
    # frameworks must be signed before their enclosing framework; --deep signing
    # would obscure that order and could change unrelated code signatures.
    for framework in sorted(frameworks, key=lambda path: (-len(path.parts), str(path))):
        contained = [path for path in binaries if path.is_relative_to(framework)]
        before = {str(path.relative_to(destination)): digest(path) for path in contained}
        command(["/usr/bin/codesign", "--force", "--all-architectures", "--sign", identity,
                 *signing_options, framework], timeout=120)
        command(["/usr/bin/codesign", "--verify", "--all-architectures", "--strict", "--deep", framework])
        records.append(dict(framework=str(framework.relative_to(destination)),
                            macho_sha256_before=before,
                            macho_sha256_after={str(path.relative_to(destination)): digest(path) for path in contained},
                            resource_seals={str(path.relative_to(destination)): digest(path)
                                            for path in framework.rglob("_CodeSignature/CodeResources")
                                            if path.is_file() and not path.is_symlink()},
                            strict_verification=True))
    for path in binaries:
        command(["/usr/bin/codesign", "--verify", "--all-architectures", "--strict", path])
    return dict(identity=identity, timestamp="none" if identity == "-" else "secure",
                frameworks=records, verified_macho_files=len(binaries), strict_verification=True)


def stage_windows(tools, stage, vendor_receipt):
    outputs = []
    for name, source in tools.items():
        expected = Path(vendor_receipt['ffmpeg_bin']) / (name + '.exe')
        if source != expected.resolve():
            raise ValueError("Windows executable must come from the verified LGPL FFmpeg distribution")
        target = stage / "bin" / (name + ".exe")
        shutil.copy2(source, target)
        outputs.append(dict(file="bin/" + target.name, sha256=digest(target), source_name=source.name))
        for directory in (source.parent, source.parent.parent, source.parent.parent.parent):
            license_files(directory, stage / "licenses" / "native" / "ffmpeg")
    public_keys = ('schema_version', 'archive_url', 'archive_sha256', 'archive_bytes', 'ffmpeg_source', 'pyav_version',
                   'patches', 'native_overrides', 'cli_files', 'abi_note', 'abi_policy', 'private_files')
    public = {key: vendor_receipt[key] for key in public_keys}
    (stage / 'licenses/native/ffmpeg/runtime-provenance.json').write_text(json.dumps(public, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'build/vendor-lgpl/ffmpeg-buildconf-v2.txt', stage / 'licenses/native/ffmpeg/ffmpeg-buildconf-v2.txt')
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-dir", type=Path, help="Native full-build bin directory; never a Chocolatey shim directory")
    parser.add_argument("--mac-ffmpeg-runtime", type=Path, help="Audited private Mac bin/lib/licenses payload to copy after PyInstaller")
    parser.add_argument("--plan", action="store_true", help="Inspect dependencies and paths without copying or building")
    args = parser.parse_args()
    system = platform.system()
    if system not in ("Windows", "Darwin") or system == "Darwin" and platform.machine().lower() != "arm64":
        raise ValueError("Build natively on 64-bit Windows or arm64 Apple Silicon macOS")
    if sys.maxsize <= 2**32:
        raise ValueError("A native 64-bit Python environment is required")
    if args.mac_ffmpeg_runtime is not None and system != 'Darwin':
        raise ValueError('--mac-ffmpeg-runtime is supported only on macOS')
    if importlib.metadata.version("pyinstaller") != PYINSTALLER_VERSION:
        raise ValueError("Build with the reviewed pyinstaller==" + PYINSTALLER_VERSION)
    if not (ROOT / "app/dist/index.html").is_file():
        raise ValueError("Build app/dist with npm run build before packaging")
    vendor, vendor_receipt, vendor_receipt_path = None, None, None
    if system == 'Windows':
        # Verify in a fresh process before importing av or running PyInstaller.
        verified = json.loads(command([sys.executable, ROOT / 'scripts/prepare_lgpl_av.py', '--verify-only'], timeout=180))
        vendor = Path(verified['site_packages'])
        vendor_receipt_path = Path(verified['receipt'])
        vendor_receipt = json.loads(vendor_receipt_path.read_text(encoding='utf-8'))
        sys.path.insert(0, str(vendor))
        if args.ffmpeg_dir and args.ffmpeg_dir.resolve() != Path(verified['ffmpeg_bin']).resolve():
            raise ValueError('Windows packaging only accepts the verified LGPL FFmpeg bin directory')
        args.ffmpeg_dir = Path(verified['ffmpeg_bin'])
    import torch
    import av
    supplied_runtime, supplied_audit = None, None
    if system == 'Darwin' and args.mac_ffmpeg_runtime is not None:
        from bundle_macos_ffmpeg import audit_payload
        supplied_runtime = args.mac_ffmpeg_runtime.resolve(strict=True)
        supplied_audit = audit_payload(supplied_runtime, arch='arm64', verify_signatures=True)
    tools = locate_tools(supplied_runtime / 'bin' if supplied_runtime else args.ffmpeg_dir, allow_shared=bool(vendor))
    target = "win32" if system == "Windows" else "darwin"
    destination, stage = ROOT / "build/backend" / target, ROOT / "build/native-stage" / target
    details = dict(platform=target, architecture=platform.machine(), pyinstaller=PYINSTALLER_VERSION,
                   python=sys.version.split()[0], torch=torch.__version__, cuda_runtime=torch.version.cuda,
                   av=av.__version__, tools={name: str(path) for name, path in tools.items()}, output=str(destination))
    if supplied_runtime is not None:
        details['mac_ffmpeg_runtime'] = str(supplied_runtime)
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
    shutil.copy2(ROOT / "LICENSE-EXCEPTION", stage / "licenses/PROJECT-LICENSE-EXCEPTION")
    shutil.copy2(ROOT / "NOTICE.md", stage / "licenses/PROJECT-NOTICE")
    python_notices(stage / "licenses")
    private_runtime, private_audit = None, None
    if system == 'Windows':
        native = stage_windows(tools, stage, vendor_receipt)
    else:
        private_runtime, private_audit, native = prepare_mac_runtime(tools, stage, supplied_runtime, supplied_audit)
    (stage / "licenses/native-binaries.json").write_text(json.dumps(native, indent=2), encoding="utf-8")
    environment = dict(os.environ, EDITOR_NATIVE_STAGE=str(stage))
    if system == 'Darwin':
        environment['PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR'] = '1'
    if vendor:
        environment.update(EDITOR_AV_VENDOR=str(vendor), EDITOR_AV_RECEIPT=str(vendor_receipt_path), PYTHONPATH=str(vendor) + os.pathsep + os.environ.get('PYTHONPATH', ''),
                           PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(ROOT / "build/backend"),
                    "--workpath", str(ROOT / "build/pyinstaller-work" / target), str(ROOT / "packaging/backend.spec")],
                   cwd=ROOT, env=environment, check=True)
    framework_signing = None
    if system == "Darwin":
        private_audit = install_mac_runtime(private_runtime, destination, private_audit)
        framework_signing = sign_mac_frameworks(destination)
    executable = destination / ("8k-editor-backend.exe" if system == "Windows" else "8k-editor-backend")
    result = command([executable, "--self-test"], timeout=180)
    receipt = json.loads(result)
    if not receipt.get("ready") or not receipt.get("frozen") or not receipt.get("static_assets"):
        raise ValueError("Packaged runtime self-test did not validate the frozen backend/static application")
    build_receipt = dict(build=details, self_test=receipt)
    if private_audit is not None:
        build_receipt['private_ffmpeg_audit'] = private_audit
    if framework_signing is not None:
        build_receipt['mac_framework_signing'] = framework_signing
    (destination / "build-receipt.json").write_text(json.dumps(build_receipt, indent=2), encoding="utf-8")
    print(result)
    print("Native backend assembled. Test a frozen import/render before publishing any installer.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as error:
        print("Build failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
