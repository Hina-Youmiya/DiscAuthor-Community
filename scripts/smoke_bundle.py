#!/usr/bin/env python3
"""Exercise a packaged backend with synthetic videos; never burns or moves a tray.

Produces four small ISOs (DVD/BD, menu/single title) through the application's
queue API. This is software integration evidence, not physical-player approval.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True,
                        help='New directory for synthetic media, temporary app data, ISOs and report')
    args = parser.parse_args()
    app = args.app.resolve()
    backend = app/'Contents/MacOS/DiscAuthorBackend'
    resources = app/'Contents/Resources'
    ffmpeg = resources/'bin/ffmpeg'
    if not ffmpeg.is_file():
        ffmpeg = app/'Contents/Frameworks/bin/ffmpeg'
    if not backend.is_file() or not ffmpeg.is_file():
        raise SystemExit('Packaged backend/FFmpeg is missing')
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'DISCAUTHOR_NATIVE_TOKEN': token,
           'DISCAUTHOR_PORT': str(port), 'DISCAUTHOR_NO_BROWSER': '1',
           'DISCAUTHOR_PARENT_PID': str(os.getpid()),
           'DISCAUTHOR_DATA_DIR': str(work/'app-data'), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}
    # No developer runtime or application data is needed by the packaged helper.
    for key in ('PYTHONPATH', 'PYTHONHOME', 'DISCAUTHOR_FFMPEG', 'DISCAUTHOR_FFPROBE'):
        env.pop(key, None)

    def request(path, data=None, auth=True, language='en', raw=False):
        headers = {'X-DiscAuthor-Language': language}
        if auth:
            headers['X-DiscAuthor-Token'] = token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(base+path, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read()
            return body if raw else json.loads(body)

    report = {'physical_player_verified': False, 'disc_burned': False, 'passed': False}
    with (work/'backend.log').open('w') as log:
        process = subprocess.Popen([str(backend)], env=env, stdout=log, stderr=log)
        try:
            for _ in range(200):
                if process.poll() is not None:
                    raise RuntimeError('Packaged backend stopped: see backend.log')
                try:
                    report['health'] = request('/health', auth=False)
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(.2)
            else:
                raise TimeoutError('Packaged backend did not start')
            assert report['health']['application'] == 'DiscAuthor Community'
            try:
                request('/api/preset', auth=False)
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError('Unauthenticated API accepted')

            preset = request('/api/preset')
            assert not preset['background'] and preset.get('menu_template') is None
            preset['font'] = {'path': 'builtin:mochiypop-p', 'index': 0}
            preset['button_feedback'].update(text_enabled=True, region_enabled=False,
                                              selected_text_color='#ffff00', activated_text_color='#ff0000')
            preset['videos'] = [{'title': '春の記録'}, {'title': '夏の記録'}]
            preset['per_page'] = 2
            for language in ('ja', 'zh', 'en'):
                preview = request('/api/preview', copy.deepcopy(preset), language=language)
                assert [b['label'] for b in preview['buttons']] == ['春の記録', '夏の記録']
                selected = request(preview['buttons'][0]['feedback']['selected'], raw=True)
                activated = request(preview['buttons'][0]['feedback']['activated'], raw=True)
                assert selected != activated
                (work/f'preview-{language}.png').write_bytes(request(preview['image'], raw=True))
            report['ui_languages_preserve_titles'] = ['ja', 'zh', 'en']
            report['selected_and_activated_feedback_differ'] = True

            media = work/'synthetic.mp4'
            subprocess.run([str(ffmpeg), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'testsrc2=size=1280x720:rate=30000/1001', '-f', 'lavfi', '-i',
                            'sine=frequency=440:sample_rate=48000', '-t', '3', '-c:v', 'libx264',
                            '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709',
                            '-color_trc', 'bt709', '-colorspace', 'bt709', '-c:a', 'aac', str(media)],
                           check=True, env=env, stdout=log, stderr=log, timeout=120)
            info = request('/api/probe', {'paths': [str(media)]})
            assert len(info['videos']) == 1 and info['videos'][0]['info']['audio']
            projects = []
            for format_ in ('dvd', 'bluray'):
                for menu in (False, True):
                    config = copy.deepcopy(preset)
                    name = f'{format_}-'+('menu' if menu else 'single')
                    config.update(title=name, format=format_, menu_enabled=menu,
                                  capacity='dvd5' if format_ == 'dvd' else 'bd25',
                                  export_mode='project_name', output_dir=str(work/'output'),
                                  no_menu_end_action='stop', bd_mode='1080i60',
                                  videos=[{'path': str(media), 'title': '春の記録'}])
                    if menu:
                        config['videos'].append({'path': str(media), 'title': '夏の記録'})
                    config['encoding']['quality'] = 'balanced'
                    projects.append(config)
            queued = request('/api/queue', {'projects': projects})
            assert len(queued['items']) == 4
            request('/api/queue/start', {})
            deadline = time.monotonic()+900
            last = None
            while time.monotonic() < deadline:
                queue = request('/api/queue')
                state = [(i['status'], round(i.get('progress') or 0)) for i in queue['items']]
                if state != last:
                    print(json.dumps(state), flush=True)
                    last = state
                if any(i['status'] in ('failed', 'cancelled', 'interrupted') for i in queue['items']):
                    report['queue'] = queue
                    raise RuntimeError('Synthetic authoring task failed; see report and task build.log')
                if all(i['status'] == 'complete' for i in queue['items']):
                    break
                time.sleep(1)
            else:
                raise TimeoutError('Synthetic authoring did not complete in 15 minutes')
            report['outputs'] = []
            for item in queue['items']:
                iso = Path(item['iso'])
                inspected = request('/api/burn/inspect', {'iso': str(iso)})
                with iso.open('rb') as stream:
                    sha = hashlib.file_digest(stream, 'sha256').hexdigest()
                report['outputs'].append({'name': iso.name, 'bytes': iso.stat().st_size,
                                          'sha256': sha, 'inspection': inspected})
            report['passed'] = True
        except Exception as error:
            report['error'] = str(error)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            report['backend_exit_code'] = process.returncode
            (work/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
