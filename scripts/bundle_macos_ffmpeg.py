#!/usr/bin/env python3
"""Build/audit an isolated native Mac FFmpeg payload using real Mach-O commands.

No work runs on import. API: bundle(ffmpeg, ffprobe, output, ...), audit_payload(output).
CLI examples:
  python bundle_macos_ffmpeg.py --output out/ffmpeg-private
  python bundle_macos_ffmpeg.py --audit-only out/ffmpeg-private

Integrate AFTER PyInstaller binary processing, then re-sign the enclosing .app.
PyInstaller processing would otherwise replace these private paths with shared
@rpath/basenames. Linked dependencies are covered; arbitrary dlopen plug-ins,
font configuration, ML models and other runtime data require separate inventory.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import traceback

OTOOL = "/usr/bin/otool"
LIPO = "/usr/bin/lipo"
INSTALL_NAME_TOOL = "/usr/bin/install_name_tool"
CODESIGN = "/usr/bin/codesign"
LOAD_COMMANDS = {"LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB", "LC_LOAD_UPWARD_DYLIB",
                 "LC_LAZY_LOAD_DYLIB", "LC_PREBOUND_DYLIB"}
SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/", "/Library/Apple/System/Library/")
LICENSE_NAME = re.compile(r"^(licen[sc]e|copying|copyright|notice|a?gpl|lgpl|mpl|epl|apache|bsd|mit|bsl)(?:$|[._-])", re.IGNORECASE)


class BundleError(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def run(arguments, *, timeout=120):
    result = subprocess.run([str(v) for v in arguments], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if result.returncode:
        raise BundleError(f"Command exited {result.returncode}: {arguments!r}\n{result.stderr[-6000:]}{result.stdout[-2000:]}")
    return result.stdout


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_system(path):
    return str(path).startswith(SYSTEM_PREFIXES)


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def archs(path):
    return run([LIPO, "-archs", path], timeout=30).strip().split()


@dataclass
class MachO:
    dependencies: list[str] = field(default_factory=list)
    rpaths: list[str] = field(default_factory=list)
    identities: list[str] = field(default_factory=list)


def macho(path, arch="arm64"):
    """Parse otool -l command records, not arbitrary strings embedded in code."""
    text = run([OTOOL, "-arch", arch, "-l", path], timeout=60)
    result, command = MachO(), None
    for line in text.splitlines():
        value = line.strip()
        if value.startswith("Load command "):
            command = None
        elif value.startswith("cmd "):
            command = value.split(None, 1)[1]
        elif command in LOAD_COMMANDS | {"LC_ID_DYLIB"} and value.startswith("name "):
            name = re.sub(r"\s+\(offset \d+\)$", "", value[5:])
            destination = result.identities if command == "LC_ID_DYLIB" else result.dependencies
            if name not in destination:
                destination.append(name)
        elif command == "LC_RPATH" and value.startswith("path "):
            name = re.sub(r"\s+\(offset \d+\)$", "", value[5:])
            if name not in result.rpaths:
                result.rpaths.append(name)
    return result


def expand_anchor(value, loader, executable):
    for token, base in (("@loader_path", Path(loader).parent), ("@executable_path", Path(executable).parent)):
        if value == token:
            return base
        if value.startswith(token + "/"):
            return base / value[len(token) + 1:]
    if value.startswith("/"):
        return Path(value)
    raise BundleError(f"Unsupported run-path anchor {value!r} in {loader}")


def effective_rpaths(info, loader, executable, inherited):
    result = []
    for value in info.rpaths:
        expanded = expand_anchor(value, loader, executable).resolve()
        if expanded not in result:
            result.append(expanded)
    for value in inherited:
        value = Path(value).resolve()
        if value not in result:
            result.append(value)
    return tuple(result)


def resolve_dependency(name, loader, executable, rpaths, search_paths=()):
    if is_system(name):
        return None
    candidates = []
    if name.startswith(("@loader_path/", "@executable_path/")):
        candidates.append(expand_anchor(name, loader, executable))
    elif name.startswith("@rpath/"):
        suffix = name[len("@rpath/"):]
        candidates += [Path(path) / suffix for path in rpaths]
        candidates += [Path(path) / suffix for path in search_paths]
    elif name.startswith("/"):
        candidates.append(Path(name))
    elif not name.startswith("@"):
        candidates += [Path(loader).parent / name, Path(executable).parent / name]
        candidates += [Path(path) / name for path in search_paths]
    else:
        raise BundleError(f"Unsupported dependency token {name!r} in {loader}")
    for candidate in candidates:
        if is_system(str(candidate)):
            # Apple shared-cache libraries need not be present as regular files.
            return None
        if candidate.is_file():
            return candidate.resolve(strict=True)
    attempted = ", ".join(str(path) for path in candidates)
    raise BundleError(f"Unresolved non-system dependency {name!r} in {loader}; tried: {attempted}")


@dataclass
class Node:
    source: Path
    destination: Path
    aliases: set[str] = field(default_factory=set)
    dependencies: dict[str, Path | None] = field(default_factory=dict)
    info: MachO | None = None


def plan_closure(ffmpeg, ffprobe, *, arch="arm64", search_paths=(), extra_binaries=()):
    """Resolve the linked closure before copying; no PyAV/global deduplication."""
    nodes, names, queue = {}, {}, deque()
    executable_paths = {"ffmpeg": Path(ffmpeg).resolve(strict=True), "ffprobe": Path(ffprobe).resolve(strict=True)}

    def add(path, executable, inherited, *, entry_name=None, alias=None):
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise BundleError(f"Dependency is not a regular file: {source}")
        if any(part.endswith(".framework") for part in source.parts) and not is_system(str(source)):
            raise BundleError(f"Non-system framework requires a framework/resource-aware bundler, not flat dylib copying: {source}")
        if source not in nodes:
            if arch not in archs(source):
                raise BundleError(f"Required {arch} slice is missing from {source}")
            destination = Path("bin") / entry_name if entry_name else Path("lib") / source.name
            nodes[source] = Node(source, destination, info=macho(source, arch))
        node = nodes[source]
        if entry_name is not None and node.destination != Path("bin") / entry_name:
            raise BundleError(f"A source is requested both as an entry executable and shared library: {source}")
        if node.destination.parts[0] == "lib":
            for name in {source.name, alias or source.name}:
                if name in ("", ".", "..") or "/" in name:
                    raise BundleError(f"Unsafe library basename: {name!r}")
                previous = names.get(name)
                if previous is not None and previous != source:
                    raise BundleError(f"Library basename collision {name!r}: {previous} versus {source}")
                names[name] = source
                node.aliases.add(name)
        queue.append((source, Path(executable), tuple(inherited)))
        return source

    for name, path in executable_paths.items():
        add(path, path, (), entry_name=name)
    for path in extra_binaries:
        add(path, executable_paths["ffmpeg"], (), alias=Path(path).name)
    seen_contexts, planned = set(), set()
    while queue:
        source, executable, inherited = queue.popleft()
        context = (source, executable, inherited)
        if context in seen_contexts:
            continue
        seen_contexts.add(context)
        node = nodes[source]
        rpaths = effective_rpaths(node.info, source, executable, inherited)
        dependencies = {name: resolve_dependency(name, source, executable, rpaths, search_paths)
                        for name in node.info.dependencies}
        if source in planned:
            if dependencies != node.dependencies:
                raise BundleError(f"Dependency resolution changes with executable/rpath context for {source}")
            continue
        node.dependencies = dependencies
        planned.add(source)
        for name, dependency in dependencies.items():
            if dependency is not None:
                add(dependency, executable, rpaths, alias=PurePosixPath(name).name)
    return nodes


def brew_prefix(path):
    parts = Path(path).parts
    if "Cellar" not in parts:
        return None
    index = parts.index("Cellar")
    if len(parts) <= index + 2:
        return None
    return Path(*parts[:index + 3])


def collect_licenses(nodes, output):
    reports = []
    prefixes = sorted({prefix for node in nodes.values() if (prefix := brew_prefix(node.source)) is not None})
    for prefix in prefixes:
        found = set()
        # Homebrew bottles commonly retain license files at their prefix root,
        # share/doc, share/licenses, or in a formula-specific share directory.
        for path in prefix.iterdir():
            if path.is_file() and LICENSE_NAME.match(path.name):
                found.add(path)
        share = prefix / "share"
        if share.is_dir():
            for path in share.rglob("*"):
                if path.is_file() and (LICENSE_NAME.match(path.name) or any(part.lower() in ("licenses", "licences") for part in path.relative_to(share).parts[:-1])):
                    found.add(path)
        if not found:
            # Some bottles retain the complete software notice in README.html
            # instead of a conventionally named license. Copy the actual whole
            # file only when it contains a recognizable grant, not just a link.
            for path in prefix.glob("README*"):
                if path.is_file() and path.stat().st_size <= 2 * 1024**2:
                    text = path.read_bytes().lower()
                    if (b"redistribution and use in source and binary forms" in text or
                            b"permission is hereby granted" in text):
                        found.add(path)
        copied = []
        for path in sorted(found):
            relative = path.relative_to(prefix)
            destination = output / "licenses" / (prefix.parent.name + "-" + prefix.name) / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            copied.append({"source": str(path), "destination": str(destination.relative_to(output)), "sha256": file_hash(destination)})
        reports.append({"formula": prefix.parent.name, "version": prefix.name, "files": copied,
                        "status": "actual_files_copied" if copied else "no_license_file_found_in_prefix"})
    return reports


def audit_payload(output, *, arch="arm64", verify_signatures=True):
    """Inspect final Mach-O commands and actual resolution; no blind string scan."""
    output = Path(output).resolve(strict=True)
    entries = {name: output / "bin" / name for name in ("ffmpeg", "ffprobe")}
    if not all(path.is_file() for path in entries.values()):
        raise BundleError("Payload must contain bin/ffmpeg and bin/ffprobe")
    symlinks, binaries = [], []
    for top in (output / "bin", output / "lib"):
        for path in sorted(top.rglob("*")):
            if path.is_symlink():
                target = path.resolve(strict=True)
                if not inside(target, output):
                    raise BundleError(f"Payload symlink escapes its root: {path} -> {target}")
                symlinks.append({"path": str(path.relative_to(output)), "target": os.readlink(path)})
            elif path.is_file():
                binaries.append(path)
    results = []
    for path in binaries:
        slices = archs(path)
        if slices != [arch]:
            raise BundleError(f"Payload is not the requested single native slice: {path}: {slices}")
        info = macho(path, arch)
        private_rpaths = []
        for value in info.rpaths:
            if not value.startswith("@loader_path"):
                raise BundleError(f"Non-private run path remains in {path}: {value}")
            resolved = expand_anchor(value, path, entries["ffmpeg"]).resolve()
            if not inside(resolved, output):
                raise BundleError(f"Run path escapes payload: {path}: {value}")
            private_rpaths.append(resolved)
        dependencies = []
        for name in info.dependencies:
            if is_system(name):
                dependencies.append({"load_command": name, "system": True})
                continue
            if not name.startswith(("@loader_path/", "@rpath/")):
                raise BundleError(f"Absolute/non-private dependency remains in {path}: {name}")
            resolved = resolve_dependency(name, path, entries["ffmpeg"], private_rpaths)
            if resolved is None or not inside(resolved, output):
                raise BundleError(f"Non-system dependency does not resolve inside payload: {path}: {name}")
            dependencies.append({"load_command": name, "resolved": str(resolved.relative_to(output)), "system": False})
        for identity in info.identities:
            if not identity.startswith("@loader_path/"):
                raise BundleError(f"Non-private dylib identity remains in {path}: {identity}")
            target = expand_anchor(identity, path, entries["ffmpeg"]).resolve(strict=True)
            if target != path.resolve():
                raise BundleError(f"Dylib identity resolves to a different file: {path}: {identity}")
        if verify_signatures:
            run([CODESIGN, "--verify", "--strict", "--all-architectures", path], timeout=60)
        results.append({"path": str(path.relative_to(output)), "architecture": slices, "bytes": path.stat().st_size,
                        "sha256": file_hash(path), "dependencies": dependencies, "rpaths": info.rpaths,
                        "identities": info.identities, "signature_verified": verify_signatures})
    return {"status": "passed", "audited_utc": utc(), "architecture": arch, "binary_count": len(results),
            "binaries": results, "symlinks": symlinks, "all_non_system_load_commands_resolve_inside_payload": True,
            "scope": "Mach-O linked dependencies and private symlinks/signatures; embedded build strings are not dependency evidence. Runtime dlopen/data resources require separate smoke checks."}


def bundle(ffmpeg, ffprobe, output, *, arch="arm64", search_paths=(), extra_binaries=()):
    if sys.platform != "darwin":
        raise BundleError("Build this payload on macOS using Apple Mach-O tools")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "bundle-report.json"
    report = {"schema_version": 1, "status": "planning", "started_utc": utc(), "architecture": arch,
              "integration": "Copy this completed payload after PyInstaller processing; sign the enclosing app after all modifications. Do not flatten/deduplicate with PyAV libraries.",
              "input_ffmpeg": str(ffmpeg), "input_ffprobe": str(ffprobe)}
    atomic_json(report_path, report)
    try:
        nodes = plan_closure(ffmpeg, ffprobe, arch=arch, search_paths=tuple(Path(p).resolve() for p in search_paths), extra_binaries=extra_binaries)
        report.update(status="copying", binary_count=len(nodes), plan=[{"source": str(n.source), "destination": str(n.destination),
                          "aliases": sorted(n.aliases), "source_sha256": file_hash(n.source),
                          "dependencies": {key: str(value) if value is not None else None for key, value in n.dependencies.items()}}
                         for n in nodes.values()])
        atomic_json(report_path, report)
        (output / "bin").mkdir()
        (output / "lib").mkdir()
        for node in nodes.values():
            destination = output / node.destination
            source_archs = archs(node.source)
            if source_archs == [arch]:
                shutil.copyfile(node.source, destination)
            else:
                run([LIPO, node.source, "-thin", arch, "-output", destination])
            destination.chmod(0o755)
        for node in nodes.values():
            if node.destination.parts[0] != "lib":
                continue
            for name in sorted(node.aliases):
                if name != node.destination.name:
                    (output / "lib" / name).symlink_to(node.destination.name)
        report["licenses"] = collect_licenses(nodes, output)
        report["status"] = "relocating"
        atomic_json(report_path, report)
        for node in nodes.values():
            destination = output / node.destination
            arguments = [INSTALL_NAME_TOOL]
            if node.info.identities:
                arguments += ["-id", "@loader_path/" + destination.name]
            for original, dependency in node.dependencies.items():
                if dependency is None:
                    continue
                target = output / nodes[dependency].destination
                relative = os.path.relpath(target, destination.parent).replace(os.sep, "/")
                arguments += ["-change", original, "@loader_path/" + relative]
            # Keep only a private rpath for optional @rpath-based runtime loads;
            # no inherited Homebrew rpaths may survive in the payload.
            private = "@loader_path/../lib" if node.destination.parts[0] == "bin" else "@loader_path"
            for old in node.info.rpaths:
                if old != private:
                    arguments += ["-delete_rpath", old]
            if private not in node.info.rpaths:
                arguments += ["-add_rpath", private]
            run([*arguments, destination])
        report["status"] = "signing"
        atomic_json(report_path, report)
        for node in sorted(nodes.values(), key=lambda n: n.destination.parts[0] == "bin"):
            run([CODESIGN, "--force", "--sign", "-", "--timestamp=none", output / node.destination])
        report["status"] = "auditing"
        atomic_json(report_path, report)
        report["audit"] = audit_payload(output, arch=arch, verify_signatures=True)
        report.update(status="passed", finished_utc=utc())
        atomic_json(report_path, report)
        return report
    except BaseException as error:
        report.update(status="failed", finished_utc=utc(), error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
        atomic_json(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ffmpeg", type=Path, default=Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"))
    parser.add_argument("--ffprobe", type=Path, help="Defaults to ffprobe next to --ffmpeg")
    parser.add_argument("--output", type=Path, help="Fresh private payload directory only")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default="arm64")
    parser.add_argument("--search-path", action="append", type=Path, default=[], help="Additional explicit non-system dependency search root")
    parser.add_argument("--extra-binary", action="append", type=Path, default=[], help="Explicit runtime-loaded dylib not named by a Mach-O load command")
    parser.add_argument("--audit-only", type=Path, help="Read-only audit of an existing payload; includes signature checks")
    args = parser.parse_args()
    if args.audit_only is not None:
        if args.output is not None:
            parser.error("--audit-only and --output are mutually exclusive")
        result = audit_payload(args.audit_only, arch=args.arch)
    else:
        if args.output is None:
            parser.error("--output is required to build a fresh payload")
        result = bundle(args.ffmpeg, args.ffprobe or args.ffmpeg.with_name("ffprobe"), args.output,
                        arch=args.arch, search_paths=args.search_path, extra_binaries=args.extra_binary)
    print(json.dumps({key: result.get(key) for key in ("status", "architecture", "binary_count")}, indent=2))


if __name__ == "__main__":
    main()
