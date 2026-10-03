"""Read the actual AVC bytes inside a bounded DiscAuthor M2TS prefix.

No FFmpeg demuxer/extradata is involved. This is a guard for our x264 authoring
contract (AUD-delimited, repeated SPS/PPS at IDR), not a complete AVC validator
or a claim of hardware-player compatibility. H.264 7.4.1.2.3 requires an AUD,
when present, to be first in its access unit, and a buffering-period SEI, when
present, to be the first SEI message in that access unit. The first AU begins
at the first NAL of the bitstream: initial in-band SPS/PPS are not exempt.
"""
import re

MAX_SAMPLE_BYTES = 8 * 1024 * 1024
_START_CODE = re.compile(b'\x00\x00\x01')
_VCL = {1, 2, 3, 4, 5}
_NEW_AU_PREFIX = {6, 7, 8, 9, 14, 15, 16, 17, 18}


def _elementary_stream(data, pid):
    """Join video PES payloads; tolerate only the final deliberately cut PES."""
    pending = bytearray()
    chunks = []
    pes_count = 0

    def finish(final=False):
        nonlocal pes_count
        if not pending:
            return
        if len(pending) < 9:
            if final:
                return
            raise ValueError('AVC PES header is truncated')
        if pending[:3] != b'\x00\x00\x01' or not 0xe0 <= pending[3] <= 0xef:
            raise ValueError('AVC PID does not contain a video PES')
        if pending[6] & 0xc0 != 0x80:
            raise ValueError('AVC PES has an invalid MPEG-2 header')
        body = 9 + pending[8]
        declared = int.from_bytes(pending[4:6], 'big')
        end = declared + 6 if declared else len(pending)
        if declared and body > end:
            raise ValueError('AVC PES optional header exceeds its length')
        if len(pending) < body:
            if final:
                return
            raise ValueError('AVC PES optional header is truncated')
        if len(pending) < end:
            if not final:
                raise ValueError('AVC PES ended before its declared length')
            end = len(pending)
        if body <= end:
            chunks.append(bytes(pending[body:end]))
            pes_count += 1

    for offset in range(0, len(data) - 191, 192):
        ts = data[offset + 4:offset + 192]
        if ts[0] != 0x47:
            raise ValueError(f'M2TS sync byte missing at packet {offset // 192}')
        if ((ts[1] & 31) << 8 | ts[2]) != pid:
            continue
        if ts[1] & 0x80 or ts[3] & 0xc0:
            raise ValueError('AVC transport packet is damaged or scrambled')
        adaptation = (ts[3] >> 4) & 3
        if not adaptation:
            raise ValueError('AVC transport packet has invalid adaptation control')
        if adaptation == 2:
            continue
        start = 5 + ts[4] if adaptation == 3 else 4
        if start > 188:
            raise ValueError('AVC adaptation field exceeds its transport packet')
        if ts[1] & 0x40:
            finish()
            pending.clear()
        elif not pending:
            raise ValueError('AVC prefix begins inside a PES packet')
        pending.extend(ts[start:])
    finish(final=True)
    if not chunks:
        raise ValueError('No AVC video PES in M2TS prefix')
    return b''.join(chunks), pes_count


def _rbsp(data):
    return data.replace(b'\x00\x00\x03', b'\x00\x00')


def _first_mb(nal):
    # Only the first ue(v) is needed. Our encoder does not emit ASO/FMO or
    # redundant pictures, so a new first_mb=0 also catches a missing AUD.
    bits = _rbsp(nal[1:10])
    zeros = 0
    for byte in bits:
        for shift in range(7, -1, -1):
            if byte & (1 << shift):
                return 0 if zeros == 0 else 1
            zeros += 1
    return None  # final NAL may have been cut by the bounded sample


def _sei_types(nal, *, complete):
    payload = _rbsp(nal[1:]).rstrip(b'\x00')
    result = []
    pos = 0
    while pos < len(payload):
        if payload[pos:] == b'\x80':
            break
        kind = size = 0
        while pos < len(payload) and payload[pos] == 255:
            kind += 255
            pos += 1
        if pos >= len(payload):
            if complete:
                raise ValueError('Truncated AVC SEI payload type')
            break
        kind += payload[pos]
        pos += 1
        while pos < len(payload) and payload[pos] == 255:
            size += 255
            pos += 1
        if pos >= len(payload):
            if complete:
                raise ValueError('Truncated AVC SEI payload size')
            break
        size += payload[pos]
        pos += 1
        if pos + size > len(payload):
            if complete:
                raise ValueError('Truncated AVC SEI payload')
            break
        result.append(kind)
        pos += size
    return result


def audit_avc_access_units(data, pid=0x1011):
    """Return evidence and order errors from a prefix beginning at file byte 0.

    A final incomplete transport packet/NAL is permitted. Complete AU prefixes
    are checked even when their final slice is cut. This inspects bytes only and
    does not decode pictures, simulate HRD, or validate an entire long clip.
    """
    if len(data) > MAX_SAMPLE_BYTES:
        raise ValueError('AVC inspection is limited to the first 8 MiB')
    elementary, pes_count = _elementary_stream(data, pid)
    starts = list(_START_CODE.finditer(elementary))
    if not starts:
        raise ValueError('No Annex-B AVC NAL units in video PES')
    if any(elementary[:starts[0].start()]):
        raise ValueError('Unexpected bytes before the first AVC NAL')
    units = []
    current = {'nal_types': [], 'sei_payload_types': [], 'vcl_count': 0}
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(elementary)
        nal = elementary[match.end():end].rstrip(b'\x00')
        if not nal:
            if i + 1 == len(starts):
                break
            raise ValueError('Empty AVC NAL unit')
        if nal[0] & 0x80:
            raise ValueError('AVC forbidden_zero_bit is set')
        kind = nal[0] & 31
        # Do not split on a late AUD before VCL: it belongs to the already
        # started AU and must be reported as an order error, not discarded.
        new_picture = kind in _VCL and _first_mb(nal) == 0
        if current['vcl_count'] and (kind in _NEW_AU_PREFIX or new_picture):
            units.append(current)
            current = {'nal_types': [], 'sei_payload_types': [], 'vcl_count': 0}
        current['nal_types'].append(kind)
        if kind in _VCL:
            current['vcl_count'] += 1
        if kind == 6:
            current['sei_payload_types'].extend(_sei_types(nal, complete=i + 1 < len(starts)))
    if current['vcl_count']:
        units.append(current)
    if not units:
        raise ValueError('No AVC coded picture in video prefix')

    errors = []
    idr_count = 0
    for index, unit in enumerate(units):
        kinds = unit['nal_types']
        sei = unit['sei_payload_types']
        label = f'AVC access unit {index}'
        if kinds[0] != 9:
            errors.append(f'{label}: AUD must be first; NAL order={kinds[:16]}')
        if kinds.count(9) != 1:
            errors.append(f'{label}: expected exactly one AUD')
        if 0 in sei and (sei[0] != 0 or sei.count(0) != 1):
            errors.append(f'{label}: buffering-period SEI must be the first SEI message; types={sei}')
        if 5 in kinds:
            idr_count += 1
            before = kinds[:kinds.index(5)]
            if 7 not in before or 8 not in before or before.index(7) > before.index(8):
                errors.append(f'{label}: IDR is missing preceding in-band SPS/PPS')
    if not idr_count or 5 not in units[0]['nal_types']:
        errors.append('AVC clip must begin with an IDR access unit')
    return {
        'passed': not errors, 'video_pid': pid, 'sample_bytes': len(data),
        'video_pes_packets': pes_count, 'access_units': len(units),
        'idr_access_units': idr_count,
        'first_access_unit_nal_types': units[0]['nal_types'],
        'first_access_unit_sei_payload_types': units[0]['sei_payload_types'],
        'errors': errors,
    }


def check_avc_access_units(data, pid=0x1011):
    report = audit_avc_access_units(data, pid)
    if report['errors']:
        raise ValueError('; '.join(report['errors'][:4]))
    return report
