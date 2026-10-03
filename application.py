"""Local authoring service, owned by the native macOS app."""
import os,json,threading,webbrowser,urllib.request,logging,signal,time
from paths import DATA_ROOT
from server import app,jobs,lock,queue_manager,burn_manager,direct_burn_manager
from waitress import create_server
logging.basicConfig(filename=str(DATA_ROOT/'application.log'),level=logging.INFO)

def stop_jobs():
    try:queue_manager.shutdown()
    except OSError:logging.exception('Could not persist queue shutdown state')
    with lock: running_jobs=[job for ident,job in jobs.items() if not ident.startswith('direct-burn-')]
    for job in running_jobs:
        job.cancel.set()
        job.resume()
        process=job.process
        if process is not None:
            # The parent watcher exits immediately; a stopped child must receive
            # CONT before TERM rather than waiting for the Job polling thread.
            try:os.kill(process.pid,signal.SIGCONT)
            except OSError:pass
            try:process.terminate()
            except OSError:pass
    # Let author workers remove their own temporary filter caches after TERM.
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if not any(getattr(job,'_service_active',False) or job.process is not None or job.status in ('queued','running','paused') for job in running_jobs) and not queue_manager.thread.is_alive():break
        time.sleep(.05)

def prepare_shutdown():
    # Never terminate a drive while writing or closing a session. The file lock
    # remains held, so a newly opened app cannot start a competing burn.
    direct_burn_manager.shutdown(wait=False)
    burn_manager.shutdown(wait=False)
    stop_jobs()
    burn_manager.shutdown(wait=True)
    direct_burn_manager.shutdown(wait=True)

def main():
    first=int(os.environ.get('DISCAUTHOR_PORT','8779'))
    native=bool(os.environ.get('DISCAUTHOR_NATIVE_TOKEN'))
    if native:
        parent=int(os.environ.get('DISCAUTHOR_PARENT_PID','0'))
        if parent:
            def watch_parent():
                while True:
                    time.sleep(1)
                    try:os.kill(parent,0)
                    except ProcessLookupError:
                        prepare_shutdown();os._exit(0)
            threading.Thread(target=watch_parent,daemon=True).start()
    for port in range(first,first+(1 if native else 20)):
        url=f'http://127.0.0.1:{port}/'
        if not native:
            try:
                with urllib.request.urlopen(url+'health',timeout=.3) as r: existing=json.load(r)
                if existing.get('application')=='DiscAuthor Community':
                    if not os.environ.get('DISCAUTHOR_NO_BROWSER'):webbrowser.open(url)
                    return
            except Exception:pass
        try:server=create_server(app,host='127.0.0.1',port=port,threads=6)
        except OSError:
            if native:raise
            continue
        def terminate(signum,frame):
            signal.signal(signal.SIGTERM,signal.SIG_IGN)
            prepare_shutdown();server.close();raise SystemExit(0)
        signal.signal(signal.SIGTERM,terminate)
        if not native and not os.environ.get('DISCAUTHOR_NO_BROWSER'):threading.Timer(.5,lambda:webbrowser.open(url)).start()
        server.run();return
    raise RuntimeError('No available loopback port')
if __name__=='__main__':main()
