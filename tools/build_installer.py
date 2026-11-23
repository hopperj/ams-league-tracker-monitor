"""Compile the per-user Windows installer and exercise an isolated installation."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ams_recorder import __version__
from ams_recorder.installation import INSTALLER_ASSET


def compile_installer(compiler, output, filename, release, *, app_id=None, name=None):
    command = [str(compiler), '/Qp', '/DRecorderVersion=' + release,
               '/O' + str(output), '/F' + filename]
    if app_id: command.append('/DRecorderAppId=' + app_id)
    if name: command.append('/DRecorderName=' + name)
    subprocess.run([*command, str(ROOT / 'installer' / 'recorder.iss')], check=True, timeout=120)
    return output / (filename + '.exe')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default=os.environ.get('INNO_SETUP_COMPILER'))
    args = parser.parse_args()
    if os.name != 'nt': parser.error('Build the installer on Windows with Inno Setup 6.3 or later')
    compiler = args.compiler or shutil.which('ISCC.exe') or str(
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6' / 'ISCC.exe')
    if not Path(compiler).is_file(): parser.error('Install Inno Setup 6.3+ or set INNO_SETUP_COMPILER to ISCC.exe')
    output = ROOT / 'dist'
    installer = compile_installer(compiler, output, Path(INSTALLER_ASSET).stem, __version__)
    # Never register/uninstall the developer's real recorder in a smoke check.
    with tempfile.TemporaryDirectory(prefix='recorder-installer-') as folder:
        temporary = Path(folder)
        identity = 'AMSLeagueRecorder.Smoke.' + uuid.uuid4().hex
        name = 'AMS League Recorder smoke ' + uuid.uuid4().hex[:8]
        smoke = compile_installer(compiler, temporary, 'setup-smoke', __version__, app_id=identity, name=name)
        older = compile_installer(compiler, temporary, 'setup-old', '0.0.0', app_id=identity, name=name)
        subprocess.run([sys.executable, str(ROOT / 'tools' / 'smoke_installer.py'),
            str(smoke), str(older), str(output / 'AMSLeagueRecorder-Windows-x64-Intel-AMD.exe'), name],
            check=True, timeout=180)
    print('Checked Windows installer: ' + str(installer))


if __name__ == '__main__': main()
