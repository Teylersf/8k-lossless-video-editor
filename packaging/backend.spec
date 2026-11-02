# -*- mode: python ; coding: utf-8 -*-
"""Native PyInstaller 6.22.3 onedir backend. GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION."""
import os
import sys
import json
import hashlib
from pathlib import Path
import platform
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
vendor = os.environ.get('EDITOR_AV_VENDOR')
if os.name == 'nt':
    if not vendor:
        raise RuntimeError('Windows packaging requires the verified private LGPL PyAV runtime')
    vendor = Path(vendor).resolve(strict=True)
    receipt = json.loads(Path(os.environ.get('EDITOR_AV_RECEIPT', vendor.parent / 'receipt-v2.json')).read_text(encoding='utf-8'))
    if receipt.get('schema_version') != 2 or not receipt.get('native_overrides'):
        raise RuntimeError('The audited clean avcodec/avformat rebuild is required')
    if not receipt.get('runtime_verification'):
        raise RuntimeError('Private PyAV native verification is missing')
    for relative, expected in receipt['private_files'].items():
        if hashlib.sha256((vendor / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Private PyAV payload changed: ' + relative)
    sys.path.insert(0, str(vendor))
stage = Path(os.environ['EDITOR_NATIVE_STAGE']).resolve()
target = 'win32' if os.name == 'nt' else 'darwin'
if platform.system() not in ('Windows', 'Darwin'):
    raise RuntimeError('Build the desktop backend on native Windows or Apple Silicon macOS')

datas = [(str(root / 'server.py'), '.'), (str(root / 'media_library.py'), '.'),
         (str(root / 'LICENSE'), '.'), (str(root / 'LICENSE-EXCEPTION'), '.'), (str(root / 'NOTICE.md'), '.'), (str(root / 'app/dist'), 'app/dist'),
         (str(stage / 'licenses'), 'licenses')]
# Preserve the independently relocated/signed Mac FFmpeg runtime by copying it
# after COLLECT; Analysis otherwise unifies incompatible dylib basenames.
if target == 'win32':
    datas.append((str(stage / 'bin'), 'bin'))
engine_modules = ['editor_pipeline', 'project_model', 'media_pipeline', 'runtime_backend', 'studio_engine', 'format_catalog']
datas += [(str(root / 'engine' / (name + '.py')), 'engine') for name in engine_modules]
binaries, hiddenimports = [], ['server', 'media_library'] + engine_modules + ['engine.' + name for name in engine_modules]
packages = ['torch', 'av', 'numpy', 'PIL', 'fastapi', 'starlette', 'uvicorn', 'pydantic', 'pydantic_core',
            'multipart', 'python_multipart', 'httpx', 'httpcore', 'anyio', 'annotated_doc']
# The editor uses eager tensor operations, not training, model export, cloud
# filesystems, notebooks or the optional packages installed beside local Torch.
# Keep Torch itself, all its CUDA/MPS native libraries and its required symbolic
#/runtime dependencies; a frozen render is still required after this build.
optional_excludes = {
    'scipy', 'pandas', 'numba', 'llvmlite', 'sklearn', 'cv2', 'tensorflow',
    'jax', 'jaxlib', 'onnx', 'onnxruntime', 'torchaudio', 'torchvision', 'torchtext',
    'transformers', 'tensorboard', 'tensorboardX', 'triton', 'pyarrow',
    'boto3', 'botocore', 'sqlalchemy', 's3fs', 'aiobotocore', 'gcsfs', 'adlfs',
    'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'matplotlib', 'IPython', 'notebook',
}
def runtime_module(name):
    if name.startswith(('torch.utils.tensorboard', 'torch.utils.benchmark', 'torch.onnx')):
        return False
    return not any(part in {'tests', 'test', 'testing', '_internal_tests'} or part.startswith('test_') for part in name.split('.'))
for package in packages:
    extra_data, extra_binaries, extra_imports = collect_all(package, include_py_files=True,
                                                          filter_submodules=runtime_module)
    datas += extra_data
    binaries += extra_binaries
    hiddenimports += extra_imports
for package in ['uvicorn.logging', 'uvicorn.loops', 'uvicorn.protocols', 'uvicorn.lifespan']:
    hiddenimports += collect_submodules(package)
for distribution in ['torch', 'av', 'numpy', 'Pillow', 'fastapi', 'starlette', 'uvicorn', 'pydantic', 'pydantic_core',
                     'python-multipart', 'httpx', 'httpcore', 'anyio', 'annotated-doc', 'typing_extensions',
                     'filelock', 'fsspec', 'jinja2', 'sympy', 'networkx', 'mpmath', 'h11', 'click', 'idna', 'certifi']:
    datas += copy_metadata(distribution)

a = Analysis([str(root / 'packaging/backend_entry.py')], pathex=([str(vendor)] if vendor else []) + [str(root), str(root / 'engine')],
             binaries=binaries, datas=datas, hiddenimports=sorted(set(hiddenimports)),
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=sorted(optional_excludes),
             noarchive=False)
if target == 'win32':
    native_names = {f'{name}-{major}.dll' for name, major in {'avcodec':62,'avdevice':62,'avfilter':11,'avformat':62,'avutil':60,'swresample':6,'swscale':9}.items()}
    def shipping_entry(entry):
        destination, source = entry[:2]
        basename = Path(destination).name.lower()
        # PE imports and delay imports of all 37 installed Torch DLLs were
        # audited: this unused profiler has no importer. Frozen CUDA render
        # remains mandatory; no installed Torch file is modified.
        if basename == 'nvperf_host.dll':
            return False
        if basename in native_names:
            if Path(source).resolve().parent != vendor / 'av.libs':
                raise RuntimeError('Unexpected FFmpeg DLL source: ' + source)
            return destination.replace('\\', '/').startswith('av.libs/')
        if any(basename.startswith(prefix + '-') for prefix in ('avcodec','avdevice','avfilter','avformat','avutil','swresample','swscale')) and basename.endswith('.dll'):
            raise RuntimeError('Unapproved original FFmpeg DLL in bundle: ' + destination)
        return True
    a.binaries = [entry for entry in a.binaries if shipping_entry(entry)]
    a.datas = [entry for entry in a.datas if shipping_entry(entry)]
    shipped = {Path(entry[0]).name.lower() for entry in a.binaries + a.datas if entry[0].replace('\\', '/').startswith('av.libs/')}
    if shipped != native_names:
        raise RuntimeError('Private FFmpeg DLL closure incomplete: ' + repr(shipped))
pyz = PYZ(a.pure)
version_resource = None
if target == 'win32':
    from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo,
                                                    StringTable, StringStruct, VarFileInfo, VarStruct)
    version_resource = VSVersionInfo(ffi=FixedFileInfo(filevers=(0, 1, 0, 0), prodvers=(0, 1, 0, 0),
        mask=0x3f, flags=0x2, OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName', 'Taylor Kalin'),
            StringStruct('FileDescription', '8K Lossless Video Editor'), StringStruct('ProductName', '8K Lossless Video Editor'),
            StringStruct('FileVersion', '0.1.0-beta.1'), StringStruct('ProductVersion', '0.1.0-beta.1'),
            StringStruct('InternalName', '8k-editor-backend'), StringStruct('OriginalFilename', '8k-editor-backend.exe'),
            StringStruct('LegalCopyright', 'Copyright 2026 Taylor Kalin')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='8k-editor-backend',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
          disable_windowed_traceback=False, target_arch='arm64' if target == 'darwin' else None,
          codesign_identity=os.environ.get('EDITOR_CODESIGN_IDENTITY') or None, entitlements_file=None,
          contents_directory='_internal', version=version_resource,
          icon=str(root / 'desktop/assets/icon.ico') if target == 'win32' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=target)
