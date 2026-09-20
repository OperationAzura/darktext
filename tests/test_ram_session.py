import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from darktext.ram_session import DialogCache, Recorder
from darktext.ram_text import BUFFER_OFFSET, BUFFER_SIZE
import test_ram_text


def segment(text, state=1):
    data = bytearray(0x10000)
    data[0x8D6] = state
    data[BUFFER_OFFSET:BUFFER_OFFSET + BUFFER_SIZE] = text.ljust(BUFFER_SIZE, b'\0')
    return bytes(data)


class CacheTests(unittest.TestCase):
    def stable(self, cache, data):
        cache.observe(data, 0x20000)
        return cache.observe(data, 0x20000)

    def test_popup_retains_parent_and_buffer_return_restores_once(self):
        cache = DialogCache()
        parent = segment(b'A traveler arrives at the village.\x15Enter the gate.\0')
        initial = self.stable(cache, parent)
        self.assertEqual(initial['kind'], 'dialog_buffer_changed')
        popup = segment(b'\x15\n\x15\x81Name\xff\n\x15Potion\0')
        event = self.stable(cache, popup)
        self.assertEqual(event['kind'], 'auxiliary_buffer')
        self.assertEqual(event['cached_dialog'], initial['dialog'])
        self.assertEqual(event['cache_status'], 'retained_unverified')
        # An unchanged buffer cannot establish that the popup closed.
        self.assertIsNone(cache.observe(popup, 0x20000))
        restored = self.stable(cache, parent)
        self.assertEqual(restored['kind'], 'dialog_buffer_restored')
        self.assertIsNone(cache.observe(parent, 0x20000))
        self.assertIsNone(cache.observe(parent, 0x20000))

    def test_new_dialog_replaces_cache_not_appends(self):
        cache = DialogCache()
        self.stable(cache, segment(b'A traveler arrives at the village.\0'))
        event = self.stable(cache, segment(b'A merchant greets the party outside.\0'))
        self.assertEqual(event['kind'], 'dialog_buffer_changed')
        self.assertEqual(cache.dialog['narrative'], 'A merchant greets the party outside.')

    def test_context_change_and_disconnect_drop_parent(self):
        cache = DialogCache()
        self.stable(cache, segment(b'A traveler arrives at the village.\0'))
        event = cache.observe(segment(b'\x15\n\x15Popup\0', state=2), 0x20000)
        self.assertEqual(event['kind'], 'context_changed')
        self.assertIsNone(cache.dialog)
        self.stable(cache, segment(b'A traveler arrives at the village.\0'))
        cache.reset()
        self.assertIsNone(cache.dialog)

    def test_unstable_narrative_never_replaces_cache(self):
        cache = DialogCache()
        self.stable(cache, segment(b'A traveler arrives at the village.\0'))
        cache.observe(segment(b'A partially constructed different sentence.\0'), 0x20000)
        cache.observe(segment(b'\x15\n\x15Popup\0'), 0x20000)
        self.assertEqual(cache.dialog['narrative'], 'A traveler arrives at the village.')


class RecoveryTests(unittest.TestCase):
    def test_transport_failure_forces_discovery_and_relocation(self):
        helper = test_ram_text.RamTextTests()
        reader = helper.make_reader()
        reader.base = 0x20000
        with patch('darktext.ram_text.memory', side_effect=OSError('connection lost')):
            with self.assertRaises(OSError):
                reader.read()
        self.assertIsNone(reader.base)
        image = helper.image(reader, 0x40000)
        with patch('darktext.ram_text.memory', side_effect=[image, image[0x40000:0x50000]]):
            self.assertTrue(reader.read().narrative)
        self.assertEqual(reader.base, 0x40000)

    def test_decode_failure_keeps_valid_address(self):
        helper = test_ram_text.RamTextTests()
        reader = helper.make_reader()
        reader.base = 0x20000
        image = helper.image(reader, reader.base)
        image[reader.base + BUFFER_OFFSET] = 0
        with patch('darktext.ram_text.memory', return_value=image[0x20000:0x30000]):
            with self.assertRaises(ValueError):
                reader.read()
        self.assertEqual(reader.base, 0x20000)


class RecorderTests(unittest.TestCase):
    def test_capture_failure_preserves_ram_and_flushes_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session'
            recorder = Recorder(path, 'http://localhost:1', 1)
            with patch('darktext.ram_session.urlopen', side_effect=OSError('no video')):
                recorder.sample(segment(b'A traveler arrives at the village.\0'), 0x20000, None)
            recorder.close()
            events = [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()]
            snapshot = next(e for e in events if e['kind'] == 'snapshot')
            self.assertEqual(snapshot['frame_error'], 'no video')
            self.assertTrue((path / snapshot['ram']).is_file())
            self.assertEqual(events[-1]['kind'], 'session_end')
            with self.assertRaises(FileExistsError):
                Recorder(path, 'http://localhost:1', 1)

    def test_storage_budget_stops_growth(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session'
            recorder = Recorder(path, 'http://localhost:1', 1)
            self.assertIsNone(recorder.artifact('too-large', bytes(1024 * 1024)))
            self.assertTrue(recorder.full)
            recorder.write({'ignored': 'x' * 2000})
            recorder.close()
            self.assertLess(sum(p.stat().st_size for p in path.iterdir()), 1024 * 1024)

class TransitionRegressionTests(unittest.TestCase):
    def test_state_change_does_not_repromote_stale_buffer(self):
        cache = DialogCache()
        original = segment(b'A traveler arrives at the village.\0')
        cache.observe(original, 0x20000)
        cache.observe(original, 0x20000)
        changed = segment(b'A traveler arrives at the village.\0', state=2)
        self.assertEqual(cache.observe(changed, 0x20000)['kind'], 'context_changed')
        self.assertIsNone(cache.observe(changed, 0x20000))
        self.assertIsNone(cache.dialog)
        fresh = segment(b'A merchant greets the party outside.\0', state=2)
        cache.observe(fresh, 0x20000)
        self.assertEqual(cache.observe(fresh, 0x20000)['kind'], 'dialog_buffer_changed')

    def test_simultaneous_new_state_and_new_text_are_accepted(self):
        cache = DialogCache()
        original = segment(b'A traveler arrives at the village.\0')
        cache.observe(original, 0x20000)
        cache.observe(original, 0x20000)
        fresh = segment(b'A merchant greets the party outside.\0', state=2)
        self.assertEqual(cache.observe(fresh, 0x20000)['kind'], 'context_changed')
        self.assertEqual(cache.observe(fresh, 0x20000)['kind'], 'dialog_buffer_changed')
