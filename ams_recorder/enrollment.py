"""Steam approval before recording, preserving existing credentials and queues."""
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.error

from .configuration import load_configuration, prepare_configuration, save_configuration
from .transport import Transport


def enrollment_path():
    executable=Path(sys.executable if getattr(sys,'frozen',False) else sys.argv[0]).resolve()
    return executable.with_name('AMSLeagueRecorder.enroll.json')


def read_enrollment(path):
    path=Path(path)
    if not path.exists():return None
    if path.stat().st_size>8192:raise ValueError('Personalized setup file is unreadable; use Link Steam account instead.')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or value.get('schema_version')!=1 or not isinstance(value.get('enrollment_token'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',value['enrollment_token']):
        raise ValueError('Personalized setup file is unreadable; use Link Steam account instead.')
    if not isinstance(value.get('server'),str):raise ValueError('Invalid personalized server')
    value['server']=Transport(value['server'],'validation-only').server
    return value


def prepare_enrolled(config_path,spool_path,setup_path=None):
    """A download may select a fresh destination, never retarget an existing queue."""
    notice=None
    try:setup=read_enrollment(setup_path or enrollment_path())
    except (OSError,ValueError,TypeError):setup=None;notice='Personalized setup could not be read. Use Link Steam account before recording.'
    server=setup['server'] if setup and not Path(config_path).exists() else None
    config=prepare_configuration(config_path,spool_path,server=server)
    if setup and not config.get('account_linked'):
        if setup['server']!=config['server']:return config,'This download targets another server. Your saved recorder is unchanged; use Link Steam account.'
        fingerprint=hashlib.sha256(setup['enrollment_token'].encode()).hexdigest()
        if config.get('enrollment_seen')!=fingerprint:
            config=dict(config,enrollment_token=setup['enrollment_token'],enrollment_seen=fingerprint)
            save_configuration(config_path,config)
    return config,notice


def connect(config_path,cancel,notify):
    """Notify on a worker thread while capture waits for server approval."""
    config=load_configuration(config_path);transport=Transport(config['server'],config['token'])
    payload=lambda body:gzip.compress(json.dumps(body).encode(),mtime=0)
    backoff=1;pairing=None;enrollment=config.get('enrollment_token');fallback=False
    while not cancel.is_set():
        try:
            if not pairing:
                if config.get('auto_registration'):transport.register(config['recorder_id'])
                result=transport.request('/api/recorders/link/start',payload({'enrollment_token':enrollment} if enrollment else {}))
                if result.get('status')!='linked':
                    pairing=result.get('pairing_id');url=result.get('approval_url')
                    # Browser destinations must stay on the recorder's configured origin.
                    if not isinstance(pairing,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',pairing) or url!=transport.server+'/#connect-recorder/'+pairing:
                        raise ValueError('Unexpected recorder approval address')
                    notify('open_browser',url)
                    notify('link_status','Approve this recorder in your browser to enable recording.')
                else:pairing=None
            else:
                result=transport.request('/api/recorders/link/status?pairing_id='+pairing)
            if result.get('status')=='linked':
                latest=load_configuration(config_path)
                if latest['recorder_id']!=config['recorder_id'] or latest['server']!=config['server']:raise ValueError('Recorder settings changed while linking')
                latest.update(account_linked=True,account_name=result.get('account_name','Steam racer'),account_driver_id=result.get('driver_id'))
                latest.pop('enrollment_token',None);save_configuration(config_path,latest)
                notify('link_complete',latest);return
            backoff=1
            if cancel.wait(3):return
        except urllib.error.HTTPError as error:
            if enrollment and error.code==400 and not fallback:
                enrollment=None;fallback=True;pairing=None
                notify('link_status','Personalized setup was used or expired. Sign in to connect this recorder.');continue
            message=getattr(error,'server_message','Steam connection is unavailable. Recording is paused; try again shortly.')
            notify('link_status',message)
            if error.code in (400,401,403,404,409):return
            if cancel.wait(backoff):return
            backoff=min(backoff*2,30)
        except (OSError,ValueError) as error:
            # Never display bearer/enrollment secrets.
            notify('link_status','Account connection unavailable. Recording is paused; retrying…')
            if cancel.wait(backoff):return
            backoff=min(backoff*2,30)
