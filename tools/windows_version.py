"""Generate the fixed Windows file version used to prevent installer downgrades."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ams_recorder import __version__
from ams_recorder.updates import version


def resource(release):
    parts = version(release) + (0,)
    if any(part > 65535 for part in parts):
        raise ValueError('Windows version components must fit in 16 bits')
    return f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'AMS League Recorder'),
    StringStruct('FileVersion', {release!r}),
    StringStruct('ProductName', 'AMS League Recorder'),
    StringStruct('ProductVersion', {release!r}),
    StringStruct('OriginalFilename', 'AMSLeagueRecorder.exe')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
'''


if __name__ == '__main__':
    path = Path(sys.argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resource(__version__), encoding='utf-8')
