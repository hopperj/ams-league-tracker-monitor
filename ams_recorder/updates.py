"""Published Windows updates; installation credentials and queues are never edited."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from . import __version__

REPOSITORY = 'https://github.com/hopperj/ams-league-tracker-monitor'
ASSET = 'AMSLeagueRecorder-Windows-x64-Intel-AMD.exe'
MANIFEST = 'recorder-update.json'
LATEST = REPOSITORY + '/releases/latest/download/' + MANIFEST
MAX_EXE = 256 * 1024 * 1024
DOWNLOAD_SECONDS = 120
ALLOWED_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


class UpdateError(ValueError):
    pass


class UpdateCancelled(UpdateError):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise UpdateError('Invalid recorder release version')
    return tuple(int(part) for part in value.split('.'))


def trusted_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise UpdateError('Unexpected recorder update address')
    return url


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def release_opener():
    # This transport never receives the recorder token or a browser session.
    return urllib.request.build_opener(ReleaseRedirect())


def request(opener, url):
    return opener.open(urllib.request.Request(trusted_url(url),
        headers={'User-Agent': 'AMSLeagueRecorder/' + __version__, 'Cache-Control': 'no-cache'}), timeout=5)


@dataclass(frozen=True)
class Release:
    version: str
    sha256: str
    size: int

    @property
    def url(self):
        return REPOSITORY + '/releases/download/v' + self.version + '/' + ASSET


def check_release(current=__version__, opener=None):
    with request(opener or release_opener(), LATEST) as response:
        raw = response.read(16385)
    if len(raw) > 16384:
        raise UpdateError('Recorder update manifest is too large')
    body = json.loads(raw)
    if (not isinstance(body, dict) or type(body.get('schema_version')) is not int or body['schema_version'] != 1
            or body.get('platform') != 'windows-x64' or body.get('filename') != ASSET):
        raise UpdateError('Unsupported recorder update manifest')
    target = body.get('version')
    if version(target) <= version(current):
        return None
    digest, size = body.get('sha256'), body.get('size')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise UpdateError('Recorder update checksum is missing')
    if type(size) is not int or not 64 <= size <= MAX_EXE:
        raise UpdateError('Invalid recorder update size')
    return Release(target, digest, size)


def native_target():
    if os.name == 'nt' and getattr(sys, 'frozen', False) and Path(sys.executable).suffix.lower() == '.exe':
        return Path(sys.executable).resolve()
    return None


def cancelled(cancel):
    if cancel.is_set():
        raise UpdateCancelled('Recorder update skipped')


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify_executable(path, digest, size=None):
    path = Path(path)
    if size is not None and path.stat().st_size != size:
        raise UpdateError('Incomplete recorder update')
    if path.stat().st_size > MAX_EXE or file_digest(path) != digest:
        raise UpdateError('Recorder update checksum did not match')
    with path.open('rb') as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise UpdateError('Recorder update is not a Windows executable')
        offset = struct.unpack_from('<I', header, 0x3c)[0]
        if not 64 <= offset <= min(1024 * 1024, path.stat().st_size - 6):
            raise UpdateError('Invalid recorder executable header')
        source.seek(offset)
        pe = source.read(6)
        if pe != b'PE\x00\x00\x64\x86':
            raise UpdateError('Recorder update is not Windows x64')


def write_json(path, body):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.update-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(body, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def lock_path(root, target):
    key = hashlib.sha256(str(target).casefold().encode()).hexdigest()[:24]
    return Path(root) / (key + '.active.json')


def claim(root, target, job):
    path = lock_path(root, target)
    try:
        with path.open('x', encoding='utf-8') as output:
            json.dump({'job': str(job), 'created': time.time()}, output)
    except FileExistsError:
        # Helpers are bounded to minutes. Recover a marker left by a crash.
        if time.time() - path.stat().st_mtime > 600:
            path.unlink()
            return claim(root, target, job)
        raise UpdateError('Another recorder update is already pending')


def release_claim(job, target):
    path = lock_path(Path(job).parent, target)
    try:
        if json.loads(path.read_text())['job'] == str(job): path.unlink()
    except (OSError, ValueError, KeyError, TypeError):
        pass


def download(release, path, cancel, notify, opener=None):
    digest, total, started = hashlib.sha256(), 0, time.monotonic()
    with request(opener or release_opener(), release.url) as response, Path(path).open('xb') as output:
        while True:
            cancelled(cancel)
            if time.monotonic() - started > DOWNLOAD_SECONDS:
                raise UpdateError('Recorder update download timed out')
            block = response.read(256 * 1024)
            if not block: break
            total += len(block)
            if total > release.size: raise UpdateError('Recorder update exceeded its advertised size')
            digest.update(block)
            output.write(block)
            notify('Downloading recorder ' + release.version + '… ' + str(total * 100 // release.size) + '%')
        output.flush()
        os.fsync(output.fileno())
    if total != release.size or digest.hexdigest() != release.sha256:
        raise UpdateError('Recorder update download failed verification')
    verify_executable(path, release.sha256, release.size)


def probe(executable, job):
    # Exercise the actual packaged window with disposable data, never the user's
    # settings/queue. Smoke mode performs no registration or Steam/network work.
    with tempfile.TemporaryDirectory(prefix='probe-', dir=str(job)) as folder:
        root = Path(folder)
        result = root / 'window-result.json'
        subprocess.run([str(executable), '--smoke-test', '--config', str(root / 'config.json'),
            '--spool', str(root / 'spool.sqlite3'), '--smoke-result', str(result)], check=True, timeout=45,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.stat().st_size > 4096: raise UpdateError('Invalid recorder window check')
        body = json.loads(result.read_text())
        if body.get('complete') is not True: raise UpdateError('Recorder window check did not finish')
        version(body.get('version'))
        return body['version']


def prepare_install(release, target, updates_root, cancel, notify, restart_args=None, opener=None, probe_fn=probe):
    target, updates_root = Path(target).resolve(), Path(updates_root).resolve()
    updates_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    job = Path(tempfile.mkdtemp(prefix='update-', dir=str(updates_root)))
    staged = target.with_name('.' + target.name + '.' + job.name + '.exe')
    claimed = False
    try:
        claim(updates_root, target, job); claimed = True
        cancelled(cancel)
        candidate = job / 'candidate.exe'
        download(release, candidate, cancel, notify, opener)
        notify('Checking the new recorder window…')
        cancelled(cancel)
        if probe_fn(candidate, job) != release.version:
            raise UpdateError('Downloaded recorder version does not match its release')
        cancelled(cancel)
        # Preflight write permission and stage on the target's own filesystem;
        # LocalAppData and the executable can live on different drives.
        with staged.open('xb') as output, candidate.open('rb') as source:
            shutil.copyfileobj(source, output)
            output.flush(); os.fsync(output.fileno())
        verify_executable(staged, release.sha256, release.size)
        backup = job / 'helper.exe'
        shutil.copyfile(target, backup)
        old_digest = file_digest(target)
        verify_executable(backup, old_digest)
        cancelled(cancel)
        plan = dict(schema_version=1, target=str(target), staged=str(staged),
            parent_pid=os.getpid(), old_sha256=old_digest, sha256=release.sha256,
            size=release.size, version=release.version, restart_args=list(restart_args or []))
        write_json(job / 'plan.json', plan)
        return job / 'plan.json'
    except Exception:
        if staged.exists(): staged.unlink()
        if claimed: release_claim(job, target)
        shutil.rmtree(job, ignore_errors=True)
        raise


def read_plan(path):
    path = Path(path).resolve()
    if path.name != 'plan.json' or not re.fullmatch(r'update-[A-Za-z0-9_-]+', path.parent.name):
        raise UpdateError('Invalid recorder update plan')
    if path.stat().st_size > 16384: raise UpdateError('Recorder update plan is too large')
    body = json.loads(path.read_text())
    target, staged = Path(body['target']), Path(body['staged'])
    expected = target.with_name('.' + target.name + '.' + path.parent.name + '.exe')
    if (body.get('schema_version') != 1 or not target.is_absolute() or target.suffix.lower() != '.exe'
            or staged != expected or type(body.get('parent_pid')) is not int or body['parent_pid'] <= 0
            or not isinstance(body.get('restart_args'), list)
            or any(not isinstance(x, str) or '\x00' in x or len(x) > 4096 for x in body['restart_args'])
            or len(body['restart_args']) > 32):
        raise UpdateError('Invalid recorder update plan fields')
    version(body.get('version'))
    for key in ('old_sha256', 'sha256'):
        if not isinstance(body.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', body[key]):
            raise UpdateError('Invalid recorder update plan checksum')
    if type(body.get('size')) is not int or not 64 <= body['size'] <= MAX_EXE:
        raise UpdateError('Invalid recorder update plan size')
    return body


def launch_helper(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = read_plan(plan_path)
    verify_executable(plan_path.parent / 'helper.exe', plan['old_sha256'])
    return subprocess.Popen([str(plan_path.parent / 'helper.exe'), '--apply-update', str(plan_path)],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


def wait_for_process(pid, timeout=120):
    if os.name != 'nt': raise UpdateError('Executable updates require Windows')
    import ctypes as C
    from ctypes import wintypes as W
    api = C.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; api.OpenProcess.restype = W.HANDLE
    api.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; api.WaitForSingleObject.restype = W.DWORD
    api.CloseHandle.argtypes = [W.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only; never terminate a recorder.
    if not handle:
        if C.get_last_error() == 87: return  # The old process already exited.
        raise UpdateError('Could not wait for the old recorder to close')
    try:
        if api.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            raise UpdateError('The old recorder has not closed; update cancelled')
    finally:
        api.CloseHandle(handle)


def start_recorder(target, args):
    return subprocess.Popen([str(target), '--skip-update', *args],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


def apply_install(plan_path, wait=wait_for_process, start=start_recorder, replace=os.replace):
    plan_path = Path(plan_path).resolve(); job = plan_path.parent
    plan = read_plan(plan_path)
    target, staged, backup = Path(plan['target']), Path(plan['staged']), job / 'helper.exe'
    installed = can_restart = False
    def replace_image(source):
        for attempt in range(30):
            try:
                replace(source, target)
                return
            except PermissionError:
                if attempt == 29: raise
                time.sleep(1)
    try:
        wait(plan['parent_pid'])
        verify_executable(backup, plan['old_sha256'])
        if file_digest(target) != plan['old_sha256']:
            raise UpdateError('The installed recorder changed during the update')
        can_restart = True
        verify_executable(staged, plan['sha256'], plan['size'])
        # A one-file launcher's parent or antivirus can release the old image
        # shortly after its GUI exits. Retry the atomic replacement briefly.
        replace_image(staged)
        installed = True
        verify_executable(target, plan['sha256'], plan['size'])
        write_json(job / 'result.json', dict(status='installed', version=plan['version']))
        restarted = start(target, plan['restart_args'])
        try:
            write_json(job / 'result.json', dict(status='installed', version=plan['version'],
                                               restart_pid=getattr(restarted, 'pid', None)))
        except OSError:
            pass  # A running new process must not be rolled back for a metadata-write failure.
        return True
    except Exception:
        if installed:
            rollback = target.with_name('.' + target.name + '.' + job.name + '.rollback.exe')
            shutil.copyfile(backup, rollback)
            verify_executable(rollback, plan['old_sha256'])
            replace_image(rollback)
        try:
            write_json(job / 'result.json', dict(status='failed', version=plan['version']))
        except OSError:
            pass
        # If installation/restart failed, reopen the preserved version once.
        # --skip-update prevents a repeated failure/restart loop.
        if can_restart and target.exists(): start(target, plan['restart_args'])
        return False
    finally:
        if staged.exists(): staged.unlink()
        release_claim(job, target)


def discard_install(plan_path):
    path = Path(plan_path).resolve(); plan = read_plan(path)
    staged = Path(plan['staged'])
    if staged.exists(): staged.unlink()
    release_claim(path.parent, Path(plan['target']))
    shutil.rmtree(path.parent, ignore_errors=True)


def cleanup(updates_root):
    root = Path(updates_root)
    if not root.is_dir(): return
    for job in root.glob('update-*'):
        if job.is_symlink() or not job.is_dir(): continue
        result = job / 'result.json'
        if result.is_file() and time.time() - result.stat().st_mtime > 7 * 86400:
            shutil.rmtree(job, ignore_errors=True)


def helper_main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if os.name != 'nt' or len(args) != 2 or args[0] != '--apply-update':
        raise SystemExit('Invalid Windows recorder update helper invocation')
    raise SystemExit(0 if apply_install(args[1]) else 1)
