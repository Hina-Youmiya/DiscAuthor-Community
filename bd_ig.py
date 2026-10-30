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


def _inspect_pages(raw, offset, count):
    """Decode the no-effect pages emitted by our main-menu/popup authors."""
    def take(size):
        nonlocal offset
        end = offset + size
        if end > len(raw):
            raise ValueError('IG page or button data is truncated')
        value = raw[offset:end]
        offset = end
        return value

    def number(size):
        return int.from_bytes(take(size), 'big')

    pages = []
    for _ in range(count):
        page = {'id': number(1), 'version': number(1), 'uo_mask': take(8).hex()}
        # Each empty effect sequence has window_count=0 and effect_count=0.
        if take(4) != bytes(4):
            raise ValueError('IG validation does not support page transition effects')
        page.update(animation_frame_rate_code=number(1),
                    default_selected_button_id_ref=number(2),
                    default_activated_button_id_ref=number(2), palette_id_ref=number(1))
        bogs = []
        for _ in range(number(1)):
            bog = {'default_valid_button_id_ref': number(2), 'buttons': []}
            for _ in range(number(1)):
                button = take(35)
                if button[4] & 0x7f:
                    raise ValueError('IG auto-action button has nonzero reserved bits')
                nav_count = int.from_bytes(button[33:35], 'big')
                bog['buttons'].append({
                    'id': int.from_bytes(button[:2], 'big'),
                    'auto_action': bool(button[4] & 0x80),
                    'normal_object_id': int.from_bytes(button[17:19], 'big'),
                    'selected_object_id': int.from_bytes(button[23:25], 'big'),
                    'activated_object_id': int.from_bytes(button[29:31], 'big'),
                    'commands': [take(12).hex() for _ in range(nav_count)],
                })
            bogs.append(bog)
        page['bogs'] = bogs
        pages.append(page)
    if offset != len(raw):
        raise ValueError('IG page length does not match its composition')
    return pages


def inspect_multiplexed_igs(data, pid=0x1400, *, expected_ui_model=0):
    """Return complete ICS header dictionaries; reject mismatched stream models.

    An empty list means no complete interactive composition was found in the
    prefix. Callers requiring a menu must reject that result. This parser is
    deliberately limited to unfragmented ICSs, as emitted by DiscAuthor.
    expected_ui_model=0 requires an always-on main menu, 1 requires a title
    popup, and None reports either. Stream model must always match main M2TS.
    """
    if expected_ui_model not in (None, 0, 1):
        raise ValueError('Invalid expected IG user-interface model')
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
        if raw[12] & 0xbf:
            raise ValueError('Primary-stream IG must declare multiplexed mode without reserved bits')
        ui_model = raw[12] >> 6
        if expected_ui_model is not None and ui_model != expected_ui_model:
            raise ValueError('Primary-stream IG has an unexpected user-interface model')
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
            'ui_model': ui_model,
            'composition_timeout_pts': int.from_bytes(raw[13:18], 'big'),
            'selection_timeout_pts': int.from_bytes(raw[18:23], 'big'),
            'user_timeout_duration': int.from_bytes(raw[23:26], 'big'),
            'page_count': raw[26],
            'segment_length': length,
            'interactive_composition_length': declared,
        }
        if not result['page_count']:
            raise ValueError('IG interactive composition has no pages')
        result['pages'] = _inspect_pages(raw, 27, result['page_count'])
        results.append(result)
    return results
