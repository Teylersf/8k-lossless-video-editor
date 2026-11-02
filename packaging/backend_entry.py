"""Frozen desktop backend and allowlisted owned-worker entry. GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION.

The desktop wrapper starts --server with an isolated data directory, loopback
port and ownership token. The same binary dispatches the existing private
worker commands without requiring a separately installed Python interpreter.
"""
from __future__ import annotations

import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import runpy
import subprocess
import sys

APP_NAME = "8K Lossless Video Editor"
AUTHOR = "Taylor Kalin"
VERSION = "0.1.0-beta.1"
_WORKERS = {
    "editor_pipeline.py": ("engine/editor_pipeline.py", {"--worker": 2}),
    "media_pipeline.py": ("engine/media_pipeline.py", {"--worker": 2}),
    "media_library.py": ("media_library.py", {"--prepare": 3, "--sequence": 3}),
}


def resource_root():
    return Path(sys._MEIPASS).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def configure_runtime(require_data=True):
    root = resource_root()
    for directory in (root, root / "engine"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    if getattr(sys, "frozen", False):
        sys.dont_write_bytecode = True
        suffix = ".exe" if os.name == "nt" else ""
        # A packaged application must not accidentally depend on a host shim,
        # Homebrew installation, or a user-supplied executable of the same name.
        for name, variable in (("ffmpeg", "STUDIO_FFMPEG"), ("ffprobe", "STUDIO_FFPROBE")):
            binary = root / "bin" / (name + suffix)
            if not binary.is_file():
                raise RuntimeError(f"The application is incomplete: bundled {name} is missing")
            os.environ[variable] = str(binary)
        os.environ["PATH"] = os.pathsep.join((str(root / "bin"), str(root / "av.libs"), os.environ.get("PATH", "")))
        # Desktop imports/projects/renders belong to userData, never resources.
        if require_data and not os.environ.get("EDITOR_DATA_DIR"):
            raise RuntimeError("The desktop wrapper must provide EDITOR_DATA_DIR")
    if os.environ.get("EDITOR_LOG_DIR"):
        Path(os.environ["EDITOR_LOG_DIR"]).mkdir(parents=True, exist_ok=True)
    return root


def self_test():
    import torch
    from runtime_backend import select_device, backend_info
    import media_pipeline
    versions = {}
    for package in ("torch", "av", "numpy", "Pillow", "fastapi", "uvicorn", "python-multipart", "pydantic"):
        versions[package] = importlib.metadata.version(package)
    native = {}
    for name, path in (("ffmpeg", media_pipeline.FFMPEG), ("ffprobe", media_pipeline.FFPROBE)):
        result = subprocess.run([path, "-hide_banner", "-version"], check=True, capture_output=True, timeout=30, text=True)
        native[name] = {"path": path, "version": result.stdout.splitlines()[0]}
    print(json.dumps(dict(app=APP_NAME, author=AUTHOR, version=VERSION, ready=True, frozen=bool(getattr(sys, "frozen", False)),
                          python=sys.version.split()[0], versions=versions, native=native,
                          cuda_runtime=torch.version.cuda, backend=backend_info(select_device()),
                          static_assets=(resource_root() / "app/dist/index.html").is_file()), indent=2))
    return 0


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    root = configure_runtime(require_data=arguments not in (["--self-test"], ["--license"]))
    if arguments == ["--self-test"]:
        return self_test()
    if arguments == ["--license"]:
        path = root / "LICENSE"
        print(path.read_text(encoding="utf-8"))
        return 0
    if arguments == ["--server"]:
        import server
        server.run_server()
        return 0
    if arguments:
        requested = Path(arguments[0])
        entry = _WORKERS.get(requested.name)
        if entry:
            relative, commands = entry
            expected = (root / relative).resolve()
            tail = arguments[1:]
            if requested.resolve() != expected or not expected.is_file() or not tail or len(tail) != commands.get(tail[0]):
                raise ValueError("Refusing an unrecognized worker identity or command")
            sys.argv = [str(expected), *tail]
            runpy.run_path(str(expected), run_name="__main__")
            return 0
    raise ValueError("Use --server, --self-test, --license, or an owned internal worker")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Backend error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
