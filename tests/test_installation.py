import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from ams_recorder import installation
from tools.windows_version import resource


class InstallationTest(unittest.TestCase):
    def test_windows_installer_identifies_the_same_running_application_and_primary_binary(self):
        # These identifiers cross the Python / Inno Setup boundary.
        script=(Path(__file__).resolve().parents[1]/'installer'/'recorder.iss').read_text()
        self.assertIn(installation.APP_ID,script)
        self.assertIn('AppMutex='+installation.MUTEX,script)
        self.assertIn('DestName: "'+installation.INSTALLED_EXE+'"',script)
        self.assertIn('OutputBaseFilename='+Path(installation.INSTALLER_ASSET).stem,script)

    def test_windows_file_version_contains_numeric_release_components(self):
        constructors=('VSVersionInfo','FixedFileInfo','StringFileInfo','StringTable','StringStruct','VarFileInfo','VarStruct')
        def node(*args,**kwargs):return dict(args=args,**kwargs)
        tree=eval(resource('1.20.300'),{'__builtins__':{},**{name:node for name in constructors}})
        self.assertEqual(tree['ffi']['filevers'],(1,20,300,0))
        self.assertEqual(tree['ffi']['prodvers'],(1,20,300,0))
        for invalid in ('1.2.65536','1.2.3-rc1','01.2.3'):
            with self.subTest(version=invalid),self.assertRaises(ValueError):resource(invalid)

    @unittest.skipUnless(os.name=='nt','Windows installer coordination')
    def test_running_marker_remains_until_the_last_recorder_or_helper_exits(self):
        import ctypes as C
        from ctypes import wintypes as W
        api=C.WinDLL('kernel32',use_last_error=True)
        api.OpenMutexW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR];api.OpenMutexW.restype=W.HANDLE
        api.CloseHandle.argtypes=[W.HANDLE]
        def exists():
            handle=api.OpenMutexW(0x00100000,False,installation.MUTEX)
            if handle:api.CloseHandle(handle)
            return bool(handle)
        with patch.object(installation,'MUTEX','Local\\AMSRecorder.Test.'+uuid.uuid4().hex):
            self.assertFalse(exists())
            with installation.running():
                with installation.running():self.assertTrue(exists())
                self.assertTrue(exists())
            self.assertFalse(exists())
