"""Lossless repair of DiscAuthor's known malformed first AVC access unit.

This module only permutes whole, existing Annex-B NAL chunks in the first
complete video PES. It never changes an encoded picture, a timestamp, a TS/PES
header, or a file length. Unsupported layouts fail closed. This is a narrowly
scoped conformance repair, not a claim of physical-player compatibility.

``repair_iso`` writes an image in place and is for an unpublished, exclusively
owned generated image only. A delivered image must first be copied to a new
candidate. Callers must not publish any image after an exception, even if the
best-effort rollback succeeds.
"""
import binascii
import hashlib
import os
from pathlib import Path
import re

from bd_avc import MAX_SAMPLE_BYTES, audit_avc_access_units, check_avc_access_units

_START = re.compile(b'\x00\x00(?:\x00)?\x01')
_S = 2048


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _first_pes(data, pid):
    """Return a complete first PES and (PES offset, M2TS offset, length) spans."""
    pieces, spans = [], []
    count = 0
    last_cc = None
    complete = False
    for offset in range(0, len(data) - 191, 192):
        ts = data[offset + 4:offset + 192]
        if ts[0] != 0x47:
            raise ValueError('Invalid M2TS synchronization')
        if ((ts[1] & 31) << 8 | ts[2]) != pid:
            continue
        if ts[1] & 0x80 or ts[3] & 0xc0:
            raise ValueError('Damaged or scrambled AVC transport packet')
        adaptation = (ts[3] >> 4) & 3
        if not adaptation:
            raise ValueError('Invalid AVC adaptation control')
        if adaptation & 2:
            if 5 + ts[4] > 188:
                raise ValueError('Invalid AVC adaptation length')
            if ts[4] and ts[5] & 0x80:
                raise ValueError('Discontinuous AVC transport cannot be repaired')
        if not adaptation & 1:
            continue
        if ts[1] & 0x40 and pieces:
            complete = True
            break
        if not pieces and not ts[1] & 0x40:
            raise ValueError('M2TS prefix starts inside a video PES')
        cc = ts[3] & 15
        if last_cc is not None and cc != (last_cc + 1) % 16:
            raise ValueError('AVC continuity counter mismatch')
        last_cc = cc
        start = 5 + ts[4] if adaptation & 2 else 4
        chunk = ts[start:]
        if not chunk:
            raise ValueError('Empty AVC payload packet')
        spans.append((count, offset + 4 + start, len(chunk)))
        count += len(chunk)
        pieces.append(chunk)
    pes = b''.join(pieces)
    if not complete:
        raise ValueError('First video PES is not complete within bounded prefix')
    if len(pes) < 9 or pes[:3] != b'\x00\x00\x01' or not 0xe0 <= pes[3] <= 0xef:
        raise ValueError('Invalid first video PES')
    if pes[6] & 0xc0 != 0x80:
        raise ValueError('Invalid MPEG-2 PES header')
    body = 9 + pes[8]
    declared = int.from_bytes(pes[4:6], 'big')
    if body >= len(pes) or (declared and declared + 6 != len(pes)):
        raise ValueError('Invalid video PES length')
    return pes, body, spans


def _single_sei_type(nal):
    """Refuse composite SEI rather than modifying or splitting its payload."""
    data = nal[1:].replace(b'\x00\x00\x03', b'\x00\x00')
    pos = kind = size = 0
    while pos < len(data) and data[pos] == 255:
        kind += 255
        pos += 1
    if pos == len(data):
        raise ValueError('Truncated SEI type')
    kind += data[pos]
    pos += 1
    while pos < len(data) and data[pos] == 255:
        size += 255
        pos += 1
    if pos == len(data):
        raise ValueError('Truncated SEI size')
    size += data[pos]
    pos += 1
    if pos + size >= len(data) or data[pos + size:].rstrip(b'\x00') != b'\x80':
        raise ValueError('Composite or truncated initial SEI is unsupported')
    return kind


def _changes(before, after):
    if len(before) != len(after):
        raise ValueError('Repair changed byte length')
    ranges = []
    start = None
    for pos, (old, new) in enumerate(zip(before, after)):
        if old != new and start is None:
            start = pos
        if old == new and start is not None:
            ranges.append({'offset': start, 'before_hex': before[start:pos].hex(),
                           'after_hex': after[start:pos].hex(), 'length': pos - start})
            start = None
    if start is not None:
        ranges.append({'offset': start, 'before_hex': before[start:].hex(),
                       'after_hex': after[start:].hex(), 'length': len(before) - start})
    return ranges


def repair_m2ts_prefix(data, pid=0x1011):
    """Return (same-length repaired prefix, JSON-serializable evidence).

    Only a known initial prefix consisting of AUD, SPS, PPS and independent
    buffering-period, user-data and picture-timing SEI NALs is supported.
    Every sampled later access unit must already satisfy the authoring guard.
    """
    if len(data) > MAX_SAMPLE_BYTES:
        raise ValueError('AVC repair is limited to an 8 MiB prefix')
    pes, body, spans = _first_pes(data, pid)
    before_audit = audit_avc_access_units(data, pid)
    elementary = pes[body:]
    starts = list(_START.finditer(elementary))
    if not starts or any(elementary[:starts[0].start()]):
        raise ValueError('Unexpected bytes before first Annex-B NAL')
    prefix = []
    first_vcl = None
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(elementary)
        nal = elementary[match.end():end]
        if not nal or nal[0] & 0x80:
            raise ValueError('Invalid initial AVC NAL')
        kind = nal[0] & 31
        if kind in {1, 2, 3, 4, 5}:
            if kind != 5:
                raise ValueError('First AVC picture is not IDR')
            first_vcl = match.start()
            break
        if kind not in {6, 7, 8, 9}:
            raise ValueError('Unsupported initial AVC NAL type')
        if kind in {7, 8, 9} and match.end() - match.start() != 4:
            raise ValueError('Initial AUD/SPS/PPS must retain Annex-B zero_byte')
        sei = _single_sei_type(nal) if kind == 6 else None
        if kind == 6 and sei not in {0, 1, 5}:
            raise ValueError('Unsupported initial SEI message')
        key = {9: 0, 7: 1, 8: 2}.get(kind)
        if kind == 6:
            key = {0: 3, 5: 4, 1: 5}[sei]
        prefix.append({'kind': kind, 'sei': sei, 'key': key,
                       'chunk': elementary[match.start():end]})
    if first_vcl is None:
        raise ValueError('Initial IDR is absent from first video PES')
    keys = [x['key'] for x in prefix]
    if len(keys) != len(set(keys)) or set(keys) not in ({0, 1, 2, 3, 5}, {0, 1, 2, 3, 4, 5}):
        raise ValueError('Initial AVC header set is not the supported encoder layout')
    ordered = sorted(prefix, key=lambda x: x['key'])
    replacement = elementary[:starts[0].start()] + b''.join(x['chunk'] for x in ordered)
    if len(replacement) != first_vcl:
        raise ValueError('Initial NAL permutation changed prefix length')
    # All bytes after the non-VCL prefix, including every coded slice, are kept.
    changed_pes = pes[:body] + replacement + elementary[first_vcl:]
    result = bytearray(data)
    for pes_offset, file_offset, length in spans:
        result[file_offset:file_offset + length] = changed_pes[pes_offset:pes_offset + length]
    result = bytes(result)
    after_audit = check_avc_access_units(result, pid)
    ranges = _changes(data, result)
    report = {'changed': bool(ranges), 'physical_player_tested': False,
              'sample_bytes': len(data), 'video_pid': pid,
              'before_sha256': _sha(data), 'after_sha256': _sha(result),
              'first_pes_bytes': len(pes), 'pes_header_bytes': body,
              'first_vcl_elementary_offset': first_vcl,
              'nal_chunks_preserved': sorted(_sha(x['chunk']) for x in prefix) ==
                                      sorted(_sha(x['chunk']) for x in ordered),
              'coded_picture_payload_sha256': _sha(elementary[first_vcl:]),
              'before': before_audit, 'after': after_audit,
              'changed_ranges': ranges}
    return result, report


def _u16(b, p):
    return int.from_bytes(b[p:p + 2], 'little')


def _u32(b, p):
    return int.from_bytes(b[p:p + 4], 'little')


def _u64(b, p):
    return int.from_bytes(b[p:p + 8], 'little')


def _udf_streams(f):
    """Resolve only the UDF 2.50 layout emitted by our tsMuxer IsoWriter.

    Independent libbluray prefix reads are required before any write. This is
    intentionally not a general UDF implementation or an arbitrary-ISO editor.
    """
    size = os.fstat(f.fileno()).st_size

    def read(offset, length):
        if offset < 0 or length < 0 or offset + length > size:
            raise ValueError('UDF extent exceeds image')
        f.seek(offset)
        data = f.read(length)
        if len(data) != length:
            raise ValueError('Truncated UDF read')
        return data

    def tag(data, kind, location=None):
        length = _u16(data, 10)
        if (len(data) < 16 or _u16(data, 0) != kind or length + 16 > len(data) or
                sum(data[:4] + data[5:16]) % 256 != data[4] or
                binascii.crc_hqx(data[16:16 + length], 0) != _u16(data, 8) or
                (location is not None and _u32(data, 12) != location)):
            raise ValueError('Invalid UDF descriptor')
        return data

    anchor = tag(read(256 * _S, _S), 2, 256)
    seq_len, seq_lba = _u32(anchor, 16), _u32(anchor, 20)
    if seq_len > 1024 * 1024 or seq_len % _S:
        raise ValueError('Unsupported UDF descriptor sequence')
    seq = read(seq_lba * _S, seq_len)
    descriptors = [seq[n:n + _S] for n in range(0, len(seq), _S)]
    pds = [b for b in descriptors if _u16(b, 0) == 5]
    lvs = [b for b in descriptors if _u16(b, 0) == 6]
    if len(pds) != 1 or len(lvs) != 1:
        raise ValueError('Unsupported UDF volume count')
    pd, lv = tag(pds[0], 5), tag(lvs[0], 6)
    part, part_size = _u32(pd, 188), _u32(pd, 192)
    if (_u32(lv, 212) != _S or _u16(lv, 240) != 0x250 or
            _u32(lv, 264) != 70 or _u32(lv, 268) != 2 or
            lv[440:446] != b'\x01\x06\x01\x00\x00\x00' or
            lv[446:448] != b'\x02\x40' or
            lv[451:474].rstrip(b'\x00') != b'*UDF Metadata Partition' or
            (part + part_size) * _S > size):
        raise ValueError('Unsupported UDF partition layout')
    md_lba = _u32(lv, 486)
    md = tag(read((part + md_lba) * _S, _S), 266, md_lba)
    if md[27] != 250 or _u32(md, 212) != 8 or _u32(md, 208):
        raise ValueError('Unsupported UDF metadata allocation')
    meta, meta_bytes = part + _u32(md, 220), _u64(md, 56)
    if _u32(md, 216) != meta_bytes or meta_bytes > 64 * 1024 * 1024:
        raise ValueError('Unsupported UDF metadata length')

    def metadata(lbn, length=_S):
        if lbn * _S + length > meta_bytes:
            raise ValueError('UDF metadata address exceeds partition')
        return read((meta + lbn) * _S, length)

    fsd = tag(metadata(0), 256, 0)
    root = _u32(fsd, 404)
    if _u16(fsd, 408) != 1:
        raise ValueError('Unsupported UDF root partition')

    def directory(lbn):
        fe = tag(metadata(lbn), 266, lbn)
        if fe[27] != 4 or _u32(fe, 208) or _u32(fe, 212) != 8:
            raise ValueError('Unsupported UDF directory')
        length, pos = _u32(fe, 216), _u32(fe, 220)
        if length != _u64(fe, 56) or length > 65536:
            raise ValueError('Unsupported UDF directory length')
        entries, off = {}, 0
        data = metadata(pos, length)
        while off < len(data):
            if len(data) - off < 38:
                raise ValueError('Truncated UDF file identifier')
            nl, impl = data[off + 19], _u16(data, off + 36)
            elen = (38 + impl + nl + 3) // 4 * 4
            item = tag(data[off:off + elen], 257)
            if not item[18] & 8:
                name = item[38 + impl:38 + impl + nl]
                if not name or name[0] not in (8, 16) or _u16(item, 28) != 1:
                    raise ValueError('Unsupported UDF file identifier')
                label = name[1:].decode('latin1' if name[0] == 8 else 'utf-16-be')
                if label in entries or item[18] & 4:
                    raise ValueError('Duplicate or deleted UDF file identifier')
                entries[label] = _u32(item, 24)
            off += elen
        return entries

    try:
        bdmv = directory(root)['BDMV']
        stream = directory(bdmv)['STREAM']
    except KeyError as exc:
        raise ValueError('BDMV stream directory absent') from exc
    files = {}
    for name, lbn in directory(stream).items():
        if not re.fullmatch(r'\d{5}\.m2ts', name):
            raise ValueError('Unexpected authored stream entry')
        fe = tag(metadata(lbn), 266, lbn)
        count, first = _u32(fe, 212), 216 + _u32(fe, 208)
        if fe[27] not in (5, 249) or count % 16 or first + count > _S or not count:
            raise ValueError('Unsupported authored file allocation')
        extents, total = [], 0
        for off in range(first, first + count, 16):
            length, pos, partition = _u32(fe, off), _u32(fe, off + 4), _u16(fe, off + 8)
            if not 0 < length < 0x40000000 or partition or pos + (length + _S - 1) // _S > part_size:
                raise ValueError('Unsupported UDF file extent')
            extents.append((total, (part + pos) * _S, length))
            total += length
        if total != _u64(fe, 56):
            raise ValueError('UDF file length differs from extent sum')
        files[name] = {'size': total, 'extents': extents}
    if not files:
        raise ValueError('No authored AVC streams')
    return files


def _mapped_ranges(extents, start, length):
    result = []
    end = start + length
    for file_offset, iso_offset, extent_size in extents:
        a, b = max(start, file_offset), min(end, file_offset + extent_size)
        if a < b:
            result.append((a - start, iso_offset + a - file_offset, b - a))
    if sum(x[2] for x in result) != length:
        raise ValueError('Incomplete ISO extent mapping')
    return result


def _read_prefix(f, record):
    length = min(record['size'], MAX_SAMPLE_BYTES)
    pieces = []
    for _, iso_offset, count in _mapped_ranges(record['extents'], 0, length):
        f.seek(iso_offset)
        data = f.read(count)
        if len(data) != count:
            raise ValueError('Truncated ISO stream')
        pieces.append(data)
    return b''.join(pieces)


def _write_patch(f, offset, data):
    f.seek(offset)
    if f.write(data) != len(data):
        raise OSError('Short ISO patch write')


def repair_iso(iso, checkpoint=lambda: None, *, generated_image=False):
    """Normalize an exclusively owned *unpublished generated image* in place.

    Preflight and independent libbluray mapping verification cover all streams
    before the first write. Only exact expected original byte ranges may be
    overwritten. On any failure, callers must reject publication. Rollback is
    attempted without cancellation callbacks so a cancelled export can restore
    its original header bytes; I/O faults can still prevent restoration.
    """
    if not generated_image:
        raise ValueError('ISO repair requires an unpublished owned generated image')
    from burner import UDFBluray, fingerprint
    iso = Path(iso)
    original = fingerprint(iso)
    report = {'iso': str(iso), 'image_bytes': original[2], 'physical_player_tested': False,
              'scope': 'Lossless permutation of initial AVC NAL chunks only',
              'streams': [], 'changed_ranges': [], 'passed': False}
    reader = UDFBluray(iso)
    patches = []
    try:
        with open(iso, 'rb') as f:
            records = _udf_streams(f)
            if set(reader.names('BDMV/STREAM')) != set(records):
                raise ValueError('UDF readers disagree about stream names')
            for name, record in sorted(records.items()):
                checkpoint()
                before = _read_prefix(f, record)
                independent, size = reader.read('BDMV/STREAM/' + name, MAX_SAMPLE_BYTES)
                if size != record['size'] or independent != before:
                    raise ValueError('UDF raw extents differ from libbluray stream read')
                _, stream_report = repair_m2ts_prefix(before)
                stream_report.update(name=name, stream_bytes=size, udf_mapping_verified=True)
                report['streams'].append(stream_report)
                for change in stream_report['changed_ranges']:
                    old, new = bytes.fromhex(change['before_hex']), bytes.fromhex(change['after_hex'])
                    for src, absolute, length in _mapped_ranges(record['extents'], change['offset'], len(old)):
                        patches.append((absolute, old[src:src + length], new[src:src + length]))
    finally:
        reader.close()
    patches.sort()
    if any(a[0] + len(a[1]) > b[0] for a, b in zip(patches, patches[1:])):
        raise ValueError('Overlapping ISO patch ranges')
    checkpoint()
    if fingerprint(iso) != original:
        raise ValueError('ISO changed during repair preflight')
    attempted = []
    with open(iso, 'r+b', buffering=0) as f:
        opened = os.fstat(f.fileno())
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if opened_identity != original:
            raise ValueError('ISO identity changed before opening for repair')
        for offset, old, _ in patches:
            f.seek(offset)
            if f.read(len(old)) != old:
                raise ValueError('ISO expected original bytes changed')
        if fingerprint(iso) != original:
            raise ValueError('ISO changed before the first repair write')
        try:
            for offset, old, new in patches:
                checkpoint()
                attempted.append((offset, old))
                _write_patch(f, offset, new)
            os.fsync(f.fileno())
            if os.fstat(f.fileno()).st_size != original[2]:
                raise ValueError('ISO size changed during repair')
            expected = {item['name']: item['after_sha256'] for item in report['streams']}
            for name, record in sorted(records.items()):
                checkpoint()
                data = _read_prefix(f, record)
                if _sha(data) != expected[name]:
                    raise ValueError('ISO repaired prefix differs from planned bytes')
                check_avc_access_units(data)
            if fingerprint(iso)[:3] != original[:3]:
                raise ValueError('ISO path identity changed during repair')
        except BaseException:
            try:
                for offset, old in reversed(attempted):
                    _write_patch(f, offset, old)
                os.fsync(f.fileno())
            except BaseException as rollback_error:
                raise RuntimeError('ISO repair failed and rollback failed; reject this image') from rollback_error
            raise
    report['changed_ranges'] = [{'offset': pos, 'length': len(old), 'before_hex': old.hex(),
                                 'after_hex': new.hex()} for pos, old, new in patches]
    report.update(passed=True, changed=bool(patches), changed_bytes=sum(len(old) for _, old, _ in patches))
    return report
