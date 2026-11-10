import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

from ams_recorder import updates
from ams_recorder.configuration import load_configuration, save_configuration
from ams_recorder.enrollment import prepare_enrolled
from ams_recorder.spool import Spool


def executable(payload=b'new recorder'):
    data=bytearray(128);data[:2]=b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:70]=b'PE\0\0\x64\x86'
    return bytes(data)+payload


def manifest(payload, version='0.7.0', **changes):
    return dict(schema_version=1, version=version, platform='windows-x64', filename=updates.ASSET,
        sha256=hashlib.sha256(payload).hexdigest(), size=len(payload), **changes)


class Opener:
    def __init__(self, body): self.body=body; self.requests=[]
    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(self.body)


class ReleaseTest(unittest.TestCase):
    def check(self, body, current='0.6.0'):
        return updates.check_release(current, Opener(json.dumps(body).encode()))

    def test_only_newer_stable_versions_from_the_fixed_publisher_are_used(self):
        body=manifest(executable(), version='0.10.0')
        body['url']='https://attacker.example/program.exe'
        release=self.check(body, '0.9.0')
        self.assertEqual(release.url, updates.REPOSITORY+'/releases/download/v0.10.0/'+updates.ASSET)
        self.assertIsNone(self.check(body, '0.10.0'))
        self.assertIsNone(self.check(body, '1.0.0'))
        for value in ('0.7.0-rc1', '../0.7.0', '01.2.3', 7, 'v0.7.0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(dict(body, version=value))

    def test_wrong_platform_filename_schema_size_and_digest_are_rejected(self):
        good=manifest(executable())
        for changes in ({'schema_version':True}, {'schema_version':2}, {'platform':'linux'},
                        {'filename':'other.exe'}, {'size':True}, {'size':updates.MAX_EXE+1},
                        {'sha256':'invalid'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(dict(good, **changes))
        with self.assertRaises(ValueError): updates.check_release(opener=Opener(b'x'*16385))

    def test_update_transport_never_sends_recorder_or_steam_credentials(self):
        opener=Opener(json.dumps(manifest(executable())).encode())
        updates.check_release(opener=opener)
        self.assertEqual(opener.requests[0].full_url, updates.LATEST)
        self.assertFalse(opener.requests[0].has_header('Authorization'))
        self.assertFalse(opener.requests[0].has_header('Cookie'))

    def test_redirects_cannot_leave_https_or_the_publisher_download_hosts(self):
        handler=updates.ReleaseRedirect()
        request=urllib.request.Request(updates.LATEST)
        for url in ('http://github.com/file', 'https://github.com.evil.example/file',
                    'https://user:password@github.com/file', 'https://github.com:8443/file',
                    'file:///C:/program.exe'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'Redirect', {}, url)
        self.assertIsNotNone(handler.redirect_request(request, None, 302, 'Redirect', {},
            'https://release-assets.githubusercontent.com/file?signature=value'))


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        folder=self.root/"Recorder's folder with spaces";folder.mkdir()
        self.target=folder/'AMSLeagueRecorder.exe'
        self.old=executable(b'old recorder');self.new=executable(b'new recorder')
        self.target.write_bytes(self.old)
        self.release=updates.Release('0.7.0', hashlib.sha256(self.new).hexdigest(), len(self.new))
        self.data=self.root/'LocalAppData'/'AMSLeagueRecorder';self.data.mkdir(parents=True)
        self.config=self.data/'config.json';self.spool=self.data/'spool.sqlite3'
        save_configuration(self.config,dict(server='https://league.example',token='private-upload-token',
            recorder_id='original-installation',driver_name='Racer',event_code='AUTO',account_linked=True,
            account_name='Original Steam racer',account_driver_id='steam-driver'))
        queue=Spool(self.spool);queue.bind('https://league.example','original-installation')
        queue.append('AUTO','original-run',{'sequence':1,'captured_at_ms':1000})
        self.batch=queue.batch('original-installation');queue.close()
        self.config_bytes=self.config.read_bytes()
        self.args=['--config',str(self.config),'--spool',str(self.spool)]

    def tearDown(self): self.temp.cleanup()

    def stage(self, payload=None, probe=None, cancel=None):
        return updates.prepare_install(self.release,self.target,self.data/'updates',cancel or threading.Event(),
            lambda text:None, restart_args=self.args, opener=Opener(self.new if payload is None else payload),
            probe_fn=probe or (lambda candidate,job:None))

    def assert_identity_preserved(self):
        self.assertEqual(self.config.read_bytes(),self.config_bytes)
        queue=Spool(self.spool)
        self.assertEqual(queue.batch('original-installation'),self.batch);queue.close()
        config=load_configuration(self.config)
        self.assertTrue(config['account_linked']);self.assertEqual(config['account_driver_id'],'steam-driver')
        # A generic update finds the existing link and never needs browser approval.
        with patch('ams_recorder.enrollment.Transport.request',side_effect=AssertionError('No Steam or registration request')):
            after,notice=prepare_enrolled(self.config,self.spool,self.root/'no-enrollment.json')
        self.assertTrue(after['account_linked']);self.assertIsNone(notice)

    def test_update_waits_then_atomically_installs_and_restarts_with_saved_paths(self):
        plan=self.stage();calls=[]
        self.assertEqual(self.target.read_bytes(),self.old)
        def wait(pid):
            calls.append('wait');self.assertEqual(self.target.read_bytes(),self.old)
        def start(target,args):
            calls.append('restart');self.assertEqual(target,self.target.resolve())
            self.assertEqual(args,self.args);self.assertEqual(target.read_bytes(),self.new)
        self.assertTrue(updates.apply_install(plan,wait=wait,start=start))
        self.assertEqual(calls,['wait','restart'])
        self.assertEqual(json.loads((plan.parent/'result.json').read_text())['status'],'installed')
        self.assertEqual(list((self.data/'updates').glob('*.active.json')),[])
        self.assert_identity_preserved()

    def test_corrupt_or_incomplete_download_never_replaces_or_runs_the_candidate(self):
        for payload in (self.new[:-1],self.new+b'extra',self.new[:-1]+b'X'):
            with self.subTest(payload=payload[-5:]):
                probe=[]
                with self.assertRaises(ValueError):self.stage(payload,probe=lambda *args:probe.append(args))
                self.assertEqual(probe,[]);self.assertEqual(self.target.read_bytes(),self.old)
                self.assertEqual(list((self.data/'updates').iterdir()),[])
        self.assert_identity_preserved()

    def test_window_probe_failure_and_skip_leave_the_original_app_and_link_intact(self):
        def fail(*args):raise RuntimeError('Packaged window could not start')
        with self.assertRaises(RuntimeError):self.stage(probe=fail)
        stop=threading.Event();stop.set()
        with self.assertRaises(updates.UpdateCancelled):self.stage(cancel=stop)
        self.assertEqual(self.target.read_bytes(),self.old)
        self.assert_identity_preserved()

    def test_tampering_after_download_is_rechecked_before_replacement(self):
        plan=self.stage();staged=Path(updates.read_plan(plan)['staged']);staged.write_bytes(executable(b'tampered'))
        starts=[]
        self.assertFalse(updates.apply_install(plan,wait=lambda pid:None,start=lambda *args:starts.append(args)))
        self.assertEqual(self.target.read_bytes(),self.old);self.assertEqual(len(starts),1)
        self.assert_identity_preserved()

    def test_failed_restart_rolls_back_and_reopens_the_previous_recorder_once(self):
        plan=self.stage();images=[]
        def start(target,args):
            images.append(target.read_bytes())
            if len(images)==1:raise OSError('Could not launch new recorder')
        self.assertFalse(updates.apply_install(plan,wait=lambda pid:None,start=start))
        self.assertEqual(images,[self.new,self.old]);self.assertEqual(self.target.read_bytes(),self.old)
        self.assert_identity_preserved()

    def test_parent_timeout_does_not_start_a_second_recorder_or_replace_the_running_one(self):
        plan=self.stage();starts=[]
        def wait(pid):raise updates.UpdateError('Still running')
        self.assertFalse(updates.apply_install(plan,wait=wait,start=lambda *args:starts.append(args)))
        self.assertEqual(starts,[]);self.assertEqual(self.target.read_bytes(),self.old)

    def test_second_update_cannot_take_over_an_active_job(self):
        first=self.stage()
        with self.assertRaises(updates.UpdateError):self.stage()
        self.assertTrue(first.exists())
        updates.discard_install(first)
        self.assertEqual(list((self.data/'updates').iterdir()),[])

    def test_install_plan_cannot_redirect_replacement_to_a_different_file(self):
        plan=self.stage();body=json.loads(plan.read_text());body['staged']=str(self.config)
        plan.write_text(json.dumps(body))
        with self.assertRaises(ValueError):updates.read_plan(plan)
        self.assertEqual(self.target.read_bytes(),self.old)
        self.assert_identity_preserved()

    def test_unsupported_executable_architecture_is_rejected_even_with_matching_hash(self):
        candidate=self.root/'wrong.exe';body=bytearray(self.new);body[68:70]=b'\x4c\x01';candidate.write_bytes(body)
        with self.assertRaises(ValueError):updates.verify_executable(candidate,hashlib.sha256(body).hexdigest())

    def test_restarts_use_arguments_without_a_shell_and_skip_one_update_check(self):
        with patch('ams_recorder.updates.subprocess.Popen') as start:
            updates.start_recorder(self.target,self.args)
        self.assertEqual(start.call_args.args[0],[str(self.target),'--skip-update',*self.args])
        self.assertNotIn('shell',start.call_args.kwargs)
