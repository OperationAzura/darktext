import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from darktext.ram_selection import SelectionTracker, formula_rows, selected_option
from darktext.ram_session import DialogCache, watch
from darktext.ram_text import BUFFER_OFFSET, BUFFER_SIZE

BASE = 0x20000
PARENT = b'A visitor considers the available choices.\x15First choice\x15Hidden choice\x15Leave now\0'
POPUP = b'\x14'.join(b'\x81' + name + b'\xff\n' + b''.join(
    b'\x15 \x80Formula ' + str(i).encode() + b'\n' for i in range(4))
    for name in [b'Alex', b'Blair', b'Casey', b'Drew']) + b'\0'


def segment(raw=PARENT, owner=0x1A, row=0, popup=False, mode=None):
    d = bytearray(65536)
    d[0xA88D:0xA88F] = owner.to_bytes(2, 'little')
    d[BUFFER_OFFSET:BUFFER_OFFSET+BUFFER_SIZE] = raw.ljust(BUFFER_SIZE, b'\0')
    d[0xE96E] = 16 if popup else 2
    d[0xE96F] = row
    d[0xE9B0:0xE9B0+(16 if popup else 2)] = bytes(range(16)) if popup else bytes([0, 2])
    d[0xE99C:0xE9B0] = bytes([1]*20)
    if popup:
        d[0xEE41] = 1
        d[0xA776:0xA778] = b'\x34\x12'
    d[0xEE42] = int(popup) if mode is None else mode
    return d


def cache_for(d):
    cache = DialogCache()
    cache.observe(d, BASE)
    cache.observe(d, BASE)
    return cache


class SelectionTests(unittest.TestCase):
    def test_hidden_owner_ordinals_are_not_compacted_text_indices(self):
        d = segment(row=1)
        result = selected_option(d, cache_for(d), BASE)
        self.assertEqual(result['raw_row'], 2)
        self.assertEqual(result['text'], 'Leave now')

    def test_empty_option_slots_do_not_shift_owner_ordinals(self):
        d = segment(b'A visitor considers the available choices.\x15First choice\x15\x15Leave now\0', row=1)
        self.assertEqual(selected_option(d, cache_for(d), BASE)['text'], 'Leave now')

    def test_repeated_formula_rows_keep_all_character_slots(self):
        for slot in range(4):
            d = segment(POPUP, owner=0x59, row=slot*4+2, popup=True)
            result = selected_option(d, cache_for(d), BASE)
            self.assertEqual(result['character_slot'], slot)
            self.assertEqual(result['formula_slot'], 2)
            self.assertEqual(result['speech_text'], ['Alex', 'Blair', 'Casey', 'Drew'][slot] + ': Formula 2')

    def test_popup_visibility_does_not_imply_popup_input_mode(self):
        parent = segment(owner=0x59)
        cache = cache_for(parent)
        d = segment(POPUP, owner=0x59, popup=True, mode=0)
        # Active table remains the primary table while pointer is on its owner.
        d[0xE96E] = 2
        d[0xE9B0:0xE9B2] = bytes([0, 2])
        cache.observe(d, BASE); cache.observe(d, BASE)
        self.assertEqual(selected_option(d, cache, BASE)['source'], 'dialog')

    def test_invalid_or_unsupported_selection_is_silent(self):
        for mutation in [lambda d: d.__setitem__(0xE96F,255),
                         lambda d: d.__setitem__(0xE96E,21),
                         lambda d: d.__setitem__(0xE9B0,99),
                         lambda d: d.__setitem__(0xE99C,0),
                         lambda d: d.__setitem__(0xA88D,0x36)]:
            d=segment(); mutation(d)
            self.assertIsNone(selected_option(d,cache_for(d),BASE))

    def test_unstable_or_unsupported_buffer_cannot_speak_old_parent(self):
        d=segment();cache=cache_for(d)
        changed=segment(b'Another narrative with different choices.\x15New choice\0')
        cache.observe(changed,BASE)
        self.assertIsNone(selected_option(changed,cache,BASE))
        unsupported=segment(b'No risk.\0')
        cache.observe(unsupported,BASE);cache.observe(unsupported,BASE)
        self.assertIsNone(selected_option(unsupported,cache,BASE))

    def test_debounce_cancels_and_revisiting_same_row_speaks_again(self):
        d=segment();cache=cache_for(d);tracker=SelectionTracker()
        self.assertEqual(tracker.observe(d,cache,BASE)['kind'],'selection_cleared')
        self.assertEqual(tracker.observe(d,cache,BASE)['kind'],'selection_changed')
        self.assertIsNone(tracker.observe(d,cache,BASE))
        away=segment(row=255)
        self.assertEqual(tracker.observe(away,cache,BASE)['kind'],'selection_cleared')
        tracker.observe(d,cache,BASE)
        self.assertEqual(tracker.observe(d,cache,BASE)['kind'],'selection_changed')

    def test_popup_parser_rejects_incomplete_or_unknown_controls(self):
        for raw in [POPUP[:-1],POPUP.replace(b'\x80',b'\x90'),POPUP.replace(b'\x15 \x80Formula 3\n',b'')]:
            with self.assertRaises(ValueError):formula_rows(raw)

    def test_watch_speaks_and_stops_on_connection_loss(self):
        d=segment();reader=Mock(api='http://localhost:8086',base=BASE)
        reader.read_segment.side_effect=[d,d,d,d,OSError('gone'),KeyboardInterrupt()]
        speaker=Mock();speaker.speak.return_value=True
        with patch('darktext.speaker.DirectLiveSpeaker',return_value=speaker), \
             patch('darktext.speaker.get_voice_for_narrative',return_value=object()), \
             patch('darktext.ram_session.time.sleep'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):watch(reader,speak_selection=True)
        speaker.speak.assert_called_once_with('First choice')
        self.assertGreaterEqual(speaker.stop.call_count,2)


class SaintsSelectionTests(unittest.TestCase):
    def test_learning_saints_mapping_preserves_character_and_saint_slot(self):
        raw = POPUP.replace(b'Formula ', b'S.Example ')
        d = segment(raw, owner=0x36, row=5, popup=True)
        result = selected_option(d, cache_for(d), BASE)
        self.assertEqual(result['source'], 'saint_popup')
        self.assertEqual(result['character_slot'], 1)
        self.assertEqual(result['saint_slot'], 1)
        self.assertEqual(result['text'], 'S.Example 1')
        self.assertEqual(result['speech_text'], 'Blair: Saint Example 1')
        self.assertNotIn('formula_slot', result)

    def test_learning_saints_primary_menu_remains_unsupported(self):
        d = segment(owner=0x36)
        self.assertIsNone(selected_option(d, cache_for(d), BASE))

    def test_time_passage_buffer_cannot_be_read_as_a_saint(self):
        d = segment(b'No risk.\0', owner=0x36, row=5, popup=True)
        self.assertIsNone(selected_option(d, cache_for(d), BASE))
