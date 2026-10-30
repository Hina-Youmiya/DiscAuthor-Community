#!/usr/bin/env python3
"""Read-only Blu-ray menu return integration check using the real libbluray VM.

Example:
  python3 scripts/check_menu_navigation.py --iso sample.iso \
    --library /path/libbluray.2.dylib --menu-map menu-map.json --report result.json

menu-map.json: {"pages": [[{"title": 1, "rect": [130,270,1660,190]}, ...]],
"remember_selection": true, "first_button": 1}. Page links use "page" (zero
based) instead of "title". These are the expected authoring button coordinates,
not navigation command bytes. DiscAuthor's pages_for(project) supplies pages.
The default playlist layout is menu pages first, followed by numbered titles.
Use --no-menu instead of --menu-map for a single-title, stop-at-end image.
No disc is mounted, burned, erased or ejected. This is software navigation
validation, not video decoding, frame rendering or physical-player approval.
"""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import time


EVENTS = {1: 'error', 2: 'read_error', 3: 'encrypted', 5: 'title',
          6: 'playlist', 7: 'playitem', 10: 'end_of_title', 22: 'playlist_stop',
          25: 'still', 26: 'still_time', 28: 'idle', 29: 'popup_available',
          30: 'menu_visible', 33: 'uo_mask'}


class Event(C.Structure):
    _fields_ = [('event', C.c_uint32), ('param', C.c_uint32)]


class Overlay(C.Structure):
    _fields_ = [('pts', C.c_int64), ('plane', C.c_uint8), ('command', C.c_uint8)]


class DiscInfo(C.Structure):
    # Stable public BLURAY_DISC_INFO prefix; no private player memory is read.
    _fields_ = [('detected', C.c_uint8), ('disc_name', C.c_void_p),
                ('volume_id', C.c_void_p), ('disc_id', C.c_uint8 * 20),
                ('no_menu_support', C.c_uint8), ('first_play', C.c_uint8),
                ('top_menu', C.c_uint8), ('num_titles', C.c_uint32)]


class PlaylistInfo(C.Structure):
    _fields_ = [('index', C.c_uint32), ('playlist', C.c_uint32), ('duration', C.c_uint64)]


OVERLAY_CALLBACK = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(Overlay))


class Player:
    def __init__(self, library, iso, budget, timeout):
        self.lib = C.CDLL(str(library))
        ptr, integer = C.c_void_p, C.c_int
        declarations = {
            'bd_open': (ptr, [C.c_char_p, C.c_char_p]),
            'bd_close': (None, [ptr]), 'bd_play': (integer, [ptr]),
            'bd_read_ext': (integer, [ptr, ptr, integer, C.POINTER(Event)]),
            'bd_menu_call': (integer, [ptr, C.c_int64]),
            'bd_user_input': (integer, [ptr, C.c_int64, C.c_uint32]),
            'bd_mouse_select': (integer, [ptr, C.c_int64, C.c_uint16, C.c_uint16]),
            'bd_tell_time': (C.c_uint64, [ptr]), 'bd_get_title_size': (C.c_uint64, [ptr]),
            'bd_seek_time': (C.c_uint64, [ptr, C.c_uint64]),
            'bd_register_overlay_proc': (None, [ptr, ptr, OVERLAY_CALLBACK]),
            'bd_get_disc_info': (C.POINTER(DiscInfo), [ptr]),
            'bd_get_playlist_info': (C.POINTER(PlaylistInfo), [ptr, C.c_uint32, C.c_uint]),
            'bd_free_title_info': (None, [C.POINTER(PlaylistInfo)]),
        }
        for name, (result, arguments) in declarations.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, arguments
        self.handle = self.lib.bd_open(os.fsencode(iso), None)
        if not self.handle:
            raise RuntimeError('libbluray could not open this ISO or BDMV directory')
        self.buffer = C.create_string_buffer(6144 * 4)
        self.state, self.events, self.overlay_events = {}, [], []
        self.visible_menu_playlist = None
        self.budget, self.deadline = budget, time.monotonic() + timeout
        self.callback = OVERLAY_CALLBACK(self._overlay)
        self.lib.bd_register_overlay_proc(self.handle, None, self.callback)
        info = self.lib.bd_get_disc_info(self.handle).contents
        self.disc = {'detected': info.detected, 'top_menu': info.top_menu,
                     'num_titles': info.num_titles, 'first_play': info.first_play}
        if self.lib.bd_play(self.handle) != 1:
            self.close()
            raise RuntimeError('bd_play failed')

    def _overlay(self, handle, overlay):
        if overlay:
            self.overlay_events.append([overlay.contents.plane, overlay.contents.command])

    def close(self):
        if self.handle:
            self.lib.bd_close(self.handle)
            self.handle = None

    def read(self, event_only=False):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Navigation case exceeded its time limit')
        event = Event()
        count = self.lib.bd_read_ext(self.handle, self.buffer, 0 if event_only else len(self.buffer), C.byref(event))
        if count < 0 or event.event in (1, 2, 3):
            raise RuntimeError(f'libbluray read failed: count={count}, event={event.event}/{event.param}')
        self.budget['bytes_read'] += count
        if self.budget['bytes_read'] > self.budget['limit_bytes']:
            raise RuntimeError('Bounded read budget exceeded')
        if event.event:
            self.state[event.event] = event.param
            if event.event == 30:
                self.visible_menu_playlist = self.state.get(6) if event.param else None
            if event.event in EVENTS:
                self.events.append([EVENTS[event.event], event.param])
        return count, event.event

    def until(self, condition):
        for _ in range(20000):
            count, event = self.read()
            if condition():
                return
            if count == 0 and event in (0, 26, 28):
                time.sleep(.001)
        raise RuntimeError('Navigation did not settle: ' + repr(self.events[-12:]))

    def menu(self, page):
        start = len(self.events)
        self.until(lambda: self.state.get(6) == page and self.state.get(30) == 1
                   and self.visible_menu_playlist == page
                   and ['menu_visible', 1] in self.events[start:])

    def title(self, title, playlist):
        start = len(self.events)
        def selected_title():
            for name, value in self.events[start:]:
                if name == 'title' and value not in (0, 65535, title):
                    raise AssertionError(f'Confirmation selected title {value}, expected {title}')
            return (self.state.get(5) == title and self.state.get(6) == playlist
                    and self.state.get(30, 0) == 0)
        self.until(selected_title)

    def activate(self, button=None):
        if button:
            x, y, width, height = button['rect']
            selected = self.lib.bd_mouse_select(self.handle, -1, int(x + width / 2), int(y + height / 2))
            if selected < 0:
                raise RuntimeError('Menu button could not be selected')
        if self.lib.bd_user_input(self.handle, -1, 16) < 0:
            raise RuntimeError('Menu confirmation was rejected')

    def duration(self, playlist):
        info = self.lib.bd_get_playlist_info(self.handle, playlist, 0)
        if not info:
            raise RuntimeError('Playlist metadata missing')
        try:
            return info.contents.duration / 90000
        finally:
            self.lib.bd_free_title_info(info)

    def sample_title(self, title, playlist, requested_bytes=256 * 1024):
        before = self.budget['bytes_read']
        size = self.lib.bd_get_title_size(self.handle)
        target = min(requested_bytes, max(6144, size // 8))
        while self.budget['bytes_read'] - before < target:
            _, event = self.read()
            if self.state.get(5) != title or self.state.get(6) != playlist or self.state.get(30, 0):
                raise AssertionError('Menu activated without a user request while reading the title')
            if event == 10:
                raise AssertionError('Title ended before the requested playback sample')
        return {'bytes': self.budget['bytes_read'] - before,
                'position_seconds': round(self.lib.bd_tell_time(self.handle) / 90000, 3)}

    def popup_ready(self, title, playlist):
        # In-band IG must actually reach the graphics decoder before POPUP can
        # operate. Seeking before this event would skip the first composition.
        before = self.budget['bytes_read']
        while self.state.get(29) != 1:
            _, event = self.read()
            if event == 10 or self.state.get(6) != playlist or self.state.get(5) != title:
                raise AssertionError('Title ended or changed without advertising a popup menu')
            if self.state.get(30, 0):
                raise AssertionError('Hidden popup activated before any user request')
            if self.budget['bytes_read'] - before > 16 * 1024 * 1024:
                raise AssertionError('No popup advertised within the bounded 16 MiB preroll')
        return {'additional_bytes_before_available': self.budget['bytes_read'] - before,
                'position_seconds': round(self.lib.bd_tell_time(self.handle) / 90000, 3)}

    def trace(self):
        return {'events': self.events, 'overlay_callback_count': len(self.overlay_events),
                'state': {EVENTS[key]: value for key, value in self.state.items() if key in EVENTS}}


def validate_map(data):
    pages = data['pages']
    if not isinstance(pages, list) or not pages:
        raise ValueError('menu-map must contain a nonempty pages list')
    titles = {}
    for page, buttons in enumerate(pages):
        if not isinstance(buttons, list) or not buttons:
            raise ValueError('Each menu page requires buttons')
        for button in buttons:
            rect = button.get('rect')
            if not isinstance(rect, list) or len(rect) != 4 or not all(isinstance(n, (int, float)) for n in rect):
                raise ValueError('Each expected button requires [x,y,width,height]')
            x, y, width, height = rect
            if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1920 or y + height > 1080:
                raise ValueError('Expected button rectangle lies outside 1920x1080')
            if ('title' in button) == ('page' in button):
                raise ValueError('Each expected button must identify a title or page')
            if 'title' in button:
                title = button['title']
                if type(title) is not int or title < 1 or title in titles:
                    raise ValueError('Title numbers must be unique positive integers')
                titles[title] = (page, button)
            elif type(button['page']) is not int or not 0 <= button['page'] < len(pages):
                raise ValueError('Page link is out of range')
    if sorted(titles) != list(range(1, len(titles) + 1)):
        raise ValueError('Expected title numbers must be consecutive from 1')
    return pages, titles


def goto_page(player, pages, target):
    player.menu(0)
    current = 0
    while current != target:
        button = next((b for b in pages[current] if b.get('page') == current + 1), None)
        if button is None:
            raise ValueError('Expected map needs forward page links')
        player.activate(button)
        current += 1
        player.menu(current)


def menu_case(player, data, pages, titles, title, method, seek_seconds):
    page, button = titles[title]
    playlist = len(pages) + title - 1
    if not player.disc['top_menu'] or player.disc['num_titles'] != len(titles):
        raise AssertionError('Disc top-menu/title metadata differs from the expected project')
    goto_page(player, pages, page)
    player.activate(button)
    player.title(title, playlist)
    result = {'title': title, 'page': page, 'method': method,
              'initial_sample': player.sample_title(title, playlist)}
    if method == 'popup':
        result['popup_ready'] = player.popup_ready(title, playlist)
    duration = player.duration(playlist)
    seek = min(seek_seconds, duration / 2)
    if seek > 0:
        player.lib.bd_seek_time(player.handle, int(seek * 90000))
        result['after_seek_sample'] = player.sample_title(title, playlist, 128 * 1024)
    if method == 'end':
        player.lib.bd_seek_time(player.handle, int(max(0, duration - 1) * 90000))
        ended = False
        for _ in range(20000):
            _, event = player.read()
            ended |= event == 10
            if player.state.get(30) == 1:
                if not ended:
                    raise AssertionError('Popup auto-action fired before the title reached its end')
                if player.state.get(6) != page:
                    raise AssertionError('End-of-title returned to the wrong menu page')
                result['end_event_observed_before_menu'] = True
                break
        else:
            raise AssertionError('Title did not reach its normal menu post-command')
    else:
        start = len(player.overlay_events)
        value = (player.lib.bd_menu_call(player.handle, -1) if method == 'top'
                 else player.lib.bd_user_input(player.handle, -1, 11))
        result['return_call_result'] = value
        if value < 0 or (method == 'top' and value != 1):
            raise AssertionError(f'{method} menu request rejected ({value})')
        player.menu(page)
        if player.state.get(5) != 0:
            raise AssertionError('Return did not enter the indexed Top Menu title')
        if len(player.overlay_events) <= start:
            raise AssertionError('Menu visibility changed without any overlay callback')
        result['return_overlay_callbacks'] = len(player.overlay_events) - start
    # Confirm the actual remembered focus by pressing Enter without moving it.
    program_buttons = [b for b in pages[page] if 'title' in b]
    first = data.get('first_button', 1)
    default = first if 1 <= first <= len(program_buttons) else 1
    expected_title = title if data.get('remember_selection', True) else program_buttons[default - 1]['title']
    player.activate()
    player.title(expected_title, len(pages) + expected_title - 1)
    result['focus_verified_by_replay_title'] = expected_title
    result['replay_sample'] = player.sample_title(expected_title, len(pages) + expected_title - 1, 64 * 1024)
    return result


def stop_case(player):
    # tsMuxer's native STOP route may advertise a Top Menu index entry whose
    # movie object is not a visible menu. Check actual playback/EOF behavior,
    # not the presence of that internal entry, and do not invoke it here.
    if player.disc['num_titles'] > 1:
        raise AssertionError('No-menu test requires a single video')
    for _ in range(20000):
        count, _ = player.read()
        if player.state.get(30, 0) or player.state.get(29, 0):
            raise AssertionError('No-menu video exposed an interactive menu')
        if count:
            break
    else:
        raise AssertionError('No-menu video never produced stream data')
    if player.state.get(6) != 0:
        raise AssertionError('No-menu video did not select its single playlist')
    title = player.state.get(5)
    result = {'playback_title_id': title, 'disc_index': player.disc,
              'initial_sample': player.sample_title(title, 0)}
    duration = player.duration(0)
    player.lib.bd_seek_time(player.handle, int(max(0, duration - 1) * 90000))
    ended, empty = False, 0
    for _ in range(20000):
        # After EOF, drive pending VM commands/events without reading the same
        # ended clip again; libbluray explicitly supports len=0 event polling.
        count, event = player.read(event_only=ended)
        if player.state.get(30, 0) or player.state.get(29, 0) or event == 6:
            raise AssertionError('No-menu video opened a menu or restarted a playlist')
        ended |= event == 10
        if ended and count == 0 and event == 0:
            empty += 1
            if empty == 30:
                result['end_event_observed'] = True
                result['stopped_empty_reads'] = empty
                return result
            time.sleep(.01)
        elif count or event:
            empty = 0
    raise AssertionError('No-menu video did not remain stopped after its end')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--iso', required=True, type=Path, help='Read-only ISO or BDMV directory')
    parser.add_argument('--library', required=True, type=Path, help='libbluray shared library')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--menu-map', type=Path)
    mode.add_argument('--no-menu', action='store_true')
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--methods', default='top,popup,end', help='Comma-separated top,popup,end menu tests')
    parser.add_argument('--titles', help='Comma-separated title numbers; defaults to all titles')
    parser.add_argument('--seek-seconds', type=float, default=5)
    parser.add_argument('--timeout', type=float, default=30, help='Seconds per navigation case')
    parser.add_argument('--max-read-mib', type=int, default=128)
    args = parser.parse_args()
    iso, library = args.iso.expanduser().resolve(), args.library.expanduser().resolve()
    if not iso.exists() or not library.is_file() or str(iso).startswith('/dev/'):
        parser.error('Provide an existing image/directory and shared library; physical devices are refused')
    if args.timeout <= 0 or args.max_read_mib <= 0 or args.seek_seconds < 0:
        parser.error('Invalid time/read limits')
    report_path = args.report.expanduser().resolve()
    inputs = [iso, library] + ([args.menu_map.resolve()] if args.menu_map else [])
    if report_path in inputs or (iso.is_dir() and iso in report_path.parents):
        parser.error('The report must be separate from the input image, BDMV directory, map and library')
    methods = args.methods.split(',')
    if any(method not in ('top', 'popup', 'end') for method in methods):
        parser.error('Unknown navigation method')
    budget = {'bytes_read': 0, 'limit_bytes': args.max_read_mib * 1024 * 1024}
    report = {'iso': str(iso), 'library': str(library), 'physical_player_verified': False,
              'video_decoded': False, 'read_budget': budget, 'cases': []}
    if args.no_menu:
        cases, data, pages, titles = [(1, 'stop')], None, None, None
    else:
        data = json.loads(args.menu_map.read_text())
        pages, titles = validate_map(data)
        requested = [int(n) for n in args.titles.split(',')] if args.titles else list(titles)
        if not requested or any(n not in titles for n in requested):
            parser.error('Requested title is absent from the expected map')
        cases = [(title, method) for title in requested for method in methods]
    for title, method in cases:
        player, result = None, {'title': title, 'method': method, 'passed': False}
        try:
            player = Player(library, iso, budget, args.timeout)
            result.update(stop_case(player) if args.no_menu else
                          menu_case(player, data, pages, titles, title, method, args.seek_seconds))
            result['passed'] = True
        except Exception as error:
            result['error'] = str(error)
        finally:
            if player:
                result['trace'] = player.trace()
                player.close()
            report['cases'].append(result)
        print(f'title {title} {method}: '+('PASS' if result['passed'] else 'FAIL: '+result['error']), flush=True)
        if budget['bytes_read'] >= budget['limit_bytes']:
            break
    report['passed'] = len(report['cases']) == len(cases) and all(case['passed'] for case in report['cases'])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
