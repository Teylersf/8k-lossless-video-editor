"""Start/stop only this local editor; never deploy or stop Lens Studio. GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8770'
APP = '8K Lossless Video Editor'


def native_path():
    if sys.platform=='darwin':
        os.environ['PATH']='/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:'+os.environ.get('PATH','')


def running():
    try:
        with urllib.request.urlopen(URL+'/api/status',timeout=3) as response:
            return json.load(response).get('app')==APP
    except (OSError,ValueError):
        return False


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    native_path()
    if '--stop' in arguments:
        if not running():
            print(APP+' is not running.')
            return 0
        request=urllib.request.Request(URL+'/api/shutdown',data=b'',method='POST',headers={'X-Editor-Client':'8k-lossless-editor'})
        with urllib.request.urlopen(request,timeout=15):
            pass
        deadline=time.monotonic()+30
        while running() and time.monotonic()<deadline:
            time.sleep(.2)
        if running():
            raise RuntimeError('The editor is still stopping. No other process has been signalled.')
        print(APP+' stopped.')
        return 0
    python=ROOT/'.venv-editor'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        subprocess.run([sys.executable,str(ROOT/'setup_editor.py')],cwd=ROOT,check=True)
    if python.exists() and Path(sys.executable).absolute()!=python.absolute():
        return subprocess.call([str(python),str(ROOT/'launch.py'),*arguments],cwd=ROOT)
    if not running():
        npm='npm.cmd' if os.name=='nt' else 'npm'
        if not (ROOT/'app/node_modules').is_dir():
            subprocess.run([npm,'ci'],cwd=ROOT/'app',check=True)
        subprocess.run([npm,'run','build'],cwd=ROOT/'app',check=True)
        logs=ROOT/'out'; logs.mkdir(exist_ok=True)
        kwargs={'stdin':subprocess.DEVNULL}
        if os.name=='nt': kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
        else: kwargs['start_new_session']=True
        with (logs/'server.log').open('ab') as log:
            process=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,**kwargs)
        (logs/'server.pid').write_text(str(process.pid),encoding='ascii')
        for _ in range(120):
            if running(): break
            if process.poll() is not None:
                raise RuntimeError('Editor startup failed. See out/server.log; port8770 may be occupied.')
            time.sleep(.5)
        else:
            raise RuntimeError('Editor startup exceeded its deadline. See out/server.log.')
    print(URL)
    if '--no-browser' not in arguments:
        webbrowser.open(URL)
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (OSError,RuntimeError,subprocess.SubprocessError) as error:
        print(error,file=sys.stderr)
        raise SystemExit(1)
