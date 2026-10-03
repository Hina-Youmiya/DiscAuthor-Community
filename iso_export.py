"""Save complete disc images under project names without overwriting existing files."""
from pathlib import Path
import ctypes
import errno
import hashlib
import os
import re
import stat
import sys
import tempfile
import unicodedata


_UNSUPPORTED = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}


class UnsafeISOPublication(OSError):
    """The filesystem cannot atomically publish a file without replacing another."""

    def __init__(self, source_path, *, staged_path=None):
        self.source_path = str(source_path)
        self.staged_path = str(staged_path) if staged_path else None
        super().__init__(
            errno.ENOTSUP,
            '输出盘不支持安全平铺保存，完整 ISO 已保留在任务文件夹：' + self.source_path,
            self.source_path)


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _remove_owned(path, identity):
    """Never unlink a file substituted by another writer, even during cleanup."""
    try:
        if _identity(os.stat(path, follow_symlinks=False)) == identity:
            Path(path).unlink()
    except OSError:
        pass


def validate_options(config):
    # Missing keys belong to existing projects and keep their original layout.
    config.setdefault('export_mode', 'task_folder')
    config.setdefault('export_format_suffix', True)
    if config['export_mode'] not in ('task_folder', 'project_name'):
        raise ValueError('ISO 保存方式无效')
    if type(config['export_format_suffix']) is not bool:
        raise ValueError('ISO 格式后缀设置无效')
    if not isinstance(config.get('output_dir', ''), str):
        raise ValueError('输出文件夹路径无效')
    return config


def job_root(config, default_root):
    """Use the selected folder for finished ISOs, a hidden child for job data."""
    base = Path(config.get('output_dir') or default_root).expanduser().resolve()
    if config.get('export_mode', 'task_folder') == 'task_folder':
        return base
    root = base / '.DiscAuthor'
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError('制作记录文件夹不可用')
    return root


def iso_basename(config):
    title = unicodedata.normalize('NFC', str(config.get('title') or ''))
    title = ''.join('_' if unicodedata.category(char) in ('Cc', 'Cf') or
                    char in '<>:"/\\|?*' else char for char in title)
    title = title.strip(' .')
    if title.lower().endswith('.iso'):
        title = title[:-4].rstrip(' .')
    # Keep room for the format, collision counter and extension on common disks.
    title = title.encode('utf-8')[:180].decode('utf-8', errors='ignore').rstrip(' .') or 'Untitled'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', title):
        title = '_' + title
    suffix = ('_DVD' if config['format'] == 'dvd' else '_BD') if config.get('export_format_suffix', True) else ''
    return title + suffix + '.iso'


def _rename_exclusive(source, target):
    """Atomic no-clobber publication where the filesystem supports it."""
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        # RENAME_EXCL from the installed macOS SDK's sys/stdio.h.
        if rename(os.fsencode(source), os.fsencode(target), 0x00000004):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    else:
        os.link(source, target, follow_symlinks=False)
        _remove_owned(source, _identity(os.stat(target, follow_symlinks=False)))


def _copy_private(source, directory, checkpoint):
    """Copy and verify under a private .partial name on the destination volume.

    No public .iso exists while copying, including after SIGKILL/power loss.
    Completion requires byte count, SHA-256 readback, stable identities and fsync.
    """
    original = _identity(os.stat(source, follow_symlinks=False))
    fd, name = tempfile.mkstemp(prefix='.discauthor-', suffix='.partial', dir=directory)
    target = Path(name)
    owned_inode = os.fstat(fd)
    try:
        with os.fdopen(fd, 'w+b') as output:
            read_fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(read_fd, 'rb') as data:
                if not stat.S_ISREG(os.fstat(data.fileno()).st_mode) or _identity(os.fstat(data.fileno())) != original:
                    raise ValueError('复制期间 ISO 发生变化，已停止保存')
                digest = hashlib.sha256()
                count = 0
                while True:
                    checkpoint()
                    block = data.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    count += len(block)
                if _identity(os.fstat(data.fileno())) != original or count != original[2]:
                    raise ValueError('复制期间 ISO 发生变化，已停止保存')
            output.flush()
            os.fsync(output.fileno())
            copied_identity = _identity(os.fstat(output.fileno()))
            output.seek(0)
            copied_digest = hashlib.sha256()
            while True:
                checkpoint()
                block = output.read(1024 * 1024)
                if not block:
                    break
                copied_digest.update(block)
            if copied_digest.digest() != digest.digest() or os.fstat(output.fileno()).st_size != count:
                raise ValueError('ISO 复制校验失败，原文件已保留')
            checkpoint()
            destination = os.stat(target, follow_symlinks=False)
            if ((destination.st_dev, destination.st_ino) != (owned_inode.st_dev, owned_inode.st_ino) or
                    _identity(destination) != copied_identity or
                    _identity(os.fstat(output.fileno())) != copied_identity or
                    _identity(os.stat(source, follow_symlinks=False)) != original):
                raise ValueError('复制期间 ISO 发生变化，已停止保存')
            return target, original, _identity(destination)
    except BaseException:
        try:
            current = os.stat(target, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (owned_inode.st_dev, owned_inode.st_ino):
                os.unlink(target)
        except FileNotFoundError:
            pass
        raise


def _commit(source, directory, basename, checkpoint):
    stem = basename[:-4]
    for number in range(1, 10001):
        checkpoint()
        target = directory / (basename if number == 1 else f'{stem} ({number}).iso')
        try:
            try:
                _rename_exclusive(source, target)
            except OSError as error:
                if error.errno not in _UNSUPPORTED:
                    raise
                # A hardlink publishes the already-complete file atomically and
                # fails if any target exists. Never use rename()/replace() over
                # a placeholder: an external writer could replace it first.
                try:
                    os.link(source, target, follow_symlinks=False)
                except OSError as fallback_error:
                    if fallback_error.errno not in _UNSUPPORTED:
                        raise
                    raise UnsafeISOPublication(source) from fallback_error
                _remove_owned(source, _identity(os.stat(target, follow_symlinks=False)))
            return target
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
    raise ValueError('同名 ISO 文件过多，请更改项目名称')


def publish_iso(job, iso):
    """Move an already validated, complete ISO to the common output directory.

    Source ownership and target placement are checked before touching anything.
    Cross-volume jobs copy into a private .partial, verify and flush it before
    exclusive commit. Unsupported filesystems fail closed and retain the source.
    """
    source = Path(iso)
    if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError('完成的 ISO 不是普通文件')
    if not source.resolve().is_relative_to(job.out.resolve()):
        raise ValueError('ISO 不属于当前制作任务')
    fallback = job.out.parent.parent if job.out.parent.name == '.DiscAuthor' else job.out.parent
    directory = Path(job.config.get('output_dir') or fallback).expanduser().resolve()
    if directory.is_relative_to(job.out.resolve()):
        raise ValueError('成品文件夹不能位于当前制作文件夹内')
    job._wait_to_run()
    directory.mkdir(parents=True, exist_ok=True)
    basename = iso_basename(job.config)
    # Tools have closed their output, but durable publication also requires the
    # file data to be flushed before the final name can become visible.
    with source.open('rb') as stream:
        os.fsync(stream.fileno())
    try:
        return _commit(source, directory, basename, job._wait_to_run)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
    # Cross-device move: preserve the original if cancellation or copying fails.
    temporary, original, temporary_identity = _copy_private(source, directory, job._wait_to_run)
    retain = False
    try:
        target = _commit(temporary, directory, basename, job._wait_to_run)
        # The public result is already complete. A retained original is safer
        # than marking a valid publication failed if its removal is refused.
        _remove_owned(source, original)
        return target
    except UnsafeISOPublication as error:
        retain = True  # A complete, verified private copy can be recovered too.
        raise UnsafeISOPublication(source, staged_path=temporary) from error
    finally:
        if not retain:
            _remove_owned(temporary, temporary_identity)
