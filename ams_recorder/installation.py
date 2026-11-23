"""Windows installer coordination; recorder data stays outside the app directory."""
from contextlib import contextmanager
import os
from pathlib import Path


APP_ID = '{6C1F7441-28F1-44C7-9E9E-B63270C585D0}'
MUTEX = r'Local\AMSLeagueRecorder.Running'
INSTALLED_EXE = 'AMSLeagueRecorder.exe'
INSTALLER_ASSET = 'AMSLeagueRecorder-Setup-Windows-x64-Intel-AMD.exe'


@contextmanager
def running():
    """Keep Setup/Uninstall away from an active recorder or update helper.

    This is an existence marker, not a single-instance lock. Windows keeps the
    mutex alive until every recorder/helper holding a handle has exited.
    """
    if os.name != 'nt':
        yield
        return
    import ctypes as C
    from ctypes import wintypes as W
    api = C.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
    api.CreateMutexW.restype = W.HANDLE
    api.CloseHandle.argtypes = [W.HANDLE]
    handle = api.CreateMutexW(None, False, MUTEX)
    if not handle:
        raise C.WinError(C.get_last_error())
    try:
        yield
    finally:
        api.CloseHandle(handle)


def refresh_display_version(target, version):
    """Reflect self-updates in Windows Apps, only for this registered install."""
    if os.name != 'nt': return
    import winreg
    key = r'Software\Microsoft\Windows\CurrentVersion\Uninstall' + '\\' + APP_ID + '_is1'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as registry:
            folder = winreg.QueryValueEx(registry, 'InstallLocation')[0]
            if Path(folder).resolve() == Path(target).resolve().parent:
                winreg.SetValueEx(registry, 'DisplayVersion', 0, winreg.REG_SZ, version)
    except (OSError, TypeError, ValueError):
        pass  # Portable runs and registry problems must not prevent recording.
