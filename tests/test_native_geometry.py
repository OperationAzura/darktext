"""Regression tests for native framebuffer and automatic text-region detection."""

import unittest
import numpy as np

from darktext.fast_detector import fast_selected_index, fast_signature
from darktext.text_region import detect_text_region


def draw_fake_text_line(frame, x, y, width, height=7, blue=False):
    color = (200, 50, 40) if blue else (230, 230, 230)
    for xx in range(x, x + width, 5):
        frame[y : y + height, xx : min(xx + 3, x + width)] = color


class TestNativeGeometry(unittest.TestCase):
    def test_signature_accepts_native_640x400(self):
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        sig = fast_signature(frame)
        self.assertEqual(sig.shape, (52, 96))

    def test_signature_accepts_native_320x200(self):
        frame = np.zeros((200, 320, 3), dtype=np.uint8)
        sig = fast_signature(frame)
        self.assertEqual(sig.shape, (52, 96))

    def test_selection_regions_are_absolute_native_coordinates(self):
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        frame[100:120, 210:360] = (200, 50, 40)
        regions = [(210, 100, 360, 120), (210, 140, 360, 160)]
        self.assertEqual(fast_selected_index(frame, regions), 0)

    def test_discovers_large_text_block_without_fixed_screen_position(self):
        frame = np.zeros((400, 640, 3), dtype=np.uint8)

        # A narrow stats-like column should lose to the broader dialog block.
        for i in range(16):
            draw_fake_text_line(frame, 20, 40 + i * 14, 70)

        for i, width in enumerate((300, 280, 320, 250, 290, 310)):
            draw_fake_text_line(frame, 210, 80 + i * 16, width)

        x1, y1, x2, y2 = detect_text_region(frame)
        self.assertLessEqual(x1, 210)
        self.assertGreater(x1, 120)
        self.assertLessEqual(y1, 80)
        self.assertGreaterEqual(x2, 500)
        self.assertGreaterEqual(y2, 160)

    def test_discovers_text_block_at_320x200(self):
        frame = np.zeros((200, 320, 3), dtype=np.uint8)

        for i in range(12):
            draw_fake_text_line(frame, 8, 20 + i * 7, 35, height=4)

        for i, width in enumerate((145, 135, 150, 120, 140)):
            draw_fake_text_line(frame, 90, 40 + i * 8, width, height=4)

        x1, y1, x2, y2 = detect_text_region(frame)
        self.assertLessEqual(x1, 90)
        self.assertGreater(x1, 45)
        self.assertLessEqual(y1, 40)
        self.assertGreaterEqual(x2, 225)
        self.assertGreaterEqual(y2, 70)

    def test_detector_falls_back_to_full_frame_when_no_text_is_found(self):
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        self.assertEqual(detect_text_region(frame), (0, 0, 640, 400))


if __name__ == "__main__":
    unittest.main()
