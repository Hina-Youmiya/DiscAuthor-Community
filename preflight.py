"""Read-only project checks shared by the UI and build entry point.

Capacity calculations intentionally mirror author.build: reserve 10% of nominal
capacity, subtract fixed audio bitrate, cap/minimum video bitrate by format, then
allow four encoded copies (five for project-name export) plus 500 MB working space. The contract tests run the
real build orchestration with a stub encoder so formula drift is detected.
"""
from copy import deepcopy
from pathlib import Path
import math
import shutil
import author
import encoding
import bluray
import menu_feedback
import iso_export

CAPACITY_BYTES = {'dvd5': 4_700_000_000, 'dvd9': 8_540_000_000,
                  'bd25': 25_000_000_000, 'bd50': 50_000_000_000}


def _overlap(a, b, inclusive=False):
    """Input rectangles are left/top/right/bottom, not width/height."""
    if inclusive:
        return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _check_menu(config, buttons, page):
    first=author.first_button_for(config, buttons)
    visited=set()
    pending=[first]
    while pending:
        current=pending.pop()
        if current in visited:
            continue
        if not 1 <= current <= len(buttons):
            raise ValueError(f'第 {page} 页的遥控目标超出按钮范围：{current}')
        visited.add(current)
        pending.extend(bluray.neighbours(buttons, current-1))
    missing=sorted(set(range(1, len(buttons)+1))-visited)
    if missing:
        numbers='、'.join(map(str, missing))
        raise ValueError(f'第 {page} 页从初选按钮 {first} 无法到达按钮：{numbers}')
    hd_rects=[]
    for b in buttons:
        x,y,w,h=b['rect']
        hd_rects.append((x,y,x+w,y+h))
    dvd_rects=([menu_feedback.dvd_button_rect(b, 576 if config['standard']=='pal' else 480)
                for b in buttons] if config['format']=='dvd' else None)
    for i, a in enumerate(hd_rects):
        for j in range(i+1, len(hd_rects)):
            if _overlap(a, hd_rects[j]):
                raise ValueError(f'第 {page} 页按钮 {i+1} 与按钮 {j+1} 的区域重叠，请调整位置')
            if dvd_rects is not None and _overlap(dvd_rects[i], dvd_rects[j], inclusive=True):
                raise ValueError(f'第 {page} 页按钮 {i+1} 与按钮 {j+1} 在 DVD 分辨率下重叠，请增大间距')


def _output_volume(output):
    """Find an existing directory on the destination volume, without mkdir."""
    try:
        target=Path(output).expanduser().resolve()
        candidate=target
        while not candidate.exists():
            candidate=candidate.parent
        if not candidate.is_dir():
            raise ValueError(f'输出路径的上级不是文件夹：{candidate}')
        return shutil.disk_usage(candidate)
    except OSError as exc:
        raise ValueError(f'无法检查输出目录：{output}') from exc


def check_project(config):
    """Probe real inputs, verify menu graph/geometry, and check capacity/space.

    Never mutates the supplied configuration or creates output directories.
    Durations are seconds, bitrate is kbit/s, and storage values use decimal GB.
    """
    if not isinstance(config, dict):
        raise ValueError('项目配置必须是对象')
    try:
        c=author.validate(deepcopy(config))
    except OSError as exc:
        raise ValueError(f'无法读取项目素材或字体：{exc}') from exc
    pages=[]
    if c.get('menu_enabled', True):
        first=c.get('first_button', 1)
        if type(first) is not int or not 1 <= first <= c['per_page']:
            raise ValueError('初选按钮应在每页节目数量范围内')
        pages=author.pages_for(c)
        for number, buttons in enumerate(pages, 1):
            _check_menu(c, buttons, number)
    seconds=sum(v['info']['duration'] for v in c['videos'])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('无法确定视频总时长')
    dvd=c['format']=='dvd'
    audio=192 if dvd else 384
    budget=int((CAPACITY_BYTES[c['capacity']] * .90 * 8 / seconds) / 1000)-audio
    bitrate=min(7500 if dvd else 24000, budget)
    if bitrate < (1800 if dvd else 5000):
        raise ValueError('视频总时长过长，请选择更大容量或减少视频')
    estimate=seconds * (bitrate+audio) * 1000 / 8
    # Flat publication may need a no-clobber copy on removable exFAT volumes.
    required=estimate * (5 if c.get('export_mode')=='project_name' else 4) + 500_000_000
    iso_export.job_root(c,author.DATA_ROOT/'output')
    usage=_output_volume(c.get('output_dir') or author.DATA_ROOT/'output')
    if usage.free < required:
        raise ValueError('输出盘空间不足，需要约 '+str(round(required/1e9, 1))+' GB 空间')
    return {'output_mode': encoding.plan(c,c['videos'][0]['info'])['mode'], 'encoding_warnings':encoding.warnings(c), 'valid': True, 'videos': len(c['videos']), 'pages': len(pages),
            'duration': seconds, 'video_bitrate_kbps': bitrate,
            'required_gb': math.ceil(required / 1e7) / 100,
            'available_gb': math.floor(usage.free / 1e7) / 100}
