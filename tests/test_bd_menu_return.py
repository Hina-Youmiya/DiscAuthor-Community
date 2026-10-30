"""Binary regressions for Top Menu dispatch and title popup authoring.

These tests decode the authored bytes independently. Real libbluray navigation
is also required for release: menu -> title media -> POPUP/ROOT_MENU -> menu,
including a later page, repeat entry, seeking, and no-menu playback.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bd_ig
import bluray


def number(data, offset, size=2):
    return int.from_bytes(data[offset:offset + size], 'big')


def sup_segments(data):
    result = []
    offset = 0
    while offset < len(data):
        if data[offset:offset + 2] != b'PG':
            raise AssertionError('Invalid SUP segment signature')
        length = number(data, offset + 11)
        end = offset + 13 + length
        if end > len(data):
            raise AssertionError('Truncated SUP segment')
        result.append((data[offset + 10], data[offset + 13:end]))
        offset = end
    return result


def transport(segments):
    elementary = b''.join(bytes([kind]) + len(raw).to_bytes(2, 'big') + raw
                          for kind, raw in segments)
    packet = b'\0\0\1\xbd' + (len(elementary) + 3).to_bytes(2, 'big') + b'\x80\0\0' + elementary
    result = bytearray()
    for index, offset in enumerate(range(0, len(packet), 184)):
        header = bytes([0x47, 0x14 | (0x40 if index == 0 else 0), 0, 0x10 | (index % 16)])
        result += bytes(4) + header + packet[offset:offset + 184].ljust(184, b'\xff')
    return bytes(result)


def movie_objects(data):
    result = []
    offset = 50
    for _ in range(number(data, 48)):
        count = number(data, offset + 2)
        result.append({'flags': number(data, offset),
                       'commands': [data[offset + 4 + i * 12:offset + 16 + i * 12]
                                    for i in range(count)]})
        offset += 4 + count * 12
    if offset != len(data):
        raise AssertionError('MovieObject size mismatch')
    return result


class BDMenuReturnTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='discauthor-bd-return-')
        self.root = Path(self.directory.name)
        (self.root / 'BDMV/BACKUP').mkdir(parents=True)

    def tearDown(self):
        self.directory.cleanup()

    def popup(self, page=0):
        path = self.root / 'return.sup'
        bluray.write_return_igs(path, page, frame_rate_code=4)
        return sup_segments(path.read_bytes())

    def test_popup_has_default_auto_action_to_indexed_top_menu(self):
        compositions = bd_ig.inspect_multiplexed_igs(transport(self.popup(1)), expected_ui_model=1)
        self.assertEqual(len(compositions), 1)
        ic = compositions[0]
        self.assertEqual((ic['stream_model'], ic['ui_model'], ic['frame_rate_code']), (0, 1, 4))
        self.assertEqual((ic['composition_timeout_pts'], ic['selection_timeout_pts'], ic['user_timeout_duration']), (0, 0, 0))
        self.assertEqual(ic['page_count'], 1)
        page = ic['pages'][0]
        self.assertEqual(page['id'], 0)
        self.assertEqual(page['uo_mask'], '0000000000000000')
        self.assertEqual(page['default_selected_button_id_ref'], 1)
        self.assertEqual(page['default_activated_button_id_ref'], 0xffff)
        self.assertEqual(len(page['bogs']), 1)
        bog = page['bogs'][0]
        self.assertEqual(bog['default_valid_button_id_ref'], 1)
        self.assertEqual(len(bog['buttons']), 1)
        button = bog['buttons'][0]
        self.assertEqual(button['id'], 1)
        self.assertTrue(button['auto_action'])
        # One-operand BRANCH/JUMP, immediate operand, JumpTitle option1, title0.
        self.assertEqual(button['commands'], ['218100000000000000000000'])

    def test_all_popup_button_states_are_transparent_and_silent(self):
        sections = dict(self.popup())
        self.assertEqual(sections[0x14], b'\0\0\0\x10\x80\x80\0')
        ods = sections[0x15]
        self.assertEqual((number(ods, 7), number(ods, 9)), (2, 2))
        self.assertEqual(ods[11:], b'\0\x02\0\0\0\x02\0\0')
        raw = sections[0x18]
        # ICS27 + page21 + BOG3 = first button byte offset51.
        button = raw[51:86]
        self.assertEqual((button[22], button[28]), (0xff, 0xff))
        self.assertEqual([number(button, i) for i in (17, 19, 23, 25, 29, 31)], [0] * 6)
        self.assertEqual((button[21], button[27]), (0, 0))

    def test_popup_display_set_is_scheduled_at_clip_start(self):
        self.popup()
        data = (self.root / 'return.sup').read_bytes()
        offset = 0
        count = 0
        while offset < len(data):
            self.assertEqual(number(data, offset + 2, 4), 0)
            self.assertEqual(number(data, offset + 6, 4), 0)
            offset += 13 + number(data, offset + 11)
            count += 1
        self.assertEqual(count, 4)

    def test_validator_distinguishes_main_menu_popup_and_stream_model(self):
        sections = self.popup()
        with self.assertRaisesRegex(ValueError, 'user-interface'):
            bd_ig.inspect_multiplexed_igs(transport(sections))
        self.assertEqual(bd_ig.inspect_multiplexed_igs(transport(sections), expected_ui_model=None)[0]['ui_model'], 1)
        raw = bytearray(sections[0][1]); raw[12] |= 0x80
        with self.assertRaisesRegex(ValueError, 'multiplexed'):
            bd_ig.inspect_multiplexed_igs(transport([(0x18, raw)]), expected_ui_model=None)
        raw = bytearray(sections[0][1]); raw[-1:] = b''; raw[9:12] = (len(raw) - 12).to_bytes(3, 'big')
        with self.assertRaisesRegex(ValueError, 'truncated'):
            bd_ig.inspect_multiplexed_igs(transport([(0x18, raw)]), expected_ui_model=1)

    def test_existing_main_menu_remains_always_on_and_explicitly_activated(self):
        path = self.root / 'menu.sup'
        bluray.write_igs(path, [{'rect': [20, 20, 40, 40], 'title': 1}])
        self.assertEqual(number(path.read_bytes(), 2, 4), 45000)
        ic = bd_ig.inspect_multiplexed_igs(transport(sup_segments(path.read_bytes())))[0]
        self.assertEqual(ic['ui_model'], 0)
        button = ic['pages'][0]['bogs'][0]['buttons'][0]
        self.assertFalse(button['auto_action'])
        self.assertEqual(button['commands'], ['218100000000000100000000'])

    def test_top_menu_dispatcher_preserves_title_mapping_page_and_selection(self):
        bluray.navigation(self.root, 2, 6, 4, {'first_button': 2}, first_play_mode='1080p24')
        index = (self.root / 'BDMV/index.bdmv').read_bytes()
        objects = movie_objects((self.root / 'BDMV/MovieObject.bdmv').read_bytes())
        self.assertEqual(number(index, 100), 9)
        self.assertEqual([number(index, 114 + i * 12) for i in range(6)], list(range(3, 9)))
        self.assertEqual(len(objects), 10)
        self.assertTrue(all(obj['flags'] == 0 for obj in objects))
        self.assertEqual(objects[9]['commands'], [bytes.fromhex('210000000000000100000000')])
        self.assertEqual(objects[0]['commands'], [bytes.fromhex('504000010000000000000002'),
                                                bytes.fromhex('504000010000000100000001'),
                                                bytes.fromhex('218100000000000000000000')])
        # Every title records its originating page and slot before PlayPL.
        for i in range(6):
            commands = objects[3 + i]['commands']
            self.assertEqual(commands[0], b'\x50\x40\0\1' + (1).to_bytes(4, 'big') + (1 + i // 4).to_bytes(4, 'big'))
            self.assertEqual(number(commands[1], 8, 4), 1 + i % 4)
            self.assertEqual(commands[-1], bytes.fromhex('218100000000000000000000'))
        for i in range(2):
            self.assertEqual(number(objects[1 + i]['commands'][0], 8, 4), i + 1)
        for name in ('index.bdmv', 'MovieObject.bdmv'):
            self.assertEqual((self.root / 'BDMV' / name).read_bytes(), (self.root / 'BDMV/BACKUP' / name).read_bytes())

    def test_no_menu_stays_without_top_menu_and_stops(self):
        bluray.navigation(self.root, 0, 1, config={'menu_enabled': False, 'no_menu_end_action': 'stop'}, first_play_mode='1080i60')
        index = (self.root / 'BDMV/index.bdmv').read_bytes()
        objects = movie_objects((self.root / 'BDMV/MovieObject.bdmv').read_bytes())
        self.assertEqual(number(index, 100), 0xffff)
        self.assertEqual(len(objects), 2)
        self.assertTrue(all(obj['flags'] == 0x4000 for obj in objects))
        self.assertEqual(objects[1]['commands'], [bytes.fromhex('51c000018001000080010000'),
                                                bytes.fromhex('228000000000000000000000')])


if __name__ == '__main__':
    unittest.main()
