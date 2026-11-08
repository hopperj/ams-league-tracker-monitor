"""Upload the requested full-rate synthetic Spa race with resumable recorder streams."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ams_recorder.scenarios import SpaRace, NAMES, START, START_MS

TITLE='SYNTHETIC · Spa / Cadillac DPi · 2026-11-05 21:00 AST'
STEM='spa-cadillac-2026-11-05-v1'


def write_json(path,value):
    temporary=path.with_suffix('.next')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    os.replace(temporary,path)


def request(args,path,token,body=None,compressed=None):
    headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
    if args.host_header:headers['Host']=args.host_header
    payload=None
    if compressed is not None:
        payload=compressed;headers.update({'Content-Type':'application/json','Content-Encoding':'gzip'})
    elif body is not None:
        payload=json.dumps(body).encode();headers['Content-Type']='application/json'
    req=urllib.request.Request(args.server.rstrip('/')+path,data=payload,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=90) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        try:detail=json.load(error).get('error','Request failed')
        finally:error.close()
        raise RuntimeError('HTTP '+str(error.code)+': '+detail.replace(token,'[redacted]')) from None


def upload_driver(args,identity,index,code):
    os.umask(0o077)
    checkpoint=Path(args.work)/('driver-'+str(index)+'.json')
    progress=json.loads(checkpoint.read_text()) if checkpoint.exists() else {'next_sequence':0,'batches':0,'archived_bytes':0,'warnings':{}}
    scenario=SpaRace();total=scenario.sample_count(args.hz)
    run_id=str(uuid.uuid5(uuid.NAMESPACE_URL,STEM+':'+code+':'+NAMES[index]))
    began=time.monotonic();reported=-1
    while progress['next_sequence']<total:
        first=progress['next_sequence'];last=min(first+120,total)
        frames=[scenario.frame(sequence,index,args.hz) for sequence in range(first,last)]
        batch_id=str(uuid.uuid5(uuid.UUID(run_id),str(first)+':'+str(last)))
        body={'schema_version':1,'batch_id':batch_id,'recorder_id':identity['recorder_id'],'run_id':run_id,
              'event_code':code,'created_at_ms':frames[-1]['captured_at_ms']+1000,'frames':frames}
        payload=gzip.compress(json.dumps(body,separators=(',',':'),allow_nan=False).encode(),compresslevel=6,mtime=0)
        for attempt in range(8):
            try:
                ack=request(args,'/api/batches',identity['token'],compressed=payload)
                if ack.get('batch_id')!=batch_id or ack.get('stored') is not True or ack.get('accepted') is not True:
                    raise RuntimeError('Server did not confirm durable storage')
                break
            except (OSError,ValueError,RuntimeError) as error:
                if attempt==7:raise
                print(json.dumps({'driver':NAMES[index],'retry':attempt+1,'error':str(error).replace(identity['token'],'[redacted]')}),flush=True)
                time.sleep(min(2**attempt,30))
        for issue in ack.get('warnings',[]):
            progress['warnings'][issue['code']]=progress['warnings'].get(issue['code'],0)+issue.get('count',1)
        progress.update(next_sequence=last,batches=progress['batches']+1,archived_bytes=progress['archived_bytes']+len(payload))
        write_json(checkpoint,progress)
        percent=last*100//total
        if percent//5!=reported:
            reported=percent//5
            print(json.dumps({'driver':NAMES[index],'percent':percent,'samples':last,'total':total,'batches':progress['batches'],'seconds':round(time.monotonic()-began)}),flush=True)
        if args.max_batches and progress['batches']>=args.max_batches:break
    return {'driver':NAMES[index],**progress,'total':total}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True)
    parser.add_argument('--host-header')
    parser.add_argument('--admin-token-file',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--hz',type=int,default=60)
    parser.add_argument('--max-batches',type=int,help='Development benchmark: stop each stream after this many batches')
    args=parser.parse_args()
    if args.hz<1 or args.hz>120:parser.error('Use a sampling rate between 1 and 120 Hz')
    os.umask(0o077);args.work.mkdir(parents=True,exist_ok=True,mode=0o700)
    admin=args.admin_token_file.read_text().strip()
    state_file=args.work/'state.json'
    if state_file.exists():
        state=json.loads(state_file.read_text())
        if state['server']!=args.server or state['hz']!=args.hz:raise RuntimeError('Checkpoint destination/rate mismatch')
    else:
        events=request(args,'/api/events',admin)['events']
        if any(e['title']==TITLE for e in events):raise RuntimeError('Scenario already exists; resume its existing private checkpoint directory')
        event=request(args,'/api/events',admin,{'title':TITLE,'phase':'race','synthetic':True,'points':[25,18,15]})
        identities=[request(args,'/api/recorders',admin,{'name':name}) for name in NAMES]
        state={'server':args.server,'hz':args.hz,'code':event['code'],'identities':identities,'title':TITLE,'start':START.isoformat()}
        write_json(state_file,state)
    scenario=SpaRace()
    print(json.dumps({'event':state['code'],'title':TITLE,'start':START.isoformat(),'utc_ms':START_MS,'hz_per_driver':args.hz,'samples_per_driver':scenario.sample_count(args.hz),'total_samples':scenario.sample_count(args.hz)*3,'snapshot_bytes':24576,'published_fields':158}),flush=True)
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(upload_driver,args,identity,index,state['code']) for index,identity in enumerate(state['identities'])]
        results=[future.result() for future in as_completed(futures)]
    result={'event_code':state['code'],'title':TITLE,'race_start_at':START.isoformat(),'hz_per_driver':args.hz,
            'samples_per_driver':scenario.sample_count(args.hz),'drivers':sorted(results,key=lambda r:NAMES.index(r['driver'])),
            'complete':all(r['next_sequence']==r['total'] for r in results),'synthetic':True,'kept_open_for_review':True}
    write_json(args.work/'result.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
