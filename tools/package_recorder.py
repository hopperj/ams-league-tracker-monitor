"""Build a dependency-free .pyz for distribution to drivers with Python 3.9+."""
from pathlib import Path
import shutil
import tempfile
import zipapp

root=Path(__file__).resolve().parents[1]
destination=root/'dist'/'AMSLeagueRecorder.pyz'
destination.parent.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    shutil.copytree(root/'ams_recorder',Path(directory)/'ams_recorder',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    zipapp.create_archive(directory,target=destination,main='ams_recorder.launcher:main',compressed=True)
print(destination)
