"""Losslessly repackage an authored Blu-ray with one narrowly scoped TS repair.

No decoding or remuxing. Originals are opened read-only. Progress is 0..100;
checkpoint() may raise to cancel, and run(args) may be supplied by author.Job.
The C helper validates all payload continuity before changing its extracted copy.
"""
from pathlib import Path, PurePosixPath
import ctypes as C
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid

from burner import UDFBluray, DirEntry, inspect_iso
from paths import ROOT

CHUNK = 4 * 1024 * 1024
SCHEMA = 1


def _fingerprint(path):
    s = Path(path).stat()
    return dict(device=s.st_dev, inode=s.st_ino, size=s.st_size, mtime_ns=s.st_mtime_ns)


def _atomic_json(path, data):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _hash_file(path, checkpoint, update=None):
    digest = hashlib.sha256(); done = 0
    with Path(path).open('rb') as f:
        while True:
            checkpoint(); data = f.read(CHUNK)
            if not data: break
            digest.update(data); done += len(data)
            if update: update(done)
    return digest.hexdigest()


def _default_run(args, checkpoint):
    with subprocess.Popen(list(map(str, args))) as process:
        try:
            while process.poll() is None:
                checkpoint()
                try: process.wait(timeout=.1)
                except subprocess.TimeoutExpired: pass
            if process.returncode:
                raise RuntimeError(f'{Path(args[0]).name} exited with {process.returncode}')
        except BaseException:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            raise


class _Tree:
    def __init__(self, iso):
        self.reader = UDFBluray(iso)

    def close(self):
        self.reader.close()

    def directory(self, path):
        handle = self.reader.lib.bd_open_dir(self.reader.handle, path.encode('utf8'))
        if not handle: return None
        result = []
        try:
            item = DirEntry()
            while True:
                if handle.contents.read(handle, C.byref(item)) != 0: break
                name = bytes(item.name).decode('utf8', 'strict')
                if name in ('.', '..'): continue
                if not name or '/' in name or '\\' in name or '\0' in name:
                    raise ValueError('Unsafe UDF entry name')
                if name in result: raise ValueError('Duplicate UDF entry')
                result.append(name)
                if len(result) > 10000: raise ValueError('Too many UDF entries')
        finally:
            handle.contents.close(handle)
        return sorted(result)

    def file(self, path):
        handle = self.reader.lib.bd_open_file_dec(self.reader.handle, path.encode('utf8'))
        if not handle: raise ValueError('Cannot open UDF file: ' + path)
        return handle

    def inventory(self, checkpoint):
        roots = self.directory('')
        if roots != ['BDMV', 'CERTIFICATE']:
            raise ValueError('Expected only the authored BDMV and CERTIFICATE trees')
        directories = []; files = {}
        def walk(path, depth=0):
            checkpoint()
            if depth > 32: raise ValueError('Excessive directory depth')
            names = self.directory(path)
            if names is not None:
                directories.append(path)
                for name in names: walk(path + '/' + name, depth + 1)
            else:
                handle = self.file(path)
                try: size = handle.contents.seek(handle, 0, 2)
                finally: handle.contents.close(handle)
                if size < 0: raise ValueError('Cannot size UDF file: ' + path)
                files[path] = size
            if len(files) + len(directories) > 10000: raise ValueError('Excessive UDF tree')
        for name in roots: walk(name)
        return sorted(directories), dict(sorted(files.items()))

    def chunks(self, path, size, checkpoint):
        handle = self.file(path)
        try:
            if handle.contents.seek(handle, 0, 2) != size:
                raise ValueError('UDF file size changed: ' + path)
            if handle.contents.seek(handle, 0, 0) != 0:
                raise ValueError('Cannot seek UDF file: ' + path)
            remaining = size
            buffer = C.create_string_buffer(min(CHUNK, max(1, size)))
            while remaining:
                checkpoint(); length = min(len(buffer), remaining)
                count = handle.contents.read(handle, buffer, length)
                if count <= 0 or count > length: raise ValueError('Truncated UDF file: ' + path)
                remaining -= count
                yield buffer.raw[:count]
        finally:
            handle.contents.close(handle)


def reexport_one(source_iso, output_dir, tsmuxer, progress=None, checkpoint=None, run=None):
    """Return {iso, report, reused, corrections, ...}; publish only verified data.

    output_dir must be empty/new, or contain a previously verified result from
    this function. An interrupted attempt retains its own diagnostics and must
    use a new output directory. A verified retry checks both complete ISO hashes.
    run(args) must raise on nonzero exit and wait until its process has ended.
    """
    progress = progress or (lambda message, percent: None)
    checkpoint = checkpoint or (lambda: None)
    run = run or (lambda args: _default_run(args, checkpoint))
    source = Path(source_iso).expanduser().resolve(strict=True)
    output = Path(output_dir).expanduser().resolve()
    tsmuxer = Path(tsmuxer).expanduser().resolve(strict=True)
    helper = ROOT / 'bin/reexport-m2ts'
    if not helper.is_file(): raise ValueError('Missing bin/reexport-m2ts')
    if not source.is_file() or source.suffix.lower() != '.iso': raise ValueError('Source must be a local ISO file')
    final = output / 'BLURAY_VIDEO.iso'
    report_path = output / 'reexport-report.json'
    if final.resolve() == source: raise ValueError('The original ISO must never be overwritten')
    fingerprint = _fingerprint(source)
    progress('检查原 Blu-ray ISO', 0); checkpoint()
    if inspect_iso(str(source))['format'] != 'bluray': raise ValueError('Source is not Blu-ray Video')
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if (report.get('schema_version') != SCHEMA or report.get('status') != 'verified'
                or report.get('source', {}).get('path') != str(source) or not final.is_file()):
            raise ValueError('Existing output is not a verified result; use a new output directory')
        progress('验证已有重导出结果', 5)
        if _hash_file(source, checkpoint) != report['source']['sha256']:
            raise ValueError('Source ISO differs from the verified checkpoint')
        if _hash_file(final, checkpoint) != report['output']['sha256']:
            raise ValueError('Output ISO differs from the verified checkpoint')
        if _fingerprint(source) != fingerprint: raise ValueError('Source ISO changed during verification')
        progress('已有重导出结果验证完成', 100)
        return dict(iso=str(final), report=str(report_path), reused=True,
                    corrections=report['corrected_counters'], size_bytes=final.stat().st_size)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory is not empty; refusing to replace its files')

    tree = _Tree(source)
    try: directories, inventory = tree.inventory(checkpoint)
    finally: tree.close()
    total = sum(inventory.values())
    if not total or not any(p.startswith('BDMV/STREAM/') and p.endswith('.m2ts') for p in inventory):
        raise ValueError('No M2TS stream in source ISO')
    ancestor = output
    while not ancestor.exists(): ancestor = ancestor.parent
    available = shutil.disk_usage(ancestor).free
    required = total * 2 + 768 * 1024 * 1024
    if available < required:
        raise ValueError(f'Insufficient output space: need {required / 1e9:.2f} GB, available {available / 1e9:.2f} GB')
    output.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.reexport-', dir=output))
    disc = temporary / 'disc'; disc.mkdir()
    candidate = temporary / 'BLURAY_VIDEO.iso'
    report = dict(schema_version=SCHEMA, status='running', source=dict(path=str(source), **fingerprint),
                  temporary_directory=str(temporary), required_space_bytes=required, available_space_bytes=available,
                  original_tree_directories=directories, files={}, corrected_counters=0,
                  tsmuxer=dict(path=str(tsmuxer), sha256=_hash_file(tsmuxer, checkpoint)),
                  helper=dict(path=str(helper), sha256=_hash_file(helper, checkpoint)))
    _atomic_json(report_path, report)
    try:
        report['source']['sha256'] = _hash_file(source, checkpoint,
            lambda done: progress('校验原 ISO', 2 + 8 * done / fingerprint['size']))
        for path in directories: (disc / PurePosixPath(path)).mkdir(parents=True, exist_ok=True)
        tree = _Tree(source); done = 0
        try:
            for path, size in inventory.items():
                digest = hashlib.sha256(); destination = disc / PurePosixPath(path)
                with destination.open('xb') as f:
                    for data in tree.chunks(path, size, checkpoint):
                        f.write(data); digest.update(data); done += len(data)
                        progress('提取 Blu-ray 文件', 10 + 30 * done / total)
                report['files'][path] = dict(size_bytes=size, original_sha256=digest.hexdigest(),
                                            repaired_sha256=digest.hexdigest())
        finally: tree.close()
        streams = [p for p in inventory if p.lower().endswith('.m2ts')]
        for index, path in enumerate(streams):
            checkpoint(); progress('修正 PCR 连续计数器：' + PurePosixPath(path).name, 40 + 15 * index / len(streams))
            destination = disc / PurePosixPath(path)
            metrics_path = temporary / f'transport-{index}.json'
            run([str(helper), 'repair', str(destination), str(metrics_path)])
            result = json.loads(metrics_path.read_text()); before, after = result['before'], result['after']
            if (before['sha256'] != report['files'][path]['original_sha256'] or not after['passed']
                    or before['protected_sha256'] != after['protected_sha256']
                    or before['payload_sha256'] != after['payload_sha256']
                    or before['bytes'] != after['bytes'] or before['packets'] != after['packets']):
                raise ValueError('Transport repair verification failed: ' + path)
            report['files'][path].update(transport=result, repaired_sha256=after['sha256'])
            report['corrected_counters'] += before['one_ahead_pcr_counters']
            _atomic_json(report_path, report)
        checkpoint(); progress('重新封装 Blu-ray ISO', 55)
        run([str(tsmuxer), '--pack-iso', str(disc), str(candidate), 'DISC_AUTHOR'])
        limit = 50_000_000_000 if fingerprint['size'] > 25_000_000_000 else 25_000_000_000
        if not candidate.is_file() or candidate.stat().st_size > limit:
            raise ValueError('New ISO missing or above source disc capacity class')
        progress('验证新 ISO 结构与全部文件', 65); checkpoint()
        if inspect_iso(str(candidate))['format'] != 'bluray': raise ValueError('New ISO structural validation failed')
        tree = _Tree(candidate); done = 0
        try:
            new_directories, new_inventory = tree.inventory(checkpoint)
            if new_directories != directories or new_inventory != inventory:
                raise ValueError('New ISO directory inventory differs from source')
            for path, size in inventory.items():
                digest = hashlib.sha256()
                for data in tree.chunks(path, size, checkpoint):
                    digest.update(data); done += len(data)
                    progress('验证新 ISO 文件内容', 65 + 25 * done / total)
                if digest.hexdigest() != report['files'][path]['repaired_sha256']:
                    raise ValueError('New ISO file content differs from verified repair: ' + path)
        finally: tree.close()
        output_hash = _hash_file(candidate, checkpoint,
            lambda done: progress('校验新 ISO', 90 + 9 * done / candidate.stat().st_size))
        if _fingerprint(source) != fingerprint: raise ValueError('Original ISO changed during reexport')
        checkpoint()
        if final.exists(): raise ValueError('Destination ISO appeared during processing')
        os.replace(candidate, final)
        report.update(status='verified', completed_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                      original_unchanged=True, complete_tree_verified=True,
                      output=dict(path=str(final), size_bytes=final.stat().st_size, sha256=output_hash))
        _atomic_json(report_path, report)
        # Only the unique directory created by this invocation is removed.
        def cleanup_error(function, path, error):
            # On macOS/exFAT removing a file can also remove its AppleDouble
            # companion between scandir and unlink. Ignore only that race.
            if not isinstance(error, FileNotFoundError): raise error
        shutil.rmtree(temporary, onexc=cleanup_error)
        report['temporary_removed'] = True
        _atomic_json(report_path, report)
        progress('Blu-ray 无损重导出验证完成', 100)
        return dict(iso=str(final), report=str(report_path), reused=False,
                    corrections=report['corrected_counters'], size_bytes=final.stat().st_size)
    except BaseException as error:
        report.update(status='interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'failed',
                      error=str(error), temporary_retained=temporary.exists())
        _atomic_json(report_path, report)
        raise
