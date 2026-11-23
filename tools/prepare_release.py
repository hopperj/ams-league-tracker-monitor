"""Prepare release downloads from an already checked Windows build (Python 3.12+)."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tomllib
import zipfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ams_recorder.installation import INSTALLER_ASSET

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = 'AMSLeagueRecorder-Windows-x64-Intel-AMD'
PORTABLE = 'AMSLeagueRecorder-Python-Portable.pyz'


def checked_payload(folder, filename):
    file = folder / filename
    payload = file.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    if (folder / (filename + '.sha256')).read_text().split() != [checksum, filename]:
        raise SystemExit('The checked Windows download does not match its checksum: ' + filename)
    if len(payload) < 70 or payload[:2] != b'MZ':
        raise SystemExit('Expected a Windows executable: ' + filename)
    pe = struct.unpack_from('<I', payload, 0x3c)[0]
    if pe < 64 or payload[pe:pe + 4] != b'PE\x00\x00' or pe + 6 > len(payload):
        raise SystemExit('Invalid Windows executable: ' + filename)
    return payload, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=ROOT / 'dist')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--build-url', required=True)
    parser.add_argument('--notes', type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    if args.tag != 'v' + version:
        raise SystemExit('Release tag must match the package version')
    folder = args.dist
    executable = folder / (WINDOWS + '.exe')
    sidecar = folder / (executable.name + '.sha256')
    payload, checksum = checked_payload(folder, executable.name)
    pe = struct.unpack_from('<I', payload, 0x3c)[0]
    if (payload[pe:pe + 4] != b'PE\x00\x00'
            or struct.unpack_from('<H', payload, pe + 4)[0] != 0x8664):
        raise SystemExit('Expected the Windows x64 executable for Intel and AMD')
    installer = folder / INSTALLER_ASSET
    setup_payload, setup_checksum = checked_payload(folder, installer.name)
    setup_sidecar = folder / (installer.name + '.sha256')
    manifest = folder / 'recorder-update.json'
    manifest.write_text(json.dumps(dict(schema_version=1, version=version, platform='windows-x64',
        filename=executable.name, sha256=checksum, size=len(payload)), indent=2) + '\n')
    # Keep the old app checksum for webapps serving legacy portable packages.
    # New webapps select the checked installer and attach the single-use setup.
    download_manifest = folder / 'manifest.json'
    download_manifest.write_text(json.dumps(dict(schema_version=1, version=version,
        enrollment_version=1, sha256=checksum, installer=dict(filename=installer.name,
        sha256=setup_checksum, size=len(setup_payload))), indent=2) + '\n')
    with zipfile.ZipFile(folder / PORTABLE) as portable:
        if portable.testzip() or '__main__.py' not in portable.namelist():
            raise SystemExit('Invalid portable Python archive')
    (folder / 'README.txt').write_bytes((ROOT / 'docs/windows-quickstart.txt').read_bytes())
    archive = folder / (WINDOWS + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for file in (installer, setup_sidecar, folder / 'README.txt'):
            package.write(file, arcname=file.name)
    assets = (archive, installer, setup_sidecar, executable, sidecar, folder / PORTABLE, manifest, download_manifest)
    (folder / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.name + '\n'
        for file in assets))
    args.notes.write_text(f'''## Downloads

- **Windows installer (Intel and AMD x64):** run `{installer.name}`, or extract `{archive.name}` and run Setup. Then use **AMS League Recorder** in the Start menu or its desktop shortcut. Python is included and administrator access is not required.
- **Existing recorder users:** install once to get a permanent application folder and shortcuts. Your saved Steam link, settings and queued recordings are reused. Stop using any old portable copies left in Downloads.
- **Portable Windows:** `{executable.name}` is still available for portable use and existing self-update clients.
- **Portable Python:** `{PORTABLE}` requires Python 3.9+ and Tk 8.6+. Live AMS2 shared-memory capture requires 64-bit Windows; synthetic capture and replay also work on macOS/Linux.
- **Integrity:** `SHA256SUMS` covers the downloads. The executable also has its own `.sha256` file.

## Recorder

Use **Open webapp** in the recorder's top bar to open your dashboard in the default browser while recording continues.

Use **Join server** beside it to launch AMS2 through Steam and join the current dedicated server. Private lobbies ask for a password without saving it. Recording and uploads continue during connection lookup and the password prompt.

**Queued on PC** shows samples waiting to upload from your computer. **Server processing** shows samples already saved on the webapp and waiting to be processed. Recording and uploads continue while the server catches up. Game replay snapshots are archived and excluded from live racing telemetry.

Open the recorder and approve **Link Steam account** in your browser before recording. Personalized downloads open approval automatically. No key is copied manually. The server verifies your link and upload access before capture starts, and checks communication every five seconds while waiting for AMS2. Existing saved credentials and queued data are preserved. Use **AUTO** for all driving sessions. League-night approval is managed separately on the website. The default server is https://ams2.ioresearch.ca. Enable AMS2's **Shared Memory: Project CARS 2** setting.

The light is **green** for **Comms open, awaiting race data**, **red** for **Recording in progress**, **amber** after three continuous seconds of trouble, and **gray** while checking or stopped. The status text explains problems immediately. Failed deliveries stay queued locally. Saved processing notes show **Data saved — review**. Older clients also require a Steam link to upload to the updated server.

The installed recorder updates its primary executable in place, so the same Start menu and desktop shortcuts open the updated version. It checks published updates at startup and every five minutes while open, verifies their checksum and packaged window, and installs only after recording stops. It keeps a recovery copy and rolls back on installation or launch failure. An old Setup download cannot downgrade an updated recorder. Steam linking, installation credentials, settings and queued bytes are preserved; an ordinary update does not require another Steam sign-in. Uninstall removes the application and shortcuts while retaining your saved recorder data. Source and portable Python runs are updated manually.

## Validation

[Recorder checks and Windows build]({args.build_url}) passed, including Win32 shared-memory access, UI tests, packaged window and updater checks, installation, stable shortcuts after self-update, downgrade refusal, and data-safe uninstall. Live AMS2 capture still needs validation on a game PC.
''')
    print('Release downloads and SHA-256 checksums prepared for ' + args.tag)


if __name__ == '__main__':
    main()
