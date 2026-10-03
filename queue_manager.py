"""Persistent, serial disc builds sharing the legacy service's job registry."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import os
import threading
import time
import traceback
import uuid
from iso_export import job_root


class QueueConflict(ValueError):
    code = 409


class QueueNotFound(ValueError):
    code = 404


class QueuePersistenceError(OSError):
    code = 503


ACTIVE = ('queued', 'running', 'paused')
TERMINAL = ('complete', 'failed', 'cancelled', 'interrupted')
PROGRESS_FIELDS = ('stage_progress', 'processed_seconds', 'total_seconds', 'speed')


def job_is_active(job):
    return job.status in ACTIVE or getattr(job, '_service_active', False)


class QueueManager:
    MAX_ITEMS = 1000

    def __init__(self, path, jobs, lock, output_root, *, job_factory=None,
                 worker=None, preflight=None):
        from author import Job, build
        from preflight import check_project
        self.path = Path(path)
        self.jobs, self.lock = jobs, lock
        self.output_root = Path(output_root)
        self.job_factory = job_factory or Job
        self.worker = worker or build
        self.preflight = preflight or check_project
        self.items = []
        self.enabled = False
        self.error = None
        self._dirty_fields = set()
        self._last_saved_at = time.monotonic()
        self.closed = False
        self.active_id = None
        self.wake = threading.Event()
        self._owner_fd = None
        self._owner_pid = os.getpid()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = self.path.with_name(self.path.name + '.lock').open('a+b')
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise QueueConflict('已有 DiscAuthor 实例正在使用此制作队列，请关闭另一实例后重试') from None
            self._owner_fd = descriptor
            # Ownership precedes recovery: a second service must not mark a
            # live owner's running task interrupted or rewrite its journal.
            self._load()
            self.thread = threading.Thread(target=self._run, name='DiscAuthorQueue', daemon=True)
            self.thread.start()
        except BaseException:
            self._owner_fd = None
            descriptor.close()
            raise

    def _release_owner(self):
        with self.lock:
            if self._owner_fd is not None:
                self._owner_fd.close()
                self._owner_fd = None

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            items = data['items']
            if not isinstance(items, list) or len(items) > self.MAX_ITEMS:
                raise ValueError('Invalid queue size')
            seen = set()
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('config'), dict):
                    raise ValueError('Invalid queue item')
                if not isinstance(item.get('id'), str) or item['id'] in seen:
                    raise ValueError('Invalid queue ID')
                seen.add(item['id'])
                if item.get('status') not in ('pending', 'running', 'paused', *TERMINAL):
                    raise ValueError('Invalid queue status')
                for field in PROGRESS_FIELDS:
                    item.setdefault(field, None)
                if item['status'] in ('running', 'paused'):
                    item.update(status='interrupted', message='上次制作已中断，请重试',
                                error='服务退出前未完成此任务')
            self.items = items
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('无法读取持久化制作队列：' + str(self.path)) from exc
        # Never silently restart encoding after a process or computer restart.
        # A readable but unwritable store still exposes its records and error.
        try:self._persist_locked()
        except QueuePersistenceError:pass

    def _save_locked(self):
        if self._owner_fd is None or self._owner_pid != os.getpid():
            raise QueuePersistenceError('制作队列所有权已释放，不能再保存此实例的状态')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w') as stream:
                json.dump({'version': 1, 'running': self.enabled, 'items': self.items}, stream,
                          ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _fault_locked(self, exc):
        self.enabled = False
        self.error = '队列保存或调度失败，已停止后续制作：' + str(exc)

    def _persist_locked(self):
        try:
            self._save_locked()
        except Exception as exc:
            self._fault_locked(exc)
            raise QueuePersistenceError(self.error) from exc
        self._dirty_fields.clear()
        self._last_saved_at = time.monotonic()

    def _find_locked(self, ident):
        for item in self.items:
            if item['id'] == ident:
                return item
        raise QueueNotFound('找不到队列任务')

    def _sync_locked(self):
        if self.closed or not self.active_id:
            return set()
        item = self._find_locked(self.active_id)
        job = self.jobs.get(self.active_id)
        if not job:
            return set()
        snapshot = job.snapshot()
        changed = set()
        active_status = 'paused' if snapshot.get('status') == 'paused' else 'running'
        if item['status'] != active_status:
            item['status'] = active_status
            changed.add('status')
        # Keep the item running until the worker has returned. This closes the
        # gap between author.build setting complete and releasing its resources.
        for key in ('progress', 'message', 'error', 'iso', 'folder', *PROGRESS_FIELDS):
            value = snapshot.get(key)
            if item.get(key) != value:
                item[key] = value
                changed.add(key)
        self._dirty_fields.update(changed)
        return changed

    def _snapshot_locked(self):
        changes = self._sync_locked()
        important = changes - {'progress', *PROGRESS_FIELDS}
        due = time.monotonic() - self._last_saved_at >= 5
        if not self.error and self._dirty_fields and (important or due):
            try:self._persist_locked()
            except QueuePersistenceError:pass
        return {'running': self.enabled, 'error': self.error, 'items': deepcopy(self.items)}

    def snapshot(self):
        with self.lock:
            return self._snapshot_locked()

    def busy_locked(self):
        """Caller holds the service's shared lock, including legacy /api/build."""
        return self.enabled or self.active_id is not None

    def add(self, projects):
        if not isinstance(projects, list) or not 1 <= len(projects) <= self.MAX_ITEMS:
            raise ValueError('请添加 1–1000 个项目到队列')
        configs = deepcopy(projects)
        # Slow probing stays outside the scheduler lock. No partial batch is
        # appended when any input is invalid, and capacity is rechecked inside.
        for config in configs:
            if not isinstance(config, dict):
                raise ValueError('项目配置必须是对象')
            self.preflight(deepcopy(config))
        with self.lock:
            if self.closed:
                raise QueueConflict('制作服务正在退出')
            if len(self.items) + len(configs) > self.MAX_ITEMS:
                raise ValueError('队列最多保留 1000 个任务，请移除已完成记录')
            additions = []
            for config in configs:
                additions.append({'id': uuid.uuid4().hex, 'title': config.get('title') or '我的影片',
                                  'format': config['format'], 'capacity': config.get('capacity') or ('dvd5' if config['format'] == 'dvd' else 'bd25'),
                                  'status': 'pending', 'progress': 0, 'message': '等待制作',
                                  'error': None, 'iso': None, 'folder': None,
                                  'created_at': datetime.now(timezone.utc).isoformat(),
                                  'config': config, **dict.fromkeys(PROGRESS_FIELDS)})
            previous = self.items
            self.items = self.items + additions
            try:
                self._persist_locked()
            except Exception:
                self.items = previous
                raise
            self.wake.set()
            return self._snapshot_locked()

    def start(self):
        with self.lock:
            if self.closed:
                raise QueueConflict('制作服务正在退出')
            if any(ident != self.active_id and job_is_active(job) for ident, job in self.jobs.items()):
                raise QueueConflict('已有制作任务运行中')
            item = self._find_locked(self.active_id) if self.active_id else None
            previous_status = item['status'] if item else None
            if item:item['status'] = 'running'
            self.enabled = self.active_id is not None or any(i['status'] == 'pending' for i in self.items)
            try:self._persist_locked()
            except QueuePersistenceError:
                if item:item['status'] = previous_status
                raise
            self.error = None
            if self.active_id is not None:
                self.jobs[self.active_id].resume()
            self.wake.set()
            return self._snapshot_locked()

    def pause(self):
        with self.lock:
            self.enabled = False
            self._persist_locked()
            return self._snapshot_locked()

    def pause_item(self, ident):
        with self.lock:
            item = self._find_locked(ident)
            if ident != self.active_id or item['status'] not in ('running', 'paused'):
                raise QueueConflict('只能暂停正在制作的任务')
            self.jobs[ident].pause()
            self._sync_locked()
            self._persist_locked()
            return self._snapshot_locked()

    def resume_item(self, ident):
        with self.lock:
            item = self._find_locked(ident)
            if ident != self.active_id or item['status'] not in ('running', 'paused'):
                raise QueueConflict('只能继续当前暂停的任务')
            # Do not change enabled: resuming a task and enabling subsequent
            # queue dispatch are distinct user choices.
            previous_status = item['status']
            item['status'] = 'running'
            try:self._persist_locked()
            except QueuePersistenceError:
                item['status'] = previous_status
                raise
            self.jobs[ident].resume()
            return self._snapshot_locked()

    def move(self, ident, direction):
        if direction not in ('up', 'down'):
            raise ValueError('队列移动方向必须为 up 或 down')
        with self.lock:
            item = self._find_locked(ident)
            if item['status'] != 'pending':
                raise QueueConflict('只能调整等待中的任务顺序')
            pending = [index for index, row in enumerate(self.items) if row['status'] == 'pending']
            position = pending.index(self.items.index(item))
            other = position + (-1 if direction == 'up' else 1)
            if 0 <= other < len(pending):
                a, b = pending[position], pending[other]
                self.items[a], self.items[b] = self.items[b], self.items[a]
                self._persist_locked()
            return self._snapshot_locked()

    def retry(self, ident):
        with self.lock:
            item = self._find_locked(ident)
            if item['status'] not in ('failed', 'cancelled', 'interrupted'):
                raise QueueConflict('只能重试失败、取消或中断的任务')
            item.update(status='pending', progress=0, message='等待制作', error=None,
                        iso=None, folder=None, **dict.fromkeys(PROGRESS_FIELDS))
            self._persist_locked()
            self.wake.set()
            return self._snapshot_locked()

    def cancel(self, ident):
        with self.lock:
            item = self._find_locked(ident)
            if item['status'] in ('running', 'paused'):
                job = self.jobs.get(ident)
                if job:
                    job.cancel.set()
                    self._sync_locked()
                    self._persist_locked()
            elif item['status'] == 'pending':
                item.update(status='cancelled', message='已取消')
                self._persist_locked()
            return self._snapshot_locked()

    def remove(self, ident):
        with self.lock:
            item = self._find_locked(ident)
            if item['status'] in ('running', 'paused'):
                raise QueueConflict('请先取消正在制作的任务')
            self.items.remove(item)
            self._persist_locked()
            return self._snapshot_locked()

    def log(self, ident):
        with self.lock:
            folder = self._find_locked(ident).get('folder')
        path = Path(folder) / 'build.log' if folder else None
        if not path or not path.is_file():
            return ''
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 16000))
            return stream.read().decode('utf-8', errors='replace')

    def reveal_target(self, ident):
        with self.lock:
            item = self._find_locked(ident)
            target = item.get('iso') or item.get('folder')
        if not target or not Path(target).exists():
            raise ValueError('输出尚未创建')
        return target

    def _reserve_locked(self):
        if self.closed or not self.enabled or self.active_id is not None:
            return None
        if any(job_is_active(job) for job in self.jobs.values()):
            return None
        item = next((row for row in self.items if row['status'] == 'pending'), None)
        if not item:
            self.enabled = False
            self._persist_locked()
            return None
        base = job_root(item['config'], self.output_root)
        name = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
        out = base / name
        job = self.job_factory(deepcopy(item['config']), out)
        job.status = 'running'
        job.message = '检查视频与容量'
        previous = dict(item)
        previous_job = self.jobs.get(item['id'])
        item.update(status='running', message='检查视频与容量', folder=str(out))
        self.active_id = item['id']
        self.jobs[item['id']] = job
        try:self._persist_locked()
        except QueuePersistenceError:
            item.clear();item.update(previous)
            self.active_id = None
            if previous_job is None:self.jobs.pop(item['id'], None)
            else:self.jobs[item['id']] = previous_job
            raise
        return item['id'], job

    def _execute(self, ident, job):
        try:
            # Re-probe assets and current free space immediately before every
            # build. Earlier queued items may have consumed that space.
            self.preflight(deepcopy(job.config))
            if job.cancel.is_set():
                job.status, job.message = 'cancelled', '已取消'
            else:
                job.out.parent.mkdir(parents=True, exist_ok=True)
                self.worker(job)
        except Exception as exc:
            job.status, job.error, job.message = 'failed', str(exc), '制作失败'
            try:
                job.out.mkdir(parents=True, exist_ok=True)
                with (job.out / 'build.log').open('a') as stream:
                    stream.write(traceback.format_exc())
            except OSError:
                pass
        with self.lock:
            if self.closed:
                return
            self._sync_locked()
            item = self._find_locked(ident)
            item['status'] = job.status if job.status in TERMINAL else 'failed'
            if item['status'] == 'failed' and not item.get('error'):
                item.update(error='制作进程未报告完成', message='制作失败')
            self.active_id = None
            if not any(row['status'] == 'pending' for row in self.items):
                self.enabled = False
            # Completion is true in memory even when the disk cannot record it.
            # Keep the ISO path and stop dispatch, rather than replaying the job.
            try:self._persist_locked()
            except QueuePersistenceError:pass

    def _run(self):
        try:
            self._dispatch()
        finally:
            # shutdown() requests cancellation but may return before an encoder
            # has stopped. Keep ownership until that worker really returns.
            self._release_owner()

    def _dispatch(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            with self.lock:
                if self.closed:
                    return
                try:reserved = self._reserve_locked()
                except Exception as exc:
                    if not isinstance(exc, QueuePersistenceError):self._fault_locked(exc)
                    continue
            if reserved:
                try:self._execute(*reserved)
                except Exception as exc:
                    with self.lock:
                        self._fault_locked(exc)
                        ident, job = reserved
                        item = self._find_locked(ident)
                        item.update(status=job.status if job.status in TERMINAL else 'failed',
                                    iso=job.iso, folder=str(job.out), error=job.error or self.error)
                        self.active_id = None
                self.wake.set()

    def shutdown(self):
        with self.lock:
            if self.closed:
                return
            self._sync_locked()
            self.enabled = False
            self.closed = True
            if self.active_id:
                item = self._find_locked(self.active_id)
                item.update(status='interrupted', message='上次制作已中断，请重试', error='服务退出前未完成此任务')
                self.jobs[self.active_id].cancel.set()
            try:self._persist_locked()
            finally:
                self.wake.set()
                # No worker is outside this lock when active_id is empty, and
                # closed prevents further reservations. Release synchronously
                # without joining under a caller's possibly held shared RLock.
                if self.active_id is None:
                    self._release_owner()
