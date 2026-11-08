"""Prepare release downloads from an already checked Windows build (Python 3.12+)."""
import argparse
import hashlib
from pathlib import Path
import struct
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = 'AMSLeagueRecorder-Windows-x64-Intel-AMD'
PORTABLE = 'AMSLeagueRecorder-Python-Portable.pyz'


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
    payload = executable.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    if sidecar.read_text().split() != [checksum, executable.name]:
        raise SystemExit('The checked Windows executable does not match its checksum')
    if payload[:2] != b'MZ':
        raise SystemExit('Expected a Windows executable')
    pe = struct.unpack_from('<I', payload, 0x3c)[0]
    if (payload[pe:pe + 4] != b'PE\x00\x00'
            or struct.unpack_from('<H', payload, pe + 4)[0] != 0x8664):
        raise SystemExit('Expected the Windows x64 executable for Intel and AMD')
    with zipfile.ZipFile(folder / PORTABLE) as portable:
        if portable.testzip() or '__main__.py' not in portable.namelist():
            raise SystemExit('Invalid portable Python archive')
    (folder / 'README.txt').write_bytes((ROOT / 'docs/windows-quickstart.txt').read_bytes())
    archive = folder / (WINDOWS + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for file in (executable, sidecar, folder / 'README.txt'):
            package.write(file, arcname=file.name)
    assets = (archive, executable, sidecar, folder / PORTABLE)
    (folder / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.name + '\n'
        for file in assets))
    args.notes.write_text(f'''## Downloads

- **Windows x64 (Intel and AMD):** download `{archive.name}`, extract it, and run `{executable.name}`. Python is included. The standalone `.exe` is also available below.
- **Portable Python:** `{PORTABLE}` requires Python 3.9+ and Tk 8.6+. Live AMS2 shared-memory capture requires 64-bit Windows; synthetic capture and replay also work on macOS/Linux.
- **Integrity:** `SHA256SUMS` covers the downloads. The executable also has its own `.sha256` file.

## Recorder

Configure your recorder upload token. Leave the capture code blank or use **AUTO** to record all driving sessions, including solo practice across different tracks and cars. Existing explicit capture codes remain supported. League-night approval is managed separately on the website. The default server is https://ams2.ioresearch.ca:5443. Enable AMS2's **Shared Memory: Project CARS 2** setting.

The window shows a red/green delivery light. Failed deliveries stay queued locally; uploads saved with processing notes show **Data saved — review** and continue transmitting.

## Validation

[Recorder checks and Windows build]({args.build_url}) passed, including Win32 shared-memory access, the UI tests, and the packaged window startup check. Live AMS2 capture still needs validation on a game PC.
''')
    print('Release downloads and SHA-256 checksums prepared for ' + args.tag)


if __name__ == '__main__':
    main()
