"""Read-only optical-media discovery and DVD/BD playback-source inspection.

No mount, erase, write, eject or decrypt commands are issued here. Physical
devices come only from DiscRecording/diskutil enumeration; a filesystem folder
can be opened explicitly but is never advertised as an inserted optical disc.
"""
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import threading
import time
from copy import deepcopy

import burner


DISK_ID = re.compile(r'disk\d+(?:s\d+)*\Z')
_CACHE_SECONDS = 2.0
_scan_lock = threading.Lock()
_scan_cache = None
_scan_completed = 0.0


class _DiscRecordingReader(burner.DiscRecording):
    def media_nodes(self):
        """kDRDeviceMediaBSDNameKey is a read-only media-info dictionary key."""
        array = self.dr.DRCopyDeviceArray()
        result = {}
        if not array:
            return result
        try:
            for index in range(self.cf.CFArrayGetCount(array)):
                device = self.cf.CFArrayGetValueAtIndex(array, index)
                info = self.dr.DRDeviceCopyInfo(device)
                status = self.dr.DRDeviceCopyStatus(device)
                try:
                    ident = self.string(self.get(info, 'IORegistryEntryPathKey'))
                    media = self.get(status, 'MediaInfoKey')
                    node = self.string(self.get(media, 'MediaBSDNameKey'))
                    node = node.removeprefix('/dev/')
                    if ident.startswith('IOService:') and DISK_ID.fullmatch(node):
                        result[ident] = node
                finally:
                    if info:
                        self.cf.CFRelease(info)
                    if status:
                        self.cf.CFRelease(status)
        finally:
            self.cf.CFRelease(array)
        return result


def _device_snapshot():
    try:
        reader = _DiscRecordingReader()
        devices = reader.devices()
        nodes = reader.media_nodes()
        for device in devices:
            device['device_identifier'] = nodes.get(device['id'], '')
        return {'devices': devices, 'error': None}
    except (OSError, ValueError, AttributeError) as exc:
        return {'devices': [], 'error': str(exc)}


def _diskutil(*args):
    try:
        result = subprocess.run(['/usr/sbin/diskutil', *args], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('光盘设备查询失败: ' + str(exc)) from exc
    if result.returncode:
        raise ValueError('光盘设备查询失败: ' + result.stderr.decode('utf-8', 'replace').strip())
    try:
        data = plistlib.loads(result.stdout)
    except (ValueError, plistlib.InvalidFileException) as exc:
        raise ValueError('光盘设备查询返回了无效数据') from exc
    if not isinstance(data, dict):
        raise ValueError('光盘设备查询返回了无效数据')
    return data


def _entries(directory):
    """Case-insensitive optical names; do not follow links outside the source."""
    result = {}
    with os.scandir(directory) as items:
        for count, item in enumerate(items):
            if count >= 10000:
                raise ValueError('光盘目录包含过多项目')
            name = item.name.upper()
            if name in result:
                raise ValueError('光盘目录包含冲突的文件名')
            if item.is_symlink():
                continue
            result[name] = Path(item.path)
    return result


def _read(path, limit=65536):
    if path is None:
        return b'', 0
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError('光盘目录中有无效文件')
        return stream.read(limit), metadata.st_size


def _dvd_size(directory):
    entries = _entries(directory)
    header, size = _read(entries.get('VIDEO_TS.IFO'))
    if not header.startswith(b'DVDVIDEO-VMG'):
        raise ValueError('DVD-Video 管理文件无效')
    titles = [name for name in entries if re.fullmatch(r'VTS_\d\d_0.IFO', name)]
    if not titles:
        raise ValueError('DVD-Video 缺少节目')
    total = size
    for title in titles:
        data, size = _read(entries[title])
        if not data.startswith(b'DVDVIDEO-VTS'):
            raise ValueError('DVD-Video 节目信息无效')
        _, first_size = _read(entries.get(title[:7] + '1.VOB'), 16)
        if first_size < 2048:
            raise ValueError('DVD-Video 节目视频缺失或不完整')
        total += size
    for name, path in entries.items():
        if re.fullmatch(r'(?:VIDEO_TS|VTS_\d\d_\d)\.(?:VOB|BUP)', name):
            total += _read(path, 0)[1]
    return total


def _bd_size(directory):
    entries = _entries(directory)
    index, index_size = _read(entries.get('INDEX.BDMV'))
    objects, object_size = _read(entries.get('MOVIEOBJECT.BDMV'))
    if (index[:4] != b'INDX' or index[4:8] not in (b'0100', b'0200', b'0300')
            or objects[:4] != b'MOBJ'):
        raise ValueError('Blu-ray 管理文件无效')
    try:
        playlists = _entries(entries['PLAYLIST'])
        clips = _entries(entries['CLIPINF'])
        streams = _entries(entries['STREAM'])
    except (KeyError, NotADirectoryError) as exc:
        raise ValueError('Blu-ray 节目目录不完整') from exc
    names = [name for name in playlists if re.fullmatch(r'\d{5}\.MPLS', name)]
    if not names:
        raise ValueError('Blu-ray 缺少播放列表')
    total = index_size + object_size
    counted = set()
    for name in names:
        data, size = _read(playlists[name], 4 * 1024 * 1024)
        total += size
        if data[:4] != b'MPLS' or size > len(data) or len(data) < 20:
            raise ValueError('Blu-ray 播放列表无效')
        offset = int.from_bytes(data[8:12], 'big')
        if offset < 20 or offset + 10 > len(data):
            raise ValueError('Blu-ray 播放列表无效')
        count = int.from_bytes(data[offset+6:offset+8], 'big')
        if not count:
            raise ValueError('Blu-ray 播放列表没有节目')
        position = offset + 10
        for _ in range(count):
            length = int.from_bytes(data[position:position+2], 'big')
            clip = data[position+2:position+7].decode('ascii', 'replace')
            if (length < 18 or position+2+length > len(data)
                    or not re.fullmatch(r'\d{5}', clip)):
                raise ValueError('Blu-ray 播放项目无效')
            header, clip_size = _read(clips.get(clip + '.CLPI'), 8)
            _, stream_size = _read(streams.get(clip + '.M2TS'), 16)
            if header[:4] != b'HDMV' or stream_size < 192:
                raise ValueError('Blu-ray 节目视频缺失或不完整')
            if clip not in counted:
                total += clip_size + stream_size
                counted.add(clip)
            position += 2 + length
    return total


def inspect_source(path):
    """Inspect a user-selected ISO, disc root, VIDEO_TS or BDMV directory.

    This checks navigation/file structure, not the entire recording or every
    menu action. Playback is still needed for visual/audio confirmation.
    """
    if not isinstance(path, str) or not path.strip() or '\0' in path:
        raise ValueError('请选择光盘、ISO 或 VIDEO_TS／BDMV 文件夹')
    try:
        source = Path(path).expanduser().resolve(strict=True)
        metadata = source.stat()
        if stat.S_ISREG(metadata.st_mode):
            result = burner.inspect_iso(str(source))
            return {**result, 'kind': 'iso', 'label': source.stem,
                    'readable': True, 'playable': True, 'structure_valid': True,
                    'menu_verified': False}
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError('请选择光盘、ISO 或 VIDEO_TS／BDMV 文件夹')
        if source.name.upper() in ('VIDEO_TS', 'BDMV'):
            source = source.parent
        entries = _entries(source)
        dvd = entries.get('VIDEO_TS')
        bd = entries.get('BDMV')
        if bool(dvd) == bool(bd):
            raise ValueError('目录不是单一的 DVD-Video 或 Blu-ray Video')
        video_dir = dvd or bd
        if not video_dir.is_dir():
            raise ValueError('光盘视频目录无效')
        size = _dvd_size(video_dir) if dvd else _bd_size(video_dir)
        return {'path': str(source), 'kind': 'folder', 'format': 'dvd' if dvd else 'bluray',
                'label': source.name, 'size_bytes': size, 'readable': True, 'playable': True,
                'structure_valid': True, 'menu_verified': False}
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise ValueError('光盘或文件不存在，请刷新后重试') from exc
    except PermissionError as exc:
        raise ValueError('无法读取光盘或文件，请检查读取权限') from exc
    except OSError as exc:
        raise ValueError('读取光盘或文件失败: ' + str(exc)) from exc


def _flatten(nodes):
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        yield from _flatten(node.get('Partitions', []))
        yield from _flatten(node.get('APFSVolumes', []))


def _physical_optical(info):
    if str(info.get('VirtualOrPhysical', '')).lower() == 'virtual':
        return False
    if str(info.get('BusProtocol', '')).lower() == 'disk image':
        return False
    return bool(info.get('OpticalDeviceType') or info.get('OpticalMediaType'))


def _format(info):
    media = str(info.get('OpticalMediaType', '')).upper()
    if media.startswith('BD') or 'BLU' in media:
        return 'bluray'
    if media.startswith('DVD'):
        return 'dvd'
    return 'unknown'


def _scan_discs():
    snapshot = _device_snapshot()
    errors = [snapshot['error']] if snapshot.get('error') else []
    try:
        listing = _diskutil('list', '-plist')
    except ValueError as exc:
        listing = {}
        errors.append(str(exc))
    nodes = {item['DeviceIdentifier']: item for item in _flatten(listing.get('AllDisksAndPartitions', []))
             if DISK_ID.fullmatch(str(item.get('DeviceIdentifier', '')))}
    # DiscRecording already proves the device is an optical drive and supplies
    # its current media BSD name. diskutil info can block on unrelated mounted
    # disks even when queried for an optical node; never use it on this path.
    candidates = list(snapshot.get('devices', []))
    dr_ids = {item.get('id') for item in candidates}
    infos = {}
    if not candidates:
        # A read-only optical drive may be absent from DiscRecording. Only
        # optical-looking entries from the system list qualify for fallback;
        # do not wake or interrogate every attached hard disk and image.
        hints = []
        for name, node in nodes.items():
            if not re.fullmatch(r'disk\d+', name):
                continue
            content = str(node.get('Content', '')).upper()
            optical_hint = (bool(node.get('OpticalMediaType') or node.get('OpticalDeviceType'))
                            or content.startswith(('CD_', 'DVD_', 'BD_')) or content == 'UDF'
                            or not content and bool(node.get('MountPoint')) and not node.get('Partitions'))
            if optical_hint:
                hints.append(name)
        for name in hints[:3]:
            try:
                infos[name] = _diskutil('info', '-plist', name)
            except ValueError:
                continue
    found = []
    seen = set()
    known_nodes = {match.group() for item in candidates
                   if (match := re.match(r'disk\d+', str(item.get('device_identifier', ''))))}
    for name, info in infos.items():
        if _physical_optical(info) and name not in known_nodes:
            candidates.append({'id': 'diskutil:' + name,
                               'name': info.get('MediaName') or name,
                               'device_identifier': name, 'media': {'present': True,
                               'blank': False, 'busy': False, 'format': _format(info)}})
    for device in candidates:
        ident = device.get('id', '')
        if not ident.startswith(('IOService:', 'diskutil:')) or ident in seen:
            continue
        seen.add(ident)
        node = device.get('device_identifier', '')
        if not isinstance(node, str) or not DISK_ID.fullmatch(node):
            node = ''
        whole = re.match(r'disk\d+', node).group() if node else ''
        info = infos.get(whole, {})
        media = device.get('media', {})
        present = bool(media.get('present'))
        blank = bool(media.get('blank'))
        item = {'id': ident, 'name': device.get('name', ident),
                'device_identifier': node, 'device_node': '/dev/' + node if node else '',
                'format': media.get('format', 'unknown'), 'label': '',
                'capacity_bytes': media.get('capacity_bytes') or info.get('TotalSize') or info.get('Size'),
                'present': present, 'blank': blank, 'busy': bool(media.get('busy')),
                'mounted': False, 'readable': False, 'playable': False, 'path': '',
                'structure_valid': False, 'menu_verified': False,
                'reason': 'no_media' if not present else 'blank' if blank else 'not_mounted'}
        if present and not blank and whole and (ident in dr_ids or _physical_optical(info)):
            volume_nodes = [value for name, value in nodes.items()
                            if name == whole or name.startswith(whole + 's')]
            for volume in volume_nodes:
                mount = volume.get('MountPoint')
                if not isinstance(mount, str) or not mount.startswith('/'):
                    continue
                item['mounted'] = True
                item['mount_path'] = mount
                item['label'] = volume.get('VolumeName') or Path(mount).name
                break
            # Inventory must never touch the medium: even scandir/stat on a
            # mounted optical UDF volume can block indefinitely in the kernel.
            # "playable" means this supported medium can be opened by the
            # player, not that its contents or menus have been verified.
            if volume_nodes and item['format'] in ('dvd', 'bluray'):
                item.update(path='/dev/r' + whole, readable=True, playable=True, reason='ready')
        found.append(item)
    return {'devices': found, 'error': '; '.join(errors) or None}


def detect_discs(*, force_refresh=False):
    """Read-only single-flight discovery; overlapping refreshes share a scan."""
    global _scan_cache, _scan_completed
    started = time.monotonic()
    with _scan_lock:
        now = time.monotonic()
        if _scan_cache is not None and (_scan_completed >= started or
                                       not force_refresh and now - _scan_completed < _CACHE_SECONDS):
            return deepcopy(_scan_cache)
        result = _scan_discs()
        _scan_cache, _scan_completed = deepcopy(result), time.monotonic()
        return deepcopy(result)


def resolve_disc(device_id):
    """Revalidate the selected system device immediately before playback."""
    if not isinstance(device_id, str) or not device_id:
        raise ValueError('请选择已插入的可播放光盘')
    snapshot = detect_discs(force_refresh=True)
    item = next((item for item in snapshot['devices'] if item['id'] == device_id), None)
    if item is None:
        raise ValueError('光驱已断开，请刷新设备列表')
    if not item['playable']:
        messages = {'no_media': '光驱中没有光盘', 'blank': '空白光盘还没有可播放内容',
                    'not_mounted': '光盘尚未挂载，请等待系统读取后刷新',
                    'invalid_structure': '光盘不是可读取的 DVD-Video 或 Blu-ray Video'}
        raise ValueError(messages.get(item['reason'], '光盘暂时不可播放'))
    if item['id'].startswith('IOService:'):
        current = next((entry for entry in _device_snapshot()['devices'] if entry['id'] == item['id']), None)
        if (current is None or current.get('device_identifier') != item['device_identifier']
                or not current.get('media', {}).get('present') or current.get('media', {}).get('blank')):
            raise ValueError('光盘或文件不存在，请刷新后重试')
    else:
        current = next((entry for entry in detect_discs(force_refresh=True)['devices']
                        if entry['id'] == item['id']), None)
        if current is None or current['path'] != item['path'] or not current['playable']:
            raise ValueError('光盘或文件不存在，请刷新后重试')
    return {'path': item['path'], 'format': item['format'], 'readable': True, 'playable': True,
            'structure_valid': False, 'menu_verified': False, 'kind': 'disc', 'device_id': item['id'],
            'device_identifier': item['device_identifier'], 'device_node': item['device_node'],
            'label': item['label'], 'capacity_bytes': item['capacity_bytes']}
