"""Explicit, inspectable SDR encoding plans for DVD-Video and ordinary BDMV."""
from fractions import Fraction

MODES = ('auto', '1080p24', '1080p24_exact', '1080i60', '1080i50')
DEFAULTS = {'quality': 'high', 'denoise': 'off', 'anti_flicker': 'off', 'source_scan': 'auto'}


def settings(config):
    raw = config.get('encoding', {})
    if not isinstance(raw, dict):
        raise ValueError('encoding must be an object')
    result = DEFAULTS | raw
    for key, allowed in {'quality': ('high', 'balanced', 'efficient'), 'denoise': ('off', 'light'),
                         'anti_flicker': ('off', 'light'),
                         'source_scan': ('auto', 'progressive', 'tff', 'bff')}.items():
        if result[key] not in allowed:
            raise ValueError(f'Invalid encoding.{key}: {result[key]}')
    if config.get('format') == 'dvd' and result['quality'] == 'efficient':
        raise ValueError('Efficient single-pass mode is available for Blu-ray only')
    return result


def rate(value):
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def near(a, b):
    return abs(a - b) < .002


def bd_mode(config):
    mode = config.get('bd_mode', 'auto')
    if mode != 'auto':
        return mode
    info = next((v.get('info', {}) for v in config.get('videos', [])), {})
    fps = rate(info.get('fps'))
    if near(fps, 24000 / 1001):
        return '1080p24'
    if near(fps, 24):
        return '1080p24_exact'
    if near(fps, 25) or near(fps, 50):
        return '1080i50'
    return '1080i60'


def scan_type(config, info):
    override = settings(config)['source_scan']
    if override != 'auto':
        return override
    order = info.get('field_order')
    # FFprobe's second letter is the displayed field order, not coded order.
    return {'progressive': 'progressive', 'tt': 'tff', 'bt': 'tff',
            'bb': 'bff', 'tb': 'bff'}.get(order, 'unknown')


def check_source(info, path):
    if info.get('color_transfer') in ('smpte2084', 'arib-std-b67') or \
            info.get('color_primaries') == 'bt2020' or \
            info.get('color_space') in ('bt2020nc', 'bt2020c'):
        raise ValueError(f'HDR / BT.2020 素材请先转换为 SDR Rec.709：{path}')


def color_input(info, menu=False):
    """Keep known SDR metadata; missing values use disclosed assumptions."""
    rgb = menu or any(x in (info.get('pix_fmt') or '') for x in ('rgb', 'bgr', 'gbr'))
    base = 'bt709' if rgb or info.get('height', 1080) > 576 else (
        'bt470bg' if info.get('height') == 576 else 'smpte170m')
    spaces = {'bt709', 'bt470bg', 'smpte170m', 'smpte240m', 'fcc'}
    primaries = {'bt709', 'bt470bg', 'bt470m', 'smpte170m', 'smpte240m', 'film'}
    transfers = {'bt709', 'bt470bg', 'bt470m', 'smpte170m', 'smpte240m',
                 'linear', 'iec61966-2-1', 'iec61966-2-4', 'bt1361e'}
    space = info.get('color_space') if info.get('color_space') in spaces else base
    prim = info.get('color_primaries') if info.get('color_primaries') in primaries else (space if space in primaries else base)
    trc = info.get('color_transfer') if info.get('color_transfer') in transfers else (
        'iec61966-2-1' if rgb else 'bt709')
    rng = info.get('color_range') if info.get('color_range') in ('tv', 'pc') else (
        'pc' if rgb or (info.get('pix_fmt') or '').startswith('yuvj') else 'tv')
    return {'space': 'bt709' if rgb else space, 'primaries': prim, 'trc': trc,
            'range': 'tv' if rgb else rng, 'rgb': rgb}


def plan(config, info, menu=False):
    opts = settings(config)
    dvd = config['format'] == 'dvd'
    pal = config.get('standard') == 'pal'
    mode = ('pal' if pal else 'ntsc') if dvd else bd_mode(config)
    if menu and not dvd:
        mode = '1080p24'
    fps = ('25' if pal else '30000/1001') if dvd else {
        '1080p24': '24000/1001', '1080p24_exact': '24',
        '1080i60': '30000/1001', '1080i50': '25'}[mode]
    scan = 'progressive' if menu else scan_type(config, info)
    input_fps = rate(info.get('fps'))
    interlaced = (not menu and (not dvd and mode.startswith('1080i') or
                  dvd and not (scan == 'progressive' and near(input_fps, rate(fps)))))
    film = dvd and not pal and not menu and scan == 'progressive' and near(input_fps, 24000/1001)
    # x264's Blu-ray progressive-frame coding is valid for matching 25/29.97p
    # sources. This changes the coded sequence syntax, not just container tags.
    # Real or unknown interlaced inputs must retain the field-aware pipeline.
    progressive_compat = (not dvd and not menu and opts['quality'] == 'efficient'
                          and interlaced and scan == 'progressive'
                          and near(input_fps, rate(fps)))
    filters = []
    if not menu:
        if scan in ('tff', 'bff'):
            filters += [f'bwdif=mode=send_field:parity={scan}:deint=all']
        elif scan == 'unknown':
            filters += ['bwdif=mode=send_field:parity=auto:deint=interlaced']
        else:
            filters += ['setfield=mode=prog']
    color = color_input(info, menu)
    if color['rgb']:
        # Establish a known YUV matrix for RGB before the colorspace filter.
        filters += ['scale=iw:ih:out_color_matrix=bt709:out_range=tv:flags=accurate_rnd', 'format=yuv444p']
    target = ('bt470bg' if pal else 'smpte170m') if dvd else 'bt709'
    filters += [f"colorspace=ispace={color['space']}:iprimaries={color['primaries']}:"
                f"itrc={color['trc']}:irange={color['range']}:space={target}:"
                f"primaries={target}:trc=bt709:range=tv:format=yuv420p:dither=fsb"]
    if dvd:
        h = 576 if pal else 480
        sar = '64/45' if pal else '32/27'
        # DAR includes the source SAR (including anamorphic DV/DVD material).
        filters += [f"scale=w='max(2,trunc(min(720,{h}*dar/({sar}))/2)*2)':"
                    f"h='max(2,trunc(min({h},720*({sar})/dar)/2)*2)':flags=lanczos+accurate_rnd",
                    f'pad=720:{h}:trunc((ow-iw)/4)*2:trunc((oh-ih)/4)*2', f'setsar={sar}']
    else:
        filters += ['scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:flags=lanczos+accurate_rnd',
                    'pad=1920:1080:trunc((ow-iw)/4)*2:trunc((oh-ih)/4)*2', 'setsar=1']
    if not menu and opts['denoise'] == 'light':
        filters += ['hqdn3d=1.5:1.5:3:3']
    lowpass = not menu and opts['anti_flicker'] == 'light'
    if lowpass and (not interlaced or film or progressive_compat):
        filters += ["convolution=0m='1 2 1':0rdiv=0.25:0mode=column"]
    if film:
        filters += ['fps=24000/1001', 'telecine=first_field=top:pattern=23', 'setfield=mode=tff']
    elif interlaced and not progressive_compat:
        fieldfps = '50' if fps == '25' else '60000/1001'
        filters += [f'fps={fieldfps}', 'tinterlace=mode=interleave_top' + (':flags=cvlpf' if lowpass else '')]
    else:
        filters += [f'fps={fps}', 'setfield=mode=prog']
    return {'filter': ','.join(filters), 'mode': mode, 'fps': fps,
            'interlaced': bool(interlaced), 'source_scan': scan,
            'color_space': target, 'color_primaries': target, 'color_transfer': 'bt709',
            'color_range': 'tv', 'source_color': color,
            'progressive_compat': bool(progressive_compat),
            'encoder': 'mpeg2video' if dvd else 'libx264', 'hardware_encoding': False,
            'passes': 2 if not menu and opts['quality'] == 'high' else 1,
            'preset': 'slow' if opts['quality'] == 'high' else 'medium',
            'telecine': bool(film), 'settings': opts}


def warnings(config):
    result = []
    opts = settings(config)
    for index, video in enumerate(config.get('videos', [])):
        info = video.get('info', {})
        p = plan(config, info)
        source_fps = rate(info.get('fps'))
        target_fps = rate(p['fps']) * (2 if p['interlaced'] else 1)
        source_temporal = source_fps * (2 if scan_type(config, info) in ('tff', 'bff') else 1)
        def add(code, source='', target=''):
            result.append(dict(code=code, video_index=index, source=source, target=target))
        if p['source_scan'] == 'unknown':
            add('unknown_scan', str(info.get('field_order') or 'unknown'), 'auto')
        if any(not info.get(k) or info.get(k) in ('unknown', 'unspecified', 'reserved') for k in
               ('color_space', 'color_primaries', 'color_transfer', 'color_range')):
            add('assumed_color', ' / '.join(str(info.get(k) or '?') for k in ('color_space', 'color_transfer', 'color_range')),
                f"{p['source_color']['space']} / {p['source_color']['trc']} / {p['source_color']['range']}")
        native = near(source_temporal, target_fps) or (p['interlaced'] and near(source_temporal * 2, target_fps)) or p['telecine']
        if not native:
            add('cadence_change', f'{source_temporal:.3f}', f'{target_fps:.3f}')
        if not p['interlaced'] and target_fps <= 24.01 and source_temporal > 25:
            add('motion_to_24p', f'{source_temporal:.3f}', f'{target_fps:.3f}')
        if opts['denoise'] != 'off' or opts['anti_flicker'] != 'off':
            add('filters_soften')
    return result
