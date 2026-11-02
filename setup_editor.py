"""Prepare the local editor environment without changing other applications.

GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION. Native tools are prerequisites; no system files or shell profiles
are modified. Windows can reuse an already installed CUDA PyTorch environment.
"""
from pathlib import Path
import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def native_path():
    if sys.platform == 'darwin':
        os.environ['PATH'] = '/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:' + os.environ.get('PATH','')


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', action='store_true', help='Install CPU PyTorch instead of CUDA on Windows/Linux')
    args = parser.parse_args()
    native_path()
    if sys.version_info[:2] != (3,13):
        raise RuntimeError('Run setup with native Python 3.13.')
    if sys.platform == 'darwin' and platform.machine() != 'arm64':
        raise RuntimeError('This Mac setup requires native Apple Silicon Python, without Rosetta.')
    if sys.platform == 'darwin' and tuple(map(int,platform.mac_ver()[0].split('.')[:2])) < (14,0):
        raise RuntimeError('macOS 14 or newer is required. Homebrew installation is recommended on macOS15+.')
    for name in ('node','ffmpeg','ffprobe'):
        if not shutil.which(name):
            raise RuntimeError(f'{name} is missing. See README.md for native prerequisites. On Mac: brew install python@3.13 node@22 ffmpeg-full')
    run(['node','-e',"const [a,b]=process.versions.node.split('.').map(Number); if(a!==22||b<12)throw Error('Node22.12+ within22.x required')"])
    if sys.platform == 'darwin':
        run(['node','-e',"if(process.arch!=='arm64')throw Error('Native arm64 Node is required; disable Rosetta')"])
    listing = subprocess.check_output(['ffmpeg','-hide_banner','-filters'],stderr=subprocess.DEVNULL,text=True)
    filters = {p[1] for line in listing.splitlines() if len(p:=line.split()) >= 3}
    needed = {'zscale','tonemap','amix','atrim','atempo'}
    if missing := needed-filters:
        raise RuntimeError('FFmpeg lacks required filters: '+', '.join(sorted(missing))+'. On Mac install ffmpeg-full; the launchers select its keg-only binary.')
    environment = ROOT/'.venv-editor'
    python = environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not environment.exists():
        venv.EnvBuilder(with_pip=True,system_site_packages=os.name=='nt').create(environment)
    if not python.is_file():
        raise RuntimeError('Existing .venv-editor is incompatible; move it aside manually. Setup will not delete it.')
    native_check = "import sys,platform;assert sys.version_info[:2]==(3,13);print('Python:',sys.version.split()[0],platform.machine())"
    if sys.platform == 'darwin':
        native_check += ";assert platform.machine()=='arm64', 'Existing environment must use native arm64 Python'"
    run([python,'-c',native_check])
    run([python,'-m','pip','install','--only-binary=:all:','-r',ROOT/'engine/requirements.txt'])
    use_cuda = sys.platform != 'darwin' and not args.cpu and bool(shutil.which('nvidia-smi'))
    check = "import torch; assert torch.__version__.split('+')[0]=='2.11.0'"
    if use_cuda:
        check += "; assert torch.version.cuda is not None, 'CUDA PyTorch required'"
    elif args.cpu:
        check += "; assert torch.version.cuda is None, 'CPU-only PyTorch requested'"
    if sys.platform == 'darwin':
        check += "; assert torch.backends.mps.is_built(), 'Native Metal PyTorch required'"
    torch_check = subprocess.run([str(python),'-c',check],capture_output=True,text=True)
    if torch_check.returncode:
        if sys.platform=='darwin':
            run([python,'-m','pip','install','--only-binary=:all:','torch==2.11.0'])
        else:
            index = 'https://download.pytorch.org/whl/cu126' if use_cuda else 'https://download.pytorch.org/whl/cpu'
            wheel = 'torch==2.11.0+cu126' if use_cuda else 'torch==2.11.0+cpu'
            run([python,'-m','pip','install','--upgrade',wheel,'--index-url',index])
        run([python,'-c',check])
    run([python,'-m','pip','check'])
    run([python,'-c',"import sys;sys.path.insert(0,'engine');from runtime_backend import backend_info;print(backend_info())"],cwd=ROOT)
    npm = 'npm.cmd' if os.name=='nt' else 'npm'
    run([npm,'ci'],cwd=ROOT/'app')
    run([npm,'run','build'],cwd=ROOT/'app')
    print('\nSetup completed. It did not start the editor. Run START 8K LOSSLESS VIDEO EDITOR when ready.')
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (OSError,RuntimeError,subprocess.SubprocessError) as error:
        print('Setup stopped:',error,file=sys.stderr)
        raise SystemExit(1)
