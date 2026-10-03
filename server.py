"""Loopback-only authoring UI. Source media stays on this Mac."""
from pathlib import Path
import json, secrets, threading, time, uuid, os
from flask import Flask,request,jsonify,send_file,abort
from author import ROOT,Job,build,probe,pages_for,render,rects_for
from bluray import neighbours
from menu_feedback import button_overlay,feedback_config
from paths import DATA_ROOT
from localization import translate,CATALOG
from queue_manager import QueueManager, QueueConflict, job_is_active
from burner import BurnManager, detect_devices, inspect_iso
from iso_export import job_root
from direct_burn import DirectBurnManager
import disc_reader
app=Flask(__name__,static_folder='web',static_url_path='/assets')
app.config['MAX_CONTENT_LENGTH']=16*1024*1024
TOKEN=os.environ.get('DISCAUTHOR_NATIVE_TOKEN') or secrets.token_urlsafe(32);jobs={};lock=threading.RLock();media_files={}
queue_manager=QueueManager(DATA_ROOT/'queue.json',jobs,lock,DATA_ROOT/'output')
burn_manager=BurnManager(DATA_ROOT)
direct_burn_manager=DirectBurnManager(DATA_ROOT,burn_manager,jobs,lock,queue_manager)
def media_url(path):
    path=str(Path(path).expanduser().resolve())
    for ident,saved in media_files.items():
        if saved==path:return "/media/"+ident
    ident=uuid.uuid4().hex;media_files[ident]=path
    return "/media/"+ident
@app.get("/media/<ident>")
def preview_media(ident):
    path=media_files.get(ident)
    if not path:abort(404)
    return send_file(path,conditional=True)
VIDEOS={'.mp4','.mov','.mkv','.m4v','.avi','.mts','.m2ts','.mpg','.mpeg','.vob','.webm','.mxf'}
IMAGES={'.jpg','.jpeg','.png','.webp','.tif','.tiff'}
@app.before_request
def local_only():
    if request.host.split(':')[0] not in ['127.0.0.1','localhost']:abort(403)
    if request.path.startswith('/api/') and request.headers.get('X-DiscAuthor-Token')!=TOKEN:abort(403)
    if request.method!='GET' and request.headers.get('Origin') and request.headers['Origin']!=request.host_url.rstrip('/'):abort(403)
def translate_lines(message,language):
    return '\n'.join(translate(line,language) for line in message.split('\n'))
@app.errorhandler(Exception)
def error(e):return jsonify(error=translate_lines(str(e),request.headers.get('X-DiscAuthor-Language','ja'))),getattr(e,'code',400)
@app.get('/')
def index():
    return jsonify(application='DiscAuthor Community',interface='native',version='0.4.16')
@app.get('/api/fonts')
def font_list():
    from fonts import installed,bundled
    path=DATA_ROOT/'fonts.json'
    if request.args.get('refresh') or not path.exists():path.write_text(json.dumps(installed(),ensure_ascii=False))
    local=[font for font in json.loads(path.read_text()) if not font['path'].startswith('builtin:')]
    return jsonify(fonts=bundled()+local)
@app.get('/api/preset')
def preset():
    c=json.loads((ROOT/'assets/default-project.json').read_text());c['output_dir']=str(DATA_ROOT/'output')
    settings=DATA_ROOT/'settings.json'
    if settings.exists():
        saved=json.loads(settings.read_text())
        if saved.get('output_dir'):c['output_dir']=saved['output_dir']
    return jsonify(c)
@app.post('/api/reveal/<ident>')
def reveal(ident):
    import subprocess
    j=jobs[ident];target=j.iso or str(j.out)
    if not Path(target).exists():raise ValueError('输出尚未创建')
    subprocess.Popen(['/usr/bin/open','-R',target]);return jsonify(ok=True)
def localized_snapshot(job):
    snapshot=job.snapshot();language=request.headers.get('X-DiscAuthor-Language','ja')
    for key in ('message','error'):
        if snapshot.get(key):snapshot[key]=translate(snapshot[key],language)
    return snapshot
@app.get('/health')
def health():return jsonify(application='DiscAuthor Community',version='0.4.16')
@app.get('/api/status')
def status():
    with lock: snapshots={k:localized_snapshot(v) for k,v in jobs.items()}
    return jsonify(root=str(ROOT),output=str(DATA_ROOT/'output'),jobs=snapshots,dependencies={str(p.relative_to(ROOT)):p.exists() for p in [ROOT/'bin/tsmuxer',ROOT/'bin/mkisofs',ROOT/'deps/bin/dvdauthor',ROOT/'deps/bin/spumux']})
@app.get('/api/browse')
def browse():
    p=Path(request.args.get('path') or Path.home()).expanduser().resolve()
    if not p.is_dir():raise ValueError('目录不存在')
    kind=request.args.get('kind','video');entries=[]
    for f in p.iterdir():
        if f.name.startswith('.'):continue
        try:
            directory=f.is_dir()
            if directory or kind!='directory' and f.suffix.lower() in (IMAGES if kind=='image' else VIDEOS):entries.append({'name':f.name,'path':str(f),'dir':directory})
        except OSError:continue
    return jsonify(path=str(p),parent=str(p.parent),entries=sorted(entries,key=lambda x:(not x['dir'],x['name'].lower())))
@app.post('/api/probe')
def inspect():
    paths=request.json.get('paths',[])
    if not isinstance(paths,list) or len(paths)>30:raise ValueError('最多添加 30 个文件')
    return jsonify(videos=[{'path':str(Path(p).expanduser().resolve()),'title':Path(p).stem,'preview_url':media_url(p),'info':probe(Path(p).expanduser())} for p in paths])
@app.post('/api/preview')
def preview():
    c=request.json
    if c.get('menu_enabled', True) is False:
        return jsonify(menu_enabled=False,image=None,buttons=[],page=0,pages=0,rects=[])
    if not c.get('videos'):
        c['videos']=[{'title':translate('节目 {n}',c.get('menu_language','ja'),n=i+1)} for i in range(c.get('per_page',4))]
    p=int(c.pop('page',0));pages=pages_for(c)
    if not 0<=p<len(pages):raise ValueError('页码无效')
    out=DATA_ROOT/'output'/'preview';out.mkdir(exist_ok=True,parents=True)
    name=uuid.uuid4().hex+'.png';render(c,pages[p],p,out/name)
    buttons=[]
    for i,button in enumerate(pages[p]):
        layers={}
        for state in ('selected','activated'):
            filename=uuid.uuid4().hex+'.png';button_overlay(button,state,c).save(out/filename);layers[state]='/preview/'+filename
        buttons.append(dict(button,nav=neighbours(pages[p],i),feedback=layers))
    # Only cache files generated by this endpoint; project assets live elsewhere.
    cached=sorted((f for f in out.glob('*.png') if len(f.name)==36),key=lambda f:f.stat().st_mtime,reverse=True)
    for stale in cached[300:]:
        try:stale.unlink()
        except OSError:pass
    return jsonify(image='/preview/'+name,buttons=buttons,page=p,pages=len(pages),rects=rects_for(c,sum('title' in b for b in buttons)),feedback=feedback_config(c))
@app.get('/preview/<name>')
def preview_file(name):
    if not name.endswith('.png') or len(name)!=36:abort(404)
    return send_file(DATA_ROOT/'output/preview'/name)
@app.post('/api/preflight')
def preflight():
    from preflight import check_project
    return jsonify(check_project(request.json))
@app.post('/api/build')
def start():
    with lock:
        if queue_manager.busy_locked() or any(job_is_active(j) for j in jobs.values()):raise QueueConflict('已有制作任务运行中')
        c=request.json
        from preflight import check_project
        check_project(c)
        ident=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:4]
        base=Path(c.get('output_dir') or DATA_ROOT/'output').expanduser().resolve();base.mkdir(parents=True,exist_ok=True)
        (DATA_ROOT/'settings.json').write_text(json.dumps({'output_dir':str(base)}))
        out=job_root(c,base)/ident;out.parent.mkdir(parents=True,exist_ok=True)
        j=Job(c,out);j._service_active=True;jobs[ident]=j
        def legacy_build():
            try:build(j)
            finally:
                with lock:j._service_active=False
        try:threading.Thread(target=legacy_build,daemon=True).start()
        except Exception:
            del jobs[ident]
            raise
    return jsonify(id=ident)

def localized_queue(snapshot):
    language=request.headers.get('X-DiscAuthor-Language','ja')
    if snapshot.get('error'):snapshot['error']=translate(snapshot['error'],language)
    for item in snapshot['items']:
        for key in ('message','error'):
            if item.get(key):item[key]=translate(item[key],language)
    return jsonify(snapshot)

@app.get('/api/queue')
def queue_status():return localized_queue(queue_manager.snapshot())

@app.post('/api/queue')
def queue_add():
    payload=request.get_json()
    if not isinstance(payload,dict):raise ValueError('项目配置必须是对象')
    return localized_queue(queue_manager.add(payload.get('projects')))

@app.post('/api/queue/start')
def queue_start():return localized_queue(queue_manager.start())

@app.post('/api/queue/pause')
def queue_pause():return localized_queue(queue_manager.pause())

@app.post('/api/queue/<ident>/move')
def queue_move(ident):return localized_queue(queue_manager.move(ident,(request.get_json() or {}).get('direction')))

@app.post('/api/queue/<ident>/retry')
def queue_retry(ident):return localized_queue(queue_manager.retry(ident))

@app.post('/api/queue/<ident>/cancel')
def queue_cancel(ident):return localized_queue(queue_manager.cancel(ident))

@app.post('/api/queue/<ident>/pause')
def queue_pause_item(ident):return localized_queue(queue_manager.pause_item(ident))

@app.post('/api/queue/<ident>/resume')
def queue_resume_item(ident):return localized_queue(queue_manager.resume_item(ident))

@app.delete('/api/queue/<ident>')
def queue_remove(ident):return localized_queue(queue_manager.remove(ident))

@app.get('/api/queue/<ident>/log')
def queue_log(ident):return jsonify(text=queue_manager.log(ident))

@app.post('/api/queue/<ident>/reveal')
def queue_reveal(ident):
    import subprocess
    subprocess.Popen(['/usr/bin/open','-R',queue_manager.reveal_target(ident)])
    return jsonify(ok=True)

def localized_burn(snapshot):
    language=request.headers.get('X-DiscAuthor-Language','ja')
    job=snapshot.get('job')
    if job:
        for key in ('message','error','cleanup_error'):
            if job.get(key):job[key]=translate_lines(job[key],language)
    return jsonify(snapshot)

@app.get('/api/burn/devices')
def burn_devices():return jsonify(burn_manager.devices())

@app.post('/api/burn/inspect')
def burn_inspect():return jsonify(burn_manager.inspector((request.get_json() or {}).get('iso')))

@app.post('/api/burn/check')
def burn_check():
    payload=request.get_json() or {}
    result=burn_manager.check(payload.get('iso'),payload.get('device_id'),payload.get('speed'),
        verify_after_burn=payload.get('verify_after_burn',True))
    language=request.headers.get('X-DiscAuthor-Language','ja')
    result['errors']=[translate(message,language) for message in result['errors']]
    return jsonify(result)

@app.post('/api/burn/start')
def burn_start():
    payload=request.get_json() or {}
    with lock:
        if direct_burn_manager.snapshot()['active']:raise QueueConflict('已有视频直刻任务运行中')
        return localized_burn(burn_manager.start(payload.get('iso'),payload.get('device_id'),payload.get('speed'),
            verify_after_burn=payload.get('verify_after_burn',True)))

@app.get('/api/burn/status')
def burn_status():return localized_burn(burn_manager.snapshot())

@app.post('/api/burn/tray')
def burn_tray():
    payload=request.get_json() or {}
    with lock:
        return jsonify(burn_manager.control_tray(payload.get('device_id'),payload.get('action'),
            direct_active=direct_burn_manager.snapshot()['active']))

@app.post('/api/burn/cancel')
def burn_cancel():return localized_burn(burn_manager.cancel())

@app.get('/api/burn/log')
def burn_log():return jsonify(text=burn_manager.log())

@app.post('/api/direct-burn/check')
def direct_burn_check():
    result=direct_burn_manager.preflight(request.get_json())
    language=request.headers.get('X-DiscAuthor-Language','ja')
    result['errors']=[translate_lines(message,language) for message in result['errors']]
    return jsonify(result)

@app.post('/api/direct-burn/start')
def direct_burn_start():return localized_burn(direct_burn_manager.start(request.get_json()))

@app.post('/api/direct-burn/retry')
def direct_burn_retry():return localized_burn(direct_burn_manager.retry(request.get_json()))

@app.get('/api/direct-burn/status')
def direct_burn_status():return localized_burn(direct_burn_manager.snapshot())

@app.post('/api/direct-burn/cancel')
def direct_burn_cancel():return localized_burn(direct_burn_manager.cancel())

@app.get('/api/direct-burn/log')
def direct_burn_log():return jsonify(text=direct_burn_manager.log())

@app.get('/api/discs')
def readable_discs():
    result=disc_reader.detect_discs()
    language=request.headers.get('X-DiscAuthor-Language','ja')
    if result.get('error'):result['error']=translate_lines(result['error'],language)
    for device in result['devices']:
        if device.get('reason'):device['reason']=translate_lines(device['reason'],language)
        if device.get('detail'):device['detail']=translate_lines(device['detail'],language)
    return jsonify(result)

@app.post('/api/discs/inspect')
def inspect_disc_source():return jsonify(disc_reader.inspect_source((request.get_json() or {}).get('path')))

@app.post('/api/discs/open')
def open_readable_disc():
    with lock:
        if burn_manager.snapshot()['active'] or direct_burn_manager.snapshot()['active']:
            raise QueueConflict('已有烧录任务运行中')
        return jsonify(disc_reader.resolve_disc((request.get_json() or {}).get('device_id')))
@app.post('/api/cancel/<ident>')
def cancel(ident):
    jobs[ident].cancel.set();return jsonify(ok=True)
@app.get('/api/log/<ident>')
def log(ident):
    p=jobs[ident].out/'build.log'
    return jsonify(text=p.read_text(errors='replace')[-16000:] if p.exists() else '')
@app.post('/api/save')
def save():
    (DATA_ROOT/'projects').mkdir(exist_ok=True)
    name=time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(2)+'.json';p=DATA_ROOT/'projects'/name;p.write_text(json.dumps(request.json,ensure_ascii=False,indent=2));return jsonify(path=str(p),name=name)
@app.get('/api/projects')
def projects():
    p=DATA_ROOT/'projects';return jsonify(files=[f.name for f in sorted(p.glob('*.json'),reverse=True) if not f.name.startswith('._')])
@app.get('/api/projects/<name>')
def project(name):
    if Path(name).name!=name or not name.endswith('.json'):abort(400)
    c=json.loads((DATA_ROOT/'projects'/name).read_text())
    aliases=DATA_ROOT/'asset-aliases.json'
    if c.get('background') and aliases.exists():
        destination=json.loads(aliases.read_text()).get(c['background'])
        if destination and Path(destination).is_file():c['background']=destination
    return jsonify(c)
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8779);args=parser.parse_args()
    from waitress import serve
    serve(app,host='127.0.0.1',port=args.port,threads=6)
