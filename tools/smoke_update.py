"""Exercise the real Windows helper, file-lock retry, replacement and restart."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ams_recorder import __version__, updates
from ams_recorder.configuration import save_configuration
from ams_recorder.spool import Spool


def main():
    if os.name != 'nt': raise SystemExit('The native updater smoke check requires Windows')
    built=Path(sys.argv[1]).resolve()
    api=C.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
    api.CreateFileW.restype=W.HANDLE;api.CloseHandle.argtypes=[W.HANDLE]
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);install=root/"Recorder's folder with spaces";install.mkdir()
        target=install/'Recorder.exe';shutil.copyfile(built,target)
        data=root/'LocalAppData'/'AMSLeagueRecorder';data.mkdir(parents=True)
        config,spool=data/'config.json',data/'spool.sqlite3'
        save_configuration(config,dict(server='https://example.invalid',token='smoke-only-private-credential',
            recorder_id='smoke-installation',driver_name='Smoke racer',event_code='AUTO',account_linked=True,
            account_name='Previously linked racer',account_driver_id='original-steam-racer'))
        queue=Spool(spool);queue.bind('https://example.invalid','smoke-installation')
        queue.append('AUTO','saved-run',dict(sequence=1,captured_at_ms=1000))
        original_batch=queue.batch('smoke-installation');queue.close();original_config=config.read_bytes()
        release=updates.Release(__version__,updates.file_digest(built),built.stat().st_size)
        class LocalArtifact:
            def open(self,request,timeout):return built.open('rb')
        result_file=root/'restarted-window.json'
        restart=['--smoke-test','--config',str(config),'--spool',str(spool),
                 '--smoke-result',str(result_file)]
        plan=updates.prepare_install(release,target,data/'updates',threading.Event(),lambda text:None,
            restart_args=restart,opener=LocalArtifact())
        # Hold the installed image without FILE_SHARE_DELETE, as Windows does
        # while an old executable or scanner still has it open.
        handle=api.CreateFileW(str(target),0x80000000,3,None,3,128,None)
        if handle==W.HANDLE(-1).value:raise C.WinError(C.get_last_error())
        helper=host=None
        try:
            host=subprocess.Popen([str(target),'--smoke-test','--config',str(config),'--spool',str(spool)],
                creationflags=subprocess.CREATE_NO_WINDOW)
            body=updates.read_plan(plan);body['parent_pid']=host.pid;updates.write_json(plan,body)
            helper=updates.launch_helper(plan)
            time.sleep(2)
            api.CloseHandle(handle);handle=None
            if helper.wait(timeout=75)!=0:raise RuntimeError('Native update helper failed')
            host.wait(timeout=45)
            result=json.loads((plan.parent/'result.json').read_text())
            assert result['status']=='installed' and result['restart_pid']>0,result
            updates.wait_for_process(result['restart_pid'],timeout=45)
            assert json.loads(result_file.read_text())==dict(complete=True,version=__version__)
            assert updates.file_digest(target)==release.sha256
            assert config.read_bytes()==original_config
            queue=Spool(spool)
            try:assert queue.batch('smoke-installation')==original_batch
            finally:queue.close()
        finally:
            if handle is not None:api.CloseHandle(handle)
            for process in (helper,host):
                if process is not None and process.poll() is None:
                    process.terminate();process.wait(timeout=10)
    print('Native updater installed and restarted with Steam link and queued bytes preserved')


if __name__=='__main__':main()
