import hashlib
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
import zipfile

from ams_recorder import __version__, updates
from test_updates import executable, Opener


class ReleaseAssetsTest(unittest.TestCase):
    @unittest.skipUnless(sys.version_info >= (3, 12), 'Release packaging runs on Python 3.12')
    def test_published_manifest_identifies_the_checked_executable_and_is_checksummed(self):
        from tools.prepare_release import main
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);payload=executable()
            (folder/updates.ASSET).write_bytes(payload)
            (folder/(updates.ASSET+'.sha256')).write_text(hashlib.sha256(payload).hexdigest()+'  '+updates.ASSET+'\n')
            with zipfile.ZipFile(folder/'AMSLeagueRecorder-Python-Portable.pyz','w') as archive:
                archive.writestr('__main__.py','print("portable")\n')
            with patch('sys.argv',['prepare_release','--dist',str(folder),'--tag','v'+__version__,
                    '--build-url','https://github.com/hopperj/ams-league-tracker-monitor/actions/runs/1',
                    '--notes',str(folder/'release-notes.md')]):
                main()
            raw=(folder/updates.MANIFEST).read_bytes()
            body=json.loads(raw)
            self.assertEqual(body['version'],__version__)
            self.assertEqual(body['sha256'],hashlib.sha256(payload).hexdigest())
            self.assertEqual(body['size'],len(payload))
            release=updates.check_release('0.0.0',Opener(raw))
            self.assertEqual(release.url,updates.REPOSITORY+'/releases/download/v'+__version__+'/'+updates.ASSET)
            sums=(folder/'SHA256SUMS').read_text()
            self.assertIn(hashlib.sha256(raw).hexdigest()+'  '+updates.MANIFEST,sums)
