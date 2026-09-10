from pathlib import Path
"""Comprehensive unit and regression tests for darktext."""

import hashlib
import os
import shutil
import tempfile
import time
import unittest
import numpy as np

from darktext.config import TEXT_RECT, SAMPLE_RATE
from darktext.models import Line, ScreenState
from darktext.ocr import clean_text, equivalent
from darktext.fast_detector import (
    fast_blue_score,
    fast_change_ratio,
    fast_selected_index,
    fast_signature,
)
from darktext.temporal import (
    TemporalOptionBuilder,
    _temporal_best_sample,
    _temporal_ngram_set,
    _temporal_norm_tokens,
    _temporal_similarity,
    _temporal_tokens,
)
from darktext.audio_cache import (
    OptionAudioCache,
    normalize_audio_text,
    text_content_hash,
)
from darktext.speaker import (
    CachedWavPlayer,
    get_available_voices,
    get_voice_for_narrative,
    get_voice_for_option,
)


class TestModelsAndOCR(unittest.TestCase):
    def test_line_and_screen_state(self):
        l1 = Line("Option one", 10, 20, 100, 35, 0.95, True, option_start=True)
        l2 = Line("Option two", 10, 40, 100, 55, 0.92, False, option_start=True)
        state = ScreenState("You are at the city gate.", ["Option one", "Option two"], 1, [l1, l2])

        self.assertIn("You are at the city gate.", state.content_text())
        self.assertIn("1:Option one", state.content_text())
        self.assertIn("You are at the city gate.", state.speech_text())
        self.assertIn("Option one", state.speech_text())

    def test_clean_text(self):
        self.assertEqual(clean_text("… Hello | world …"), "... Hello I world ...")

    def test_equivalent(self):
        s1 = ScreenState("Welcome to the inn.", ["Rest", "Leave"], 1, [])
        s2 = ScreenState("Welcome to  the  inn.", ["Rest", "Leave"], 2, [])
        s3 = ScreenState("A dark hallway.", ["Enter", "Leave"], 1, [])

        self.assertTrue(equivalent(s1, s2))
        self.assertFalse(equivalent(s1, s3))


class TestFastDetector(unittest.TestCase):
    def test_fast_signature_and_change_ratio(self):
        frame1 = np.zeros((480, 640, 3), dtype=np.uint8)
        frame2 = np.zeros((480, 640, 3), dtype=np.uint8)
        sig1 = fast_signature(frame1)
        sig2 = fast_signature(frame2)
        self.assertEqual(fast_change_ratio(sig1, sig2), 0.0)

        # Introduce change in story rect
        x1, y1, x2, y2 = TEXT_RECT
        frame2[y1:y2, x1:x2] = 255
        sig3 = fast_signature(frame2)
        self.assertGreater(fast_change_ratio(sig1, sig3), 0.5)

    def test_fast_blue_score_and_selection(self):
        # Create image with blue text
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        # Saturated blue in BGR is (high, low, low)
        img[60:80, 150:300] = (200, 50, 40)
        score, count = fast_blue_score(img[60:80, 150:300])
        self.assertGreater(score, 0.5)
        self.assertGreater(count, 10)

        # Fast hover regions use absolute native framebuffer coordinates.
        regions = [(150, 60, 300, 80), (150, 90, 300, 110)]
        selected = fast_selected_index(img, regions)
        self.assertEqual(selected, 0)


class TestTemporalBuilder(unittest.TestCase):
    def test_token_and_ngram_helpers(self):
        tokens = _temporal_tokens("Exit to the main street.")
        self.assertEqual(tokens, ["Exit", "to", "the", "main", "street", "."])
        norm = _temporal_norm_tokens("Exit to the main street.")
        self.assertEqual(norm, ["exit", "to", "the", "main", "street"])
        ngrams = _temporal_ngram_set("Exit to the main street.", n=2)
        self.assertIn(("exit", "to"), ngrams)
        self.assertIn(("to", "the"), ngrams)

    def test_temporal_similarity(self):
        s1 = "Exit to the main street."
        s2 = "Exit to the main street"
        s3 = "Buy shields and weapons."
        self.assertGreater(_temporal_similarity(s1, s2), 0.9)
        self.assertLess(_temporal_similarity(s1, s3), 0.3)

    def test_best_sample_repair(self):
        samples = [
            {"text": "Exit to the main str...", "ocr": 0.60, "when": 1.0},
            {"text": "Exit to the main street.", "ocr": 0.95, "when": 2.0},
        ]
        best = _temporal_best_sample(samples)
        self.assertEqual(best, "Exit to the main street.")

    def test_temporal_option_builder_multiframe(self):
        builder = TemporalOptionBuilder()
        # Frame 1: partly obscured by cursor
        l1 = Line("... Exit to the main st", 150, 60, 350, 75, 0.70, True, option_start=True)
        l2 = Line("... Talk to the innkeeper", 150, 90, 350, 105, 0.90, False, option_start=True)
        s1 = ScreenState("", ["Exit to the main st", "Talk to the innkeeper"], 1, [l1, l2])
        builder.update(s1)

        self.assertEqual(len(builder.slots), 2)
        self.assertEqual(builder.texts()[0], "Exit to the main st")

        # Frame 2: cursor moved away, full text visible
        l1_clean = Line("... Exit to the main street.", 150, 60, 380, 75, 0.95, False, option_start=True)
        s2 = ScreenState("", ["Exit to the main street.", "Talk to the innkeeper"], None, [l1_clean, l2])
        builder.update(s2)

        # Should have repaired option 1 using n-gram consensus
        self.assertEqual(builder.texts()[0], "Exit to the main street.")
        self.assertEqual(builder.texts()[1], "Talk to the innkeeper")



    def test_main_street_10_options_separation(self):
        builder = TemporalOptionBuilder()
        # Simulate 10 options on Main Street (y spaced by 16px from y=120 to y=264)
        raw_lines = [
            Line("... the Altmarkt, commercial hub of the city.", 20, 120, 350, 133, 0.95, False, option_start=True),
            # Line 2 had dots missed by OCR:
            Line("the Duisburch, a great fortress overlooking the", 20, 136, 360, 149, 0.93, False, option_start=False),
            Line("city.", 30, 152, 70, 165, 0.90, False, option_start=False),
            Line("... the tall spires of the great churches.", 20, 168, 320, 181, 0.95, False, option_start=True),
            Line("the craft guilds and other, darker, side alleys.", 20, 184, 370, 197, 0.94, False, option_start=False),
            Line("... the Haus Friedrichs, a well-known inn with stables.", 20, 200, 390, 213, 0.96, False, option_start=True),
            Line("the wharves and docks.", 20, 216, 200, 229, 0.92, False, option_start=False),
            Line("... the city walls.", 20, 232, 160, 245, 0.95, False, option_start=True),
            Line("a scenic grove where you can wait and relax.", 20, 248, 340, 261, 0.93, False, option_start=False),
            Line("... one of the main gates leading out of the city.", 20, 264, 380, 277, 0.94, False, option_start=True),
        ]
        state = ScreenState("", ["Simulated"], None, raw_lines)
        builder.update(state)
        texts = builder.texts()

        # Must have at least 8 distinct options, NOT 1 or 2 merged giant blobs!
        self.assertGreaterEqual(len(texts), 8)
        # Altmarkt must NOT be merged with churches or Friedrichs
        self.assertNotIn("great churches", texts[0])
        self.assertNotIn("Haus Friedrichs", texts[0])


class TestAudioCache(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="darktext_test_cache_"))
        self.cache = OptionAudioCache(cache_dir=self.test_dir)

    def tearDown(self):
        self.cache.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_normalization_and_hashing(self):
        t1 = "... Attack the bandits."
        t2 = "Attack   the   bandits. "
        t3 = "• Attack the bandits."
        self.assertEqual(normalize_audio_text(t1), "attack the bandits")
        self.assertEqual(normalize_audio_text(t1), normalize_audio_text(t2))
        self.assertEqual(normalize_audio_text(t1), normalize_audio_text(t3))
        self.assertEqual(text_content_hash(t1), text_content_hash(t2))

    def test_cache_save_and_retrieve(self):
        text = "Rent a room for the night."
        # Generate dummy 1 second of 22050Hz 16-bit mono PCM (44100 bytes)
        dummy_pcm = b"\x00\x00" * 22050
        saved_path = self.cache.save_pcm(text, "en_GB-alba-medium", dummy_pcm)
        self.assertIsNotNone(saved_path)
        self.assertTrue(saved_path.is_file())

        # Exact voice match
        hit = self.cache.get_cached_wav(text, "en_GB-alba-medium")
        self.assertEqual(hit, saved_path)

        # Fallback to any voice for this text
        fallback_hit = self.cache.get_cached_wav(text, "en_GB-cori-medium")
        self.assertEqual(fallback_hit, saved_path)

        # Miss on unknown text
        miss = self.cache.get_cached_wav("Unknown option text", "en_GB-alba-medium")
        self.assertIsNone(miss)

    def test_normalized_cache_matching(self):
        # Save an option with dots and period
        text_with_dots = "... Exit to the main street."
        dummy_pcm = b"\x00\x00" * 22050
        saved_path = self.cache.save_pcm(text_with_dots, "en_GB-alba-medium", dummy_pcm)
        self.assertIsNotNone(saved_path)

        # Lookup with clean text without dots or period
        clean_text = "Exit to the main street"
        hit, info, score = self.cache.get_cached_wav_with_info(clean_text, "en_GB-alba-medium")
        self.assertIsNotNone(hit)
        self.assertEqual(hit, saved_path)
        self.assertEqual(info, "exact")

        # Distinct option must NEVER hit
        different_opt = "Exit to the side street"
        hit_diff, _, _ = self.cache.get_cached_wav_with_info(different_opt, "en_GB-alba-medium")
        self.assertIsNone(hit_diff, "Different options must never collide!")

    def test_cache_persists_across_instances(self):
        text = "Check party inventory."
        dummy_pcm = b"\x00\x00" * 22050
        self.cache.save_pcm(text, "en_GB-alba-medium", dummy_pcm)
        self.cache.stop()

        # Create new cache instance pointing to same directory
        cache2 = OptionAudioCache(cache_dir=self.test_dir)
        hit = cache2.get_cached_wav(text, "en_GB-alba-medium")
        self.assertIsNotNone(hit)
        self.assertTrue(hit.is_file())
        cache2.stop()


    @unittest.skipUnless(
        os.environ.get("DARKTEXT_RUN_AUDIO_TESTS") == "1",
        "requires Piper, voice models, and a working audio device",
    )
    def test_live_synthesis_and_streaming(self):
        from darktext.speaker import DirectLiveSpeaker
        speaker = DirectLiveSpeaker(audio_cache=self.cache)
        v0 = get_voice_for_option(0)
        text = "Order a pitcher of ale."

        # Live playback with caching
        ok = speaker.speak_option(text, v0, cache_on_finish=True)
        self.assertTrue(ok)

        # Wait until playback finishes
        while speaker.is_alive():
            time.sleep(0.05)

        # Give tee thread a moment to finalize WAV write
        time.sleep(0.2)

        # Should now be in cache!
        cached = self.cache.get_cached_wav(text, v0.stem)
        self.assertIsNotNone(cached, "Expected live speech to be cached!")
        self.assertTrue(cached.is_file())
        self.assertGreater(cached.stat().st_size, 1000)
        speaker.stop()



    def test_paragraph_rejected_from_option_queue(self):
        long_paragraph = (
            "Uldalrich takes a few hours to discuss healing treatments, trying to determine "
            "his skill. you ask his aid in healing wounds. you beg him to allow you to be "
            "his students, to improve your healing skills. Otto tries to interest him in "
            "buying and selling alchemical components."
        )
        self.cache.queue_options([long_paragraph], lambda idx: get_voice_for_option(0))
        # Queue should be empty because long paragraph was rejected!
        self.assertEqual(self.cache.queue.qsize(), 0)


class TestSpeakerAndVoices(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("DARKTEXT_RUN_AUDIO_TESTS") == "1",
        "requires locally installed Piper voice models",
    )
    def test_voices_available(self):
        voices = get_available_voices()
        self.assertGreater(len(voices), 0, "Expected Piper ONNX voices in voices directory")
        narrative_voice = get_voice_for_narrative()
        self.assertIsNotNone(narrative_voice)
        v0 = get_voice_for_option(0)
        v1 = get_voice_for_option(1)
        self.assertIsNotNone(v0)
        self.assertIsNotNone(v1)


if __name__ == "__main__":
    unittest.main()
