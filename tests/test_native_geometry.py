"""Regression tests for DarkText's native-framebuffer coordinate path."""

import unittest
import numpy as np

from darktext.geometry import FrameGeometry
from darktext.fast_detector import fast_selected_index, fast_signature


class TestNativeGeometry(unittest.TestCase):
    def test_640x400_story_rect_uses_native_rows(self):
        geom = FrameGeometry(640, 400)
        self.assertEqual(geom.text_rect, (135, 38, 625, 379))

    def test_320x200_story_rect_scales_without_intermediate_frame(self):
        geom = FrameGeometry(320, 200)
        self.assertEqual(geom.text_rect, (68, 19, 312, 190))

    def test_signature_accepts_native_640x400(self):
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        sig = fast_signature(frame)
        self.assertEqual(sig.shape, (52, 96))

    def test_signature_accepts_native_320x200(self):
        frame = np.zeros((200, 320, 3), dtype=np.uint8)
        sig = fast_signature(frame)
        self.assertEqual(sig.shape, (52, 96))

    def test_selection_regions_are_story_local_native_coordinates(self):
        frame = np.zeros((400, 640, 3), dtype=np.uint8)
        geom = FrameGeometry.from_frame(frame)
        story_x1, story_y1, _, _ = geom.text_rect

        # Native story-local region (15, 13)-(165, 30), placed directly in
        # the native framebuffer. No 640x480 intermediate coordinates exist.
        frame[story_y1 + 13 : story_y1 + 30, story_x1 + 15 : story_x1 + 165] = (
            200,
            50,
            40,
        )
        regions = [(15, 13, 165, 30), (15, 38, 165, 55)]
        self.assertEqual(fast_selected_index(frame, regions), 0)


if __name__ == "__main__":
    unittest.main()
