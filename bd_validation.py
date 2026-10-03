"""Bounded checks for DiscAuthor's own AVC Blu-ray exports.

These detect known authoring regressions before publication. They are not a
complete HRD simulation, an optical readback, or a hardware compatibility test.
"""
import hashlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

from burner import UDFBluray, fingerprint
from bd_ig import inspect_multiplexed_igs
from bd_avc import check_avc_access_units

SAMPLE_BYTES = 8 * 1024 * 1024


def run_probe(args, checkpoint, timeout=45):
    """Drain diagnostic output while honoring cancellation; always reap child."""
    with subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                checkpoint()
                try:
                    _, diagnostic = process.communicate(timeout=.1)
                    return process.returncode, diagnostic
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Blu-ray header inspection timed out')
        except BaseException:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise


def check_hrd_trace(trace):
    fields = {}
    for line in trace.splitlines():
        match = re.search(r'\[trace_headers[^]]*\]\s+\d+\s+(\S+)\s+[01]+\s+=\s+(-?\d+)\s*$', line)
        if match:
            fields.setdefault(match[1], set()).add(int(match[2]))

    def one(name):
        values = fields.get(name, set())
        if len(values) != 1:
            raise ValueError('missing or changing AVC field: ' + name)
        return next(iter(values))

    # Internally encoded x264 streams have one CPB schedule and stable SPS.
    # Fail closed instead of guessing how to validate other encoder contracts.
    if one('nal_hrd_parameters_present_flag') != 1 or one('cpb_cnt_minus1') != 0:
        raise ValueError('expected a single NAL-HRD CPB schedule')
    rate = (one('bit_rate_value_minus1[0]') + 1) << (6 + one('bit_rate_scale'))
    size = (one('cpb_size_value_minus1[0]') + 1) << (4 + one('cpb_size_scale'))
    delays = sorted(fields.get('initial_cpb_removal_delay[0]', set()))
    if not delays or any(delay <= 0 or delay * rate > 90000 * size for delay in delays):
        raise ValueError(f'initial_cpb_removal_delay={delays}; allowed maximum={90000 * size // rate}')
    return {'bit_rate': rate, 'cpb_size_bits': size,
            'maximum_initial_delay': 90000 * size // rate, 'initial_delays': delays}


def validate_iso(iso, ffmpeg, work, checkpoint=lambda: None, *, menu_pages=None):
    """Read every authored stream's prefix, without mounting or writing the ISO."""
    iso = Path(iso)
    before = fingerprint(iso)
    report = {'passed': False, 'scope': 'First 8 MiB / first 5 AVC frames of each stream; known-defect checks only',
              'physical_player_tested': False, 'expected_menu_pages': menu_pages, 'streams': []}
    reader = UDFBluray(iso)
    try:
        names = sorted(name for name in reader.names('BDMV/STREAM') if re.fullmatch(r'\d{5}\.m2ts', name))
        if not names:
            raise ValueError('Blu-ray has no authored streams')
        if menu_pages is not None and (type(menu_pages) is not int or not 0 <= menu_pages < len(names)):
            raise ValueError('Invalid authored Blu-ray menu count')
        with tempfile.TemporaryDirectory(prefix='bd-validation-', dir=work) as temporary:
            sample = Path(temporary) / 'sample.m2ts'
            for index,name in enumerate(names):
                checkpoint()
                data, size = reader.read('BDMV/STREAM/' + name, SAMPLE_BYTES)
                # Do not present a deliberately truncated TS packet to FFmpeg.
                data = data[:len(data) // 192 * 192]
                if not data:
                    raise ValueError('Empty Blu-ray stream: ' + name)
                # Inspect the bytes before a demuxer can move CodecPrivate /
                # extradata. A tolerant decoder accepting the file is not
                # evidence that its access-unit order conforms to H.264.
                try:
                    avc_order = check_avc_access_units(data)
                except ValueError as exc:
                    raise ValueError(f'Blu-ray AVC 结构检查失败：{name}（{exc}）') from exc
                try:
                    compositions=inspect_multiplexed_igs(data)
                    if menu_pages is not None and bool(compositions)!=(index < menu_pages):
                        raise ValueError('Interactive menu stream does not match the project')
                    for ic in compositions:
                        if any(ic[key] for key in ('composition_timeout_pts','selection_timeout_pts','user_timeout_duration')):
                            raise ValueError('Authored still-menu timeout must be disabled')
                except ValueError as exc:
                    raise ValueError(f'Blu-ray 菜单检查失败：{name}（{exc}）') from exc
                sample.write_bytes(data)
                returncode, diagnostic = run_probe([str(ffmpeg), '-hide_banner', '-nostdin', '-nostats',
                    '-threads', '1', '-i', str(sample), '-map', '0:v:0', '-c:v', 'copy',
                    '-bsf:v', 'trace_headers', '-frames:v', '5', '-f', 'null', '-'],
                    checkpoint)
                checkpoint()
                try:
                    if returncode:
                        raise ValueError('AVC header inspection failed')
                    hrd = check_hrd_trace(diagnostic)
                except ValueError as exc:
                    raise ValueError(f'Blu-ray 时序检查失败：{name}（{exc}）') from exc
                report['streams'].append({'name': name, 'stream_bytes': size,
                    'sample_bytes': len(data), 'sample_sha256': hashlib.sha256(data).hexdigest(),
                    'avc_access_units': avc_order, 'hrd': hrd, 'ig_compositions': compositions})
        if fingerprint(iso) != before:
            raise ValueError('ISO changed during Blu-ray validation')
        report['passed'] = True
        return report
    finally:
        reader.close()
