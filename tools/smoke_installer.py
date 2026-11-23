"""Check shortcuts, in-place updates, downgrade refusal, and data-safe uninstall."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ams_recorder import updates
from ams_recorder.configuration import save_configuration
from ams_recorder.installation import INSTALLED_EXE, running
from ams_recorder.spool import Spool
import smoke_update


def setup(executable, directory, log):
    return subprocess.run([str(executable), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/SP-', '/TASKS=desktopicon', '/DIR=' + str(directory), '/LOG=' + str(log)],
        timeout=60).returncode


def shortcuts(name):
    script = '''$shell = New-Object -ComObject WScript.Shell
    $items = @('Programs', 'Desktop') | ForEach-Object {
      $link = Join-Path $shell.SpecialFolders.Item($_) ($env:AMS_SMOKE_NAME + '.lnk')
      if (!(Test-Path -LiteralPath $link)) { throw "Missing shortcut: $link" }
      $shortcut = $shell.CreateShortcut($link)
      @{path=$link; target=$shortcut.TargetPath; working=$shortcut.WorkingDirectory}
    }
    ConvertTo-Json -InputObject @($items) -Compress
    '''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        env={**os.environ, 'AMS_SMOKE_NAME': name}, check=True, capture_output=True, text=True, timeout=20)
    return json.loads(result.stdout)


def main():
    if os.name != 'nt': raise SystemExit('The installer smoke check requires Windows')
    installer, older, built = [Path(value).resolve() for value in sys.argv[1:4]]
    name = sys.argv[4]
    if not name.startswith('AMS League Recorder smoke '):
        raise SystemExit('Only run the disposable smoke-test installer here')
    with tempfile.TemporaryDirectory(prefix='recorder-installed-') as folder:
        root = Path(folder)
        install = root / "Racer's installed recorder é"
        data = root / 'saved-recorder-data'; data.mkdir()
        target = install / INSTALLED_EXE
        config, spool = data / 'config.json', data / 'spool.sqlite3'
        save_configuration(config, dict(server='https://example.invalid', token='smoke-only-secret',
            recorder_id='existing-installation', driver_name='Smoke racer', event_code='AUTO',
            account_linked=True, account_name='Existing Steam link', account_driver_id='existing-driver'))
        queue = Spool(spool); queue.bind('https://example.invalid', 'existing-installation')
        queue.append('AUTO', 'saved-run', dict(sequence=1, captured_at_ms=1000))
        saved_batch = queue.batch('existing-installation'); queue.close()
        saved_config = config.read_bytes()
        enrollment = installer.with_name('AMSLeagueRecorder.enroll.json')
        enrollment.write_text(json.dumps(dict(schema_version=1, server='https://example.invalid', enrollment_token='A'*43)))
        links = []
        try:
            assert setup(installer, install, root/'install.log') == 0, 'Installation failed'
            assert updates.file_digest(target) == updates.file_digest(built)
            assert (install/enrollment.name).read_bytes() == enrollment.read_bytes()
            links = shortcuts(name)
            for link in links:
                # Shell shortcuts expand 8.3 user-profile aliases to long paths.
                assert Path(link['target']).samefile(target) and Path(link['working']).samefile(install), link
            # A running recorder/helper makes even silent Setup stop, never kill it.
            with running():
                assert setup(installer, install, root/'running.log') != 0, 'Setup ignored a running recorder'
            assert setup(older, install, root/'downgrade.log') != 0, 'An old installer downgraded the recorder'
            assert updates.file_digest(target) == updates.file_digest(built)
            assert setup(installer, install, root/'reinstall.log') == 0, 'Same-version repair failed'
            # Exercise the real atomic updater against the installed primary EXE.
            smoke_update.main(built, installed_target=target)
            for before, after in zip(links, shortcuts(name)):
                assert before == after, 'An update changed a stable shortcut'
            result = root / 'reopened.json'
            subprocess.run([str(target), '--smoke-test', '--config', str(config), '--spool', str(spool),
                '--smoke-result', str(result)], check=True, timeout=45)
            assert json.loads(result.read_text())['complete'] is True
        finally:
            uninstaller = install / 'unins000.exe'
            if uninstaller.exists():
                subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                    check=True, timeout=60)
                deadline = time.monotonic() + 30
                while target.exists() and time.monotonic() < deadline: time.sleep(.1)
            enrollment.unlink(missing_ok=True)
        assert not target.exists(), 'Uninstall left the primary executable behind'
        assert all(not Path(link['path']).exists() for link in links), 'Uninstall left a shortcut behind'
        assert config.read_bytes() == saved_config, 'Installation changed the Steam link'
        queue = Spool(spool)
        try: assert queue.batch('existing-installation') == saved_batch, 'Installation changed queued data'
        finally: queue.close()
    print('Installer, stable shortcuts, primary-binary update, downgrade guard and data-safe uninstall passed')


if __name__ == '__main__': main()
