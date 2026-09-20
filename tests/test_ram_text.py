"""Synthetic fixtures only: no game text or memory dumps are distributed."""
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

from darktext.ram_text import (
    BUFFER_OFFSET, POINTER_OFFSET, SIGNATURE_DS_OFFSET, SIGNATURE_SIZE,
    RamReader, decode_buffer,
)


class RamTextTests(unittest.TestCase):
    def test_resolved_narrative_and_unverified_options(self):
        result = decode_buffer(b'A traveler arrives at the village.\n\x14Choose:\x15...\x1dEnter.\x16...\x1dPray.\0old')
        self.assertEqual(result.narrative, 'A traveler arrives at the village. Choose:')
        self.assertEqual(result.candidate_options, ['... Enter.', '... Pray.'])
        self.assertEqual(result.visibility, 'unverified')
        self.assertIsNone(result.selection)

    def test_rejects_unsupported_and_incomplete_streams(self):
        for data in [b'', b'A narrative without a terminator', b'\0',
                     b'A traveler meets $ChosenOneName.\0',
                     b'A narrative followed by \x81popup text\0',
                     b'\x15\n\x15A menu without narrative\0']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_buffer(data)

    def make_reader(self):
        reader = object.__new__(RamReader)
        reader.signature = bytes(range(SIGNATURE_SIZE))
        reader.api = 'http://localhost:8086'
        reader.base = None
        return reader

    def image(self, reader, base):
        data = bytearray(0xA0000)
        data[base + SIGNATURE_DS_OFFSET:base + SIGNATURE_DS_OFFSET + SIGNATURE_SIZE] = reader.signature
        struct.pack_into('<HH', data, base + POINTER_OFFSET, BUFFER_OFFSET, base >> 4)
        text = b'A traveler arrives at the village.\0'
        data[base + BUFFER_OFFSET:base + BUFFER_OFFSET + len(text)] = text
        return data

    def test_locates_relocated_data_without_cpu_ds(self):
        reader = self.make_reader()
        for base in [0x20000, 0x34000]:
            image = self.image(reader, base)
            self.assertEqual(reader.locate(image), base)
            with patch('darktext.ram_text.memory', side_effect=[image, image[base:base + 0x10000]]):
                reader.base = None
                self.assertEqual(reader.read().narrative, 'A traveler arrives at the village.')

    def test_rejects_missing_ambiguous_and_wrong_pointer(self):
        reader = self.make_reader()
        data = self.image(reader, 0x20000)
        struct.pack_into('<H', data, 0x20000 + POINTER_OFFSET, 1)
        with self.assertRaises(ValueError):
            reader.locate(data)
        first = self.image(reader, 0x20000)
        second = self.image(reader, 0x40000)
        first[0x40000:0x50000] = second[0x40000:0x50000]
        with self.assertRaises(ValueError):
            reader.locate(first)

    def test_profile_invalidated_after_game_exit(self):
        reader = self.make_reader()
        reader.base = 0x20000
        with patch('darktext.ram_text.memory', return_value=bytes(0x10000)):
            with self.assertRaises(ValueError):
                reader.read()
        self.assertIsNone(reader.base)

    def test_import_does_not_load_ocr(self):
        subprocess.run([sys.executable, '-c',
            'import darktext.ram_text, sys; '
            'assert not any(x in sys.modules for x in ("rapidocr", "cv2", "onnxruntime", "darktext.ocr"))'], check=True)


if __name__ == '__main__':
    unittest.main()
