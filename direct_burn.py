"""Single-video author → optical burn with optional readback verification.

Only start/retry authorize a hardware write. Read-only preflight never reserves
or erases media. Failed images and logs remain available for an explicit retry.
"""
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import threading
import time
import uuid

import author
from burner import BurnConflict, fingerprint, verification_option, media_readiness_error
from preflight import CAPACITY_BYTES, check_project
from queue_manager import job_is_active

ACTIVE = {'preparing', 'authoring', 'checking_media', 'preparing_burn',
          'burning', 'verifying', 'finishing', 'cleaning', 'cancelling'}


def _stamp(path):
    s = Path(path).stat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('请选择单个本机视频文件')
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]


def _media_stamp(device):
    # DR does not expose a unique serial number for all blank media. Bind the
    # drive plus every available medium identifier; never switch drives silently.
    media = device['media']
    return [device['id'], device.get('vendor'), device.get('product'),
            *[media.get(k) for k in ('format', 'type', 'capacity_bytes',
                                    'free_bytes', 'id', 'serial', 'media_id')]]


def _capacity(device):
    media = device['media']
    readiness_error = media_readiness_error(media)
    if readiness_error:
        raise ValueError(readiness_error)
    if media.get('blank') is not True:
        raise ValueError('盘片不是空白盘，不会自动擦除')
    if media.get('writable') is not True:
        raise ValueError('光驱不能写入此盘片')
    fmt = media.get('format')
    if fmt not in ('dvd', 'bluray'):
        raise ValueError('请选择 DVD 或 Blu-ray 空白光盘')
    if not device.get('can_write_dvd' if fmt == 'dvd' else 'can_write_bd'):
        raise ValueError('光驱不支持此类型烧录')
    free = media.get('free_bytes')
    if type(free) not in (int, float) or not math.isfinite(free) or free <= 0:
        raise ValueError('无法确认盘片可用容量')
    options = ('dvd9', 'dvd5') if fmt == 'dvd' else ('bd50', 'bd25')
    for size in options:
        if free >= CAPACITY_BYTES[size]:
            return fmt, size
    raise ValueError('盘片容量不足')


class DirectBurnManager:
    def __init__(self, root, burn_manager, jobs, lock, queue_manager=None, *,
                 worker=None, project_check=None, job_factory=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'direct-burn.json'
        self.lockpath = self.root / 'direct-burn.lock'
        self.burn = burn_manager
        self.jobs, self.lock, self.queue = jobs, lock, queue_manager
        self.worker = worker or author.build
        self.project_check = project_check or check_project
        self.job_factory = job_factory or author.Job
        self.job = None
        self.author_job = None
        self.fd = None
        self.thread = None
        self.closing = False
        self.cancel_requested = threading.Event()

    def _read(self):
        try:
            record = json.loads(self.path.read_text())
            return record if isinstance(record, dict) else None
        except (OSError, ValueError):
            return None

    def _persist(self):
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w') as stream:
                json.dump(self.job, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _try_lock(self):
        fd = self.lockpath.open('a+')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            fd.close()
            return None

    def _device(self, ident):
        result = self.burn.devices()
        if result.get('error'):
            raise ValueError(result['error'])
        device = next((d for d in result.get('devices', []) if d['id'] == ident), None)
        if not device:
            raise ValueError('未找到所选光驱')
        _capacity(device)
        return device

    @staticmethod
    def _speed(speed):
        if speed is not None and (type(speed) not in (int, float) or
                                 not math.isfinite(speed) or not 0 < speed <= 128):
            raise ValueError('烧录速度无效')
        return speed

    @staticmethod
    def _work_root(working_dir):
        if not isinstance(working_dir, str) or not working_dir.strip():
            raise ValueError('请选择临时制作文件夹')
        base = Path(working_dir).expanduser().resolve()
        for component in (base / '.DiscAuthor', base / '.DiscAuthor' / 'direct-burn'):
            if component.is_symlink() or component.exists() and not component.is_dir():
                raise ValueError('临时制作路径无效')
        return base / '.DiscAuthor' / 'direct-burn'

    def _prepare(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('项目配置必须是对象')
        verification_option(payload.get('verify_after_burn', True))
        raw = payload.get('video')
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('请选择单个本机视频文件')
        source = Path(raw).expanduser().resolve()
        source_stamp = _stamp(source)
        device = self._device(payload.get('device_id'))
        fmt, capacity = _capacity(device)
        work_root = self._work_root(payload.get('working_dir'))
        self._speed(payload.get('speed'))
        config = {'title': payload.get('title') or source.stem,
                  'format': fmt, 'capacity': capacity,
                  'videos': [{'path': str(source), 'title': source.stem}],
                  'menu_enabled': False,
                  'no_menu_end_action': payload.get('no_menu_end_action', 'stop'),
                  'standard': payload.get('standard', 'ntsc'),
                  'bd_mode': payload.get('bd_mode', 'auto'),
                  'output_dir': str(work_root), 'export_mode': 'task_folder',
                  'cleanup_work_on_success': True, 'prevent_sleep': True,
                  'verify_after_burn': payload.get('verify_after_burn', True)}
        if 'encoding' in payload:
            config['encoding'] = deepcopy(payload['encoding'])
        report = self.project_check(deepcopy(config))
        if _stamp(source) != source_stamp:
            raise ValueError('视频文件在检查期间发生变化')
        return config, device, source_stamp, report

    def preflight(self, payload):
        try:
            config, device, _, report = self._prepare(payload)
            return {'valid': True, 'errors': [], 'device': device,
                    'format': config['format'], 'capacity': config['capacity'],
                    'project': report}
        except Exception as exc:
            return {'valid': False, 'errors': [str(exc)], 'device': None,
                    'format': None, 'capacity': None, 'project': None}

    def _reserve(self, *, authoring_required=True):
        # All callers hold the same service lock as QueueManager and /api/build.
        if self.closing:
            raise BurnConflict('程序正在退出')
        if self.fd is not None or self.snapshot()['active']:
            raise BurnConflict('已有视频直刻任务运行中')
        if authoring_required and ((self.queue and self.queue.busy_locked()) or any(job_is_active(j) for j in self.jobs.values())):
            raise BurnConflict('已有制作任务运行中')
        if self.burn.snapshot()['active']:
            raise BurnConflict('已有烧录任务运行中')
        fd = self._try_lock()
        if fd is None:
            raise BurnConflict('已有视频直刻任务运行中')
        self.fd = fd

    def _begin(self, record, config, prepared_iso=None):
        self._reserve(authoring_required=prepared_iso is None)
        ident = record['id']
        try:
            self.job = record
            self.cancel_requested.clear()
            job = self.job_factory(config, Path(record['work_dir']))
            job._service_active = prepared_iso is None
            job.status = 'running'
            job.message = '准备视频直刻'
            self.author_job = job
            if prepared_iso is None:
                self.jobs['direct-burn-' + ident] = job
            self._persist()  # No author or hardware work until durable reservation.
            self.thread = threading.Thread(target=self._run, args=(prepared_iso,),
                                           name='DiscAuthorDirectBurn', daemon=False)
            self.thread.start()
        except BaseException:
            if self.author_job:
                self.author_job._service_active = False
            self.jobs.pop('direct-burn-' + ident, None)
            self.fd.close()
            self.fd = None
            raise
        return self.snapshot()

    def start(self, payload):
        config, device, source_stamp, _ = self._prepare(payload)
        ident = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
        out = Path(config['output_dir']) / ident
        record = {'id': ident, 'status': 'preparing', 'stage': 'preparing',
                  'progress': 0.0, 'stage_progress': None, 'message': '准备视频直刻',
                  'error': None, 'video': config['videos'][0]['path'],
                  'device_id': device['id'], 'device_name': device['name'],
                  'format': config['format'], 'capacity': config['capacity'],
                  'verified': False, 'burn_completed': False,
                  'verify_after_burn': config['verify_after_burn'],
                  'verification_required': config['verify_after_burn'],
                  'verification_skipped': not config['verify_after_burn'], 'folder': str(out), 'work_dir': str(out),
                  'iso': None, 'can_retry': False, 'cleanup_error': None,
                  'log_path': str(out / 'build.log'), 'burn_id': None,
                  'config': config, 'source_stamp': source_stamp,
                  'media_stamp': _media_stamp(device), 'speed': payload.get('speed'),
                  'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        with self.lock:
            return self._begin(record, config)

    def _owned_iso(self, record):
        if not record.get('iso'):
            raise ValueError('没有可重试的临时镜像，请重新制作')
        path = Path(record['iso'])
        root = Path(record['work_dir'])
        if path.is_symlink() or root.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError('临时镜像路径无效')
        if list(fingerprint(path)) != record.get('iso_stamp'):
            raise ValueError('临时镜像已发生变化，请重新制作')
        return path

    def retry(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('项目配置必须是对象')
        with self.lock:
            snapshot = self.snapshot()
            record = snapshot.get('job')
            if not record or payload.get('job_id') != record.get('id'):
                raise ValueError('找不到视频直刻任务')
            if snapshot['active']:
                raise BurnConflict('已有视频直刻任务运行中')
            if record.get('verified') or record.get('burn_completed') or record.get('status') == 'complete':
                raise ValueError('该光盘已烧录完成')
            image = self._owned_iso(record)
            if _stamp(record['video']) != record['source_stamp']:
                raise ValueError('视频素材已更新，请重新制作')
            device = self._device(payload.get('device_id'))
            if device['media']['format'] != record['format']:
                raise ValueError('ISO 类型与盘片类型不匹配')
            speed = self._speed(payload.get('speed'))
            verify = verification_option(payload.get('verify_after_burn', record.get('verify_after_burn', True)))
            check = self.burn.check(str(image), device['id'], speed, verify_after_burn=verify)
            if not check['valid']:
                raise ValueError('\n'.join(check['errors']))
            record.update(status='preparing', stage='preparing', progress=80.0,
                          stage_progress=None, message='准备重试烧录', error=None,
                          device_id=device['id'], device_name=device['name'],
                          media_stamp=_media_stamp(device), speed=speed,
                          can_retry=False, burn_id=None, cleanup_error=None, burn_completed=False,
                          verify_after_burn=verify, verification_required=verify, verification_skipped=not verify)
            record['config']['verify_after_burn'] = verify
            return self._begin(record, deepcopy(record['config']), image)

    def _publish(self, **changes):
        with self.lock:
            self.job.update(changes)
            self._persist()

    def _checkpoint(self):
        if self.cancel_requested.is_set():
            raise author.Cancelled()

    def _run(self, prepared_iso):
        try:
            self._checkpoint()
            if prepared_iso is None:
                self._publish(status='authoring', stage='authoring', message='正在制作临时光盘镜像')
                # Recheck metadata and volume immediately before expensive work.
                if _stamp(self.job['video']) != self.job['source_stamp']:
                    raise ValueError('视频素材已更新，请重新制作')
                self.project_check(deepcopy(self.author_job.config))
                self._work_root(str(Path(self.author_job.config['output_dir']).parent.parent))
                Path(self.author_job.config['output_dir']).mkdir(parents=True, exist_ok=True)
                self.worker(self.author_job)
                self._checkpoint()
                if self.author_job.status != 'complete' or not self.author_job.iso:
                    raise ValueError(self.author_job.error or self.author_job.message)
                image = Path(self.author_job.iso)
                self._publish(iso=str(image), iso_stamp=list(fingerprint(image)))
            else:
                image = prepared_iso
            self._checkpoint()
            if _stamp(self.job['video']) != self.job['source_stamp']:
                raise ValueError('视频素材在制作期间发生变化，请重新制作')
            image = self._owned_iso(self.job)
            self._publish(status='checking_media', stage='checking_media', progress=80.0,
                          stage_progress=None, message='重新检查空白光盘')
            device = self._device(self.job['device_id'])
            if _media_stamp(device) != self.job['media_stamp']:
                raise ValueError('制作期间光盘信息已变化，请重新检查后重试烧录')
            check = self.burn.check(str(image), self.job['device_id'], self.job['speed'],
                                    verify_after_burn=self.job.get('verify_after_burn', True))
            if not check['valid']:
                raise ValueError('\n'.join(check['errors']))
            with self.lock:
                self._checkpoint()
                result = self.burn.start(str(image), self.job['device_id'], self.job['speed'],
                                         verify_after_burn=self.job.get('verify_after_burn', True))
                self.job.update(burn_id=result['job']['id'], status='preparing_burn',
                                stage='preparing_burn', message='准备烧录')
                # Encoding is finished and the drive now owns the burn. Release
                # only the authoring slot so queued/legacy ISO exports can run
                # while our direct-burn/drive reservations remain protected.
                self.author_job._service_active = False
                self.jobs.pop('direct-burn-' + self.job['id'], None)
                if self.queue:
                    self.queue.wake.set()
                # Never let a journal failure abandon an active optical write.
                try:
                    self._persist()
                except OSError as exc:
                    self.job['error'] = '烧录状态保存失败：' + str(exc)
            while True:
                snapshot = self.burn.snapshot()
                burn_job = snapshot.get('job') or {}
                if burn_job.get('id') != self.job['burn_id']:
                    raise ValueError('烧录任务状态不匹配，请检查光盘')
                self._copy_burn_progress(burn_job)
                if not snapshot['active']:
                    break
                time.sleep(.2)
            required = self.job.get('verify_after_burn', True)
            accepted = burn_job.get('verified') is True if required else burn_job.get('verification_skipped') is True
            if burn_job.get('status') != 'complete' or not accepted:
                if burn_job.get('status') == 'cancelled':
                    raise author.Cancelled()
                raise ValueError(burn_job.get('error') or '烧录校验未完成，临时镜像已保留')
            self._complete_burn(verified=required, verification_skipped=not required)
        except author.Cancelled:
            self._finish_error('cancelled', '已取消，临时文件和日志已保留')
        except Exception as exc:
            self._finish_error('failed', str(exc))
        finally:
            # Keep the direct-burn/drive reservation until an already-started
            # drive has stopped, even if status retrieval unexpectedly fails.
            if self.job.get('burn_id'):
                while True:
                    try:
                        burn = self.burn.snapshot()
                        pending = burn['active'] and (burn.get('job') or {}).get('id') == self.job['burn_id']
                    except Exception:
                        thread = getattr(self.burn, 'thread', None)
                        pending = bool(thread and thread.is_alive())
                    if not pending:
                        break
                    time.sleep(.2)
            with self.lock:
                if self.author_job:
                    self.author_job._service_active = False
                self.jobs.pop('direct-burn-' + self.job['id'], None)
                if self.fd:
                    self.fd.close()
                    self.fd = None
                if self.queue:
                    self.queue.wake.set()

    def _complete_burn(self, *, verified, verification_skipped):
        # A completed physical burn is irreversible, including an explicit
        # decision to skip verification. Journal or cleanup
        # failures must never turn it into an eligible retry and duplicate burn.
        warnings = []
        with self.lock:
            self.job.update(status='cleaning', stage='cleaning', progress=99.0,
                            verified=verified, verification_skipped=verification_skipped,
                            burn_completed=True, can_retry=False, message='清理临时镜像')
            try:
                self._persist()
            except Exception as exc:
                warnings.append('烧录状态保存失败：' + str(exc))
        try:
            self._owned_iso(self.job).unlink()
            self.job['iso'] = None
        except (OSError, ValueError) as exc:
            warnings.append(str(exc))
        with self.lock:
            self.job.update(status='complete', stage='complete', progress=100.0,
                            stage_progress=100.0, message='烧录并校验完成' if verified else '烧录完成，已跳过校验', error=None,
                            verified=verified, verification_skipped=verification_skipped, burn_completed=True, can_retry=False,
                            cleanup_error='\n'.join(warnings) or None)
            try:
                self._persist()
            except Exception as exc:
                warnings.append('烧录状态保存失败：' + str(exc))
                self.job['cleanup_error'] = '\n'.join(warnings)
                try:
                    self._persist()
                except Exception:
                    pass

    def _copy_burn_progress(self, burn_job):
        status = burn_job.get('status', 'preparing')
        bounds = {'preparing': (80, 80), 'burning': (80, 94),
                  'verifying': (94, 99), 'finishing': (94, 94)}
        with self.lock:
            if status in ACTIVE:
                self.job.update(status=status, stage=status,
                                message=burn_job.get('message'),
                                stage_progress=burn_job.get('stage_progress'))
                pct = burn_job.get('stage_progress')
                if status in bounds:
                    lower, upper = bounds[status]
                    self.job['progress'] = max(self.job['progress'], lower +
                                               (upper - lower) * (pct or 0) / 100)

    def _finish_error(self, status, message):
        with self.lock:
            can_retry = False
            try:
                self._owned_iso(self.job)
                can_retry = not self.job.get('verified') and not self.job.get('burn_completed') and _stamp(self.job['video']) == self.job['source_stamp']
            except (OSError, ValueError):
                pass
            self.job.update(status=status, stage=status, error=message,
                            message=message, can_retry=can_retry, stage_progress=None)
            try:
                self._persist()
            except OSError:
                pass

    def snapshot(self):
        with self.lock:
            if self.fd is not None:
                record = deepcopy(self.job)
                if record['status'] == 'authoring' and self.author_job:
                    progress = self.author_job.snapshot()
                    record.update(progress=min(80.0, progress['progress'] * .8),
                                  stage_progress=progress.get('stage_progress'),
                                  message=progress.get('message', record['message']))
                return {'active': True, 'job': record}
            fd = self._try_lock()
            external = fd is None
            if fd:
                fd.close()
            record = deepcopy(self.job or self._read())
            if record and record['status'] in ACTIVE and not external:
                burn = self.burn.snapshot()
                if burn['active'] and (burn.get('job') or {}).get('iso') == record.get('iso'):
                    external = True
                    record.update(status='burning', stage='burning', message='烧录进程仍在运行')
                elif ((burn.get('job') or {}).get('iso') == record.get('iso')
                      and (burn.get('job') or {}).get('status') == 'complete'
                      and ((record.get('verify_after_burn', True) is True and (burn.get('job') or {}).get('verified') is True)
                           or (record.get('verify_after_burn') is False
                               and (burn.get('job') or {}).get('verification_required') is False
                               and (burn.get('job') or {}).get('verification_skipped') is True
                               and ((burn.get('job') or {}).get('receipt') or {}).get('complete') is True))
                      and (not record.get('burn_id') or (burn.get('job') or {}).get('id') == record['burn_id'])):
                    # Recover the independently journalled verification result if
                    # our helper exited between burning and the cleanup journal.
                    record.update(status='complete', stage='complete', progress=100.0,
                                  stage_progress=100.0, verified=record.get('verify_after_burn', True) is True,
                                  verification_skipped=record.get('verify_after_burn') is False,
                                  burn_completed=True, can_retry=False, error=None,
                                  message='烧录并校验完成' if record.get('verify_after_burn', True) is True else '烧录完成，已跳过校验',
                                  cleanup_error='烧录已完成，请检查临时文件清理状态')
                else:
                    record.update(status='interrupted', stage='interrupted',
                                  message='上次视频直刻已中断，请检查光盘后重试',
                                  error='上次视频直刻已中断，请检查光盘后重试',
                                  stage_progress=None)
                    try:
                        self._owned_iso(record)
                        record['can_retry'] = _stamp(record['video']) == record['source_stamp']
                    except (OSError, ValueError):
                        record['can_retry'] = False
            return {'active': external, 'job': record}

    def cancel(self):
        with self.lock:
            if self.fd is None:
                if self.snapshot()['active']:
                    raise BurnConflict('视频直刻由另一进程保护中，请等待完成')
                return self.snapshot()
            if self.job.get('burn_id'):
                raise BurnConflict('光盘正在写入或校验，请等待完成')
            if self.job.get('verified'):
                return self.snapshot()
            self.cancel_requested.set()
            if self.author_job:
                self.author_job.cancel.set()
                self.author_job.resume()
            self.job.update(status='cancelling', stage='cancelling',
                            message='正在取消，请等待当前操作结束')
            try:
                self._persist()
            except OSError:
                pass
            return self.snapshot()

    def log(self):
        record = self.snapshot().get('job')
        if not record:
            return ''
        path = Path(record['work_dir']) / 'build.log'
        text = ''
        try:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 48000))
                text = stream.read().decode('utf8', 'replace')
        except OSError:
            pass
        if record.get('burn_id'):
            text += '\n' + self.burn.log()
        return text

    def shutdown(self, wait=True):
        # App exit cancels preparation, but lets the drive safely finish writing.
        with self.lock:
            self.closing = True
            thread = self.thread
            if self.fd and not self.job.get('burn_id'):
                self.cancel_requested.set()
                self.author_job.cancel.set()
                self.author_job.resume()
        if wait and thread and thread is not threading.current_thread():
            thread.join()
