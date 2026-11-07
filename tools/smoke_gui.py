"""Exercise Tcl/Tk in the packaged executable without connecting or capturing."""
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    subprocess.run([str(Path(sys.argv[1]).resolve()), "--smoke-test", "--config", str(root/"config.json"),
                    "--spool", str(root/"queue.sqlite3")], check=True, timeout=45)
print("Packaged recorder window started and closed successfully")
