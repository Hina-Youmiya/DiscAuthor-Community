#!/usr/bin/env python3
"""Launch the real Community .app twice and check native startup/clean shutdown.

This catches crashes before the backend exists, including UserDefaults domain
initialization failures. No authoring, burn, erase, eject or tray API is called.
Run in a logged-in macOS desktop session; each test briefly opens an app window.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import time
import urllib.request
import uuid

BUNDLE_ID = 'jp.discauthor.community'
LIBPROC = None
LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def process_path(pid):
    global LIBPROC
    if LIBPROC is None:
        LIBPROC = ctypes.CDLL('/usr/lib/libproc.dylib')
        LIBPROC.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        LIBPROC.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    return os.fsdecode(buffer.value) if LIBPROC.proc_pidpath(pid, buffer, len(buffer)) > 0 else None


def child_processes(parent_pid):
    """Read PID relationships only; never collect commands or environment tokens."""
    rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid='], text=True)
    records = [tuple(map(int, row.split())) for row in rows.splitlines() if row.strip()]
    owned = {parent_pid}
    while True:
        added = {pid for pid, parent in records if parent in owned}
        if added <= owned:
            return owned - {parent_pid}
        owned |= added


def listening_port(pid):
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid),
                             '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True, timeout=5)
    matches = re.findall(r'^n127\.0\.0\.1:(\d+)$', result.stdout, flags=re.MULTILINE)
    return int(matches[0]) if len(matches) == 1 else None


def backend_identity(pid, executable):
    try:
        if process_path(pid) != str(executable):
            return None
        started = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()
        return (os.getpgid(pid), started) if started else None
    except (ProcessLookupError, subprocess.CalledProcessError):
        return None


def is_owned_backend(pid, executable, identity):
    return identity is not None and backend_identity(pid, executable) == identity


def preview_evidence(data):
    font_file = data/'fonts.json'
    if not font_file.is_file():
        return None
    try:
        fonts = json.loads(font_file.read_text())
    except (OSError, ValueError):
        return None
    previews = []
    for path in (data/'output/preview').glob('*.png'):
        with path.open('rb') as stream:
            if stream.read(8) == b'\x89PNG\r\n\x1a\n':
                previews.append(path.name)
    if not isinstance(fonts, list) or len(previews) < 3:
        return None
    return {'font_catalog_created': True, 'preview_png_count': len(previews)}


def run_case(app, executable, work, mode, timeout, settle):
    case = work/mode
    case.mkdir()
    data = case/'app-data'
    data.mkdir()
    # Clear development overrides so this checks the actual packaged runtime.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('DISCAUTHOR_', 'PYTHON', 'DYLD_'))}
    env.update(DISCAUTHOR_DATA_DIR=str(data), DISCAUTHOR_NO_BROWSER='1',
               PATH='/usr/bin:/bin:/usr/sbin:/sbin')
    if mode == 'override':
        env['DISCAUTHOR_PREFERENCES_SUITE'] = BUNDLE_ID+'.smoke.'+uuid.uuid4().hex
    report = {'mode': mode, 'passed': False, 'data_directory': str(data.relative_to(work)),
              'preferences_override': env.get('DISCAUTHOR_PREFERENCES_SUITE'),
              'optical_action_requested': False, 'export_requested': False}
    backend_executable = app/'Contents/MacOS/DiscAuthorBackend'
    tracked = {}
    with (case/'native.log').open('w') as log:
        process = subprocess.Popen([str(executable)], cwd=case, env=env,
                                   stdout=log, stderr=log, start_new_session=True)
        report['native_pid'] = process.pid
        started = time.monotonic()
        try:
            deadline = started+timeout
            ready_since = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'Native app exited during startup ({process.returncode}); see native.log')
                candidates = child_processes(process.pid)
                for pid in candidates:
                    identity = backend_identity(pid, backend_executable)
                    if identity is not None:
                        tracked[pid] = identity
                health = None
                for pid in sorted(tracked):
                    if not is_owned_backend(pid, backend_executable, tracked[pid]):
                        continue
                    port = listening_port(pid)
                    if port is None:
                        continue
                    try:
                        # Health is intentionally public; never read the native auth token.
                        with LOOPBACK.open(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                            candidate_health = json.load(response)
                        if candidate_health.get('application') == 'DiscAuthor Community':
                            health = candidate_health
                            report['backend_pid'] = pid
                            break
                    except (OSError, ValueError):
                        continue
                evidence = preview_evidence(data)
                if health and evidence and (data/'native-backend.log').is_file():
                    if ready_since is None:
                        ready_since = time.monotonic()
                    if time.monotonic()-ready_since >= settle:
                        report.update(health=health, initialization=evidence,
                                      survived_after_ready_seconds=round(time.monotonic()-ready_since, 2),
                                      startup_seconds=round(ready_since-started, 2))
                        break
                else:
                    ready_since = None
                time.sleep(.25)
            else:
                raise TimeoutError('Native startup/preview did not become ready; see native and backend logs')
        except Exception as error:
            report['error'] = str(error)
        finally:
            report['backend_pids_seen'] = sorted(tracked)
            if process.poll() is None:
                # AppKit targets this PID, never the bundle ID or app name, so an
                # already-running company/community instance is not terminated.
                script = ('ObjC.import("AppKit"); '
                          f'var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier({process.pid}); '
                          'app.terminate;')
                try:
                    quit_request = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script],
                                                  capture_output=True, text=True, timeout=10)
                    report['appkit_quit_requested'] = quit_request.returncode == 0
                    process.wait(timeout=15)
                except (subprocess.TimeoutExpired, OSError):
                    report['forced_native_shutdown'] = True
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            report['native_exit_code'] = process.returncode
            deadline = time.monotonic()+15
            while time.monotonic() < deadline and any(is_owned_backend(pid, backend_executable, tracked[pid]) for pid in tracked):
                time.sleep(.2)
            leftovers = [pid for pid in tracked if is_owned_backend(pid, backend_executable, tracked[pid])]
            report['backend_clean_exit'] = not leftovers
            # Failure cleanup remains restricted to an observed descendant's
            # exact executable, recorded start time and recorded process group.
            for pid in leftovers:
                if is_owned_backend(pid, backend_executable, tracked[pid]):
                    os.kill(pid, signal.SIGTERM)
            report['passed'] = (not report.get('error') and process.returncode == 0
                                and report['backend_clean_exit']
                                and not report.get('forced_native_shutdown', False))
            if not report['passed'] and 'error' not in report:
                report['error'] = 'Native or backend did not shut down cleanly'
            (case/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--work-dir', required=True, type=Path, help='New, nonexisting directory for isolated test data/logs')
    parser.add_argument('--preferences-mode', choices=('both', 'default', 'override'), default='both')
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--settle-seconds', type=float, default=5)
    args = parser.parse_args()
    if os.uname().sysname != 'Darwin':
        parser.error('This test requires a logged-in macOS desktop session')
    if args.timeout < 10 or args.settle_seconds < 3 or args.settle_seconds >= args.timeout:
        parser.error('Require timeout >= 10 and 3 <= settle-seconds < timeout')
    app = args.app.expanduser().resolve()
    with (app/'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleIdentifier') != BUNDLE_ID:
        parser.error('Refusing to launch a bundle other than jp.discauthor.community')
    executable_name = info.get('CFBundleExecutable', '')
    if not executable_name or Path(executable_name).name != executable_name:
        parser.error('Invalid CFBundleExecutable')
    executable = app/'Contents/MacOS'/executable_name
    if not executable.is_file() or not (app/'Contents/MacOS/DiscAuthorBackend').is_file():
        parser.error('Native executable or bundled backend is missing')
    work = args.work_dir.expanduser().resolve()
    if work == app or app in work.parents:
        parser.error('The test work directory must be outside the application bundle')
    work.mkdir(parents=True, exist_ok=False)
    modes = ('default', 'override') if args.preferences_mode == 'both' else (args.preferences_mode,)
    report = {'bundle_id': BUNDLE_ID, 'version': info.get('CFBundleShortVersionString'),
              'scope': 'Real native startup; not visual or physical-disc certification', 'cases': []}
    for mode in modes:
        case = run_case(app, executable, work, mode, args.timeout, args.settle_seconds)
        report['cases'].append(case)
        print(f'{mode}: '+('PASS' if case['passed'] else 'FAIL'), flush=True)
    report['passed'] = all(case['passed'] for case in report['cases'])
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
