"""Read-only checks for the multiplexed HDMV menu stream authored by DiscAuthor.

The input may be a bounded prefix of a 192-byte-packet M2TS. Partial final PES
packets are ignored, but any complete ICS encountered must be well formed.
This validates binary authoring fields, not a player's rendering or timing.
"""


def _pes_payloads(data, pid):
    pending = bytearray()
    for offset in range(0, len(data) - 191, 192):
        ts = data[offset + 4:offset + 192]
        if ts[0] != 0x47:
            raise ValueError(f'M2TS sync byte missing at packet {offset // 192}')
        if ((ts[1] & 31) << 8 | ts[2]) != pid:
            continue
        if ts[1] & 0x80 or ts[3] & 0xc0:
            raise ValueError('IG transport packet is damaged or scrambled')
        adaptation = (ts[3] >> 4) & 3
        if not adaptation:
            raise ValueError('IG transport packet has invalid adaptation control')
        if adaptation == 2:
            continue
        start = 5 + ts[4] if adaptation == 3 else 4
        if start > 188:
            raise ValueError('IG adaptation field exceeds its transport packet')
        if ts[1] & 0x40:
            if pending:
                raise ValueError('IG PES ended before its declared length')
            pending.extend(ts[start:])
        elif pending:
            pending.extend(ts[start:])
        else:
            continue
        if len(pending) < 6:
            continue
        if pending[:4] != b'\0\0\1\xbd':
            raise ValueError('IG stream has an invalid PES start code')
        length = int.from_bytes(pending[4:6], 'big')
        if not length:
            raise ValueError('IG PES must have an explicit packet length')
        end = length + 6
        if len(pending) < end:
            continue
        packet = pending[:end]
        pending.clear()
        if len(packet) < 9 or packet[6] & 0xc0 != 0x80:
            raise ValueError('IG PES has an invalid MPEG-2 header')
        body = 9 + packet[8]
        if body > len(packet):
            raise ValueError('IG PES optional header exceeds its packet')
        yield packet[body:]


def inspect_multiplexed_igs(data, pid=0x1400):
    """Return complete ICS header dictionaries; reject mismatched stream models.

    An empty list means no complete interactive composition was found in the
    prefix. Callers requiring a menu must reject that result. This parser is
    deliberately limited to unfragmented ICSs, as emitted by DiscAuthor.
    """
    elementary = b''.join(_pes_payloads(data, pid))
    results = []
    offset = 0
    while offset + 3 <= len(elementary):
        kind = elementary[offset]
        length = int.from_bytes(elementary[offset + 1:offset + 3], 'big')
        end = offset + 3 + length
        if end > len(elementary):
            if kind == 0x18:
                raise ValueError('IG interactive composition is truncated')
            break  # A bounded prefix may stop inside the final graphics object.
        raw = elementary[offset + 3:end]
        offset = end
        if kind != 0x18:
            continue
        if len(raw) < 13 or raw[8] != 0xc0:
            raise ValueError('IG interactive composition header is incomplete or fragmented')
        declared = int.from_bytes(raw[9:12], 'big')
        if declared != len(raw) - 12:
            raise ValueError('IG interactive composition length does not match its segment')
        if raw[12] != 0:
            raise ValueError('Primary-stream IG must declare multiplexed always-on mode')
        if len(raw) < 27:
            raise ValueError('Multiplexed IG is missing its timeout or page fields')
        if raw[13] & 0xfe or raw[18] & 0xfe:
            raise ValueError('IG timeout has nonzero reserved bits')
        result = {
            'width': int.from_bytes(raw[:2], 'big'),
            'height': int.from_bytes(raw[2:4], 'big'),
            'frame_rate_code': raw[4] >> 4,
            'composition_number': int.from_bytes(raw[5:7], 'big'),
            'stream_model': 0,
            'ui_model': 0,
            'composition_timeout_pts': int.from_bytes(raw[13:18], 'big'),
            'selection_timeout_pts': int.from_bytes(raw[18:23], 'big'),
            'user_timeout_duration': int.from_bytes(raw[23:26], 'big'),
            'page_count': raw[26],
            'segment_length': length,
            'interactive_composition_length': declared,
        }
        if not result['page_count']:
            raise ValueError('IG interactive composition has no pages')
        results.append(result)
    return results
