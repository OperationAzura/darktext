"""Concurrency, audio-format, and changing-screen regressions."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from darktext import daemon
from darktext.audio_cache import OptionAudioCache
from darktext.fast_detector import FastOcrWorker, HoverDebouncer, fast_signature
from darktext.models import Line, ScreenState
from darktext.speaker import DirectLiveSpeaker
from darktext.temporal import TemporalOptionBuilder
from darktext.voice import voice_sample_rate


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for background operation")
        time.sleep(0.01)


def menu(*texts, spacing=12):
    lines = [Line("... " + text, 20, 80 + i * spacing, 300,
                  90 + i * spacing, .95, False, True)
             for i, text in enumerate(texts)]
    return ScreenState("", list(texts), None, lines)


class TestTemporalReliability(unittest.TestCase):
    def test_close_rows_stay_separate_across_frames(self):
        builder = TemporalOptionBuilder()
        state = menu("Enter the inn.", "Leave the city.", "Visit the church.")
        builder.update(state)
        builder.update(state)
        self.assertEqual(builder.texts(), state.options)
        self.assertEqual(len(builder.regions()), 3)

    def test_identical_geometry_does_not_identify_unrelated_dialog(self):
        builder = TemporalOptionBuilder()
        builder.update(menu("Purchase armour.", "Repair shields."))
        self.assertFalse(builder.compatible(menu("Pray for mercy.", "Learn Latin."), .01))
        self.assertTrue(builder.compatible(menu("Purchase armour.", "Repair shields."), .01))

    def test_consensus_is_recomputed_only_after_an_observation(self):
        builder = TemporalOptionBuilder()
        state = menu("Enter the inn.")
        builder.update(state)
        with patch("darktext.temporal._temporal_best_sample", return_value="Enter the inn.") as best:
            for _ in range(100):
                texts = builder.texts()
                texts[0] = "caller cannot corrupt the cached result"
            self.assertEqual(best.call_count, 1)
            builder.update(state)
            self.assertEqual(builder.texts(), state.options)
            self.assertEqual(best.call_count, 2)


class TestHoverAndOCR(unittest.TestCase):
    def test_highlight_must_persist_before_switching(self):
        hover = HoverDebouncer(.04)
        self.assertIsNone(hover.update(0, 0))
        self.assertIsNone(hover.update(1, .01))
        self.assertIsNone(hover.update(0, .02))
        self.assertEqual(hover.update(0, .07), 0)
        self.assertIsNone(hover.update(None, .08))
        self.assertIsNone(hover.update(0, .09))
        self.assertEqual(hover.update(0, .14), 0)

    def test_worker_stops_and_refuses_new_work(self):
        worker = FastOcrWorker(Mock())
        worker.stop()
        self.assertFalse(worker._thread.is_alive())
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.assertFalse(worker.request(frame, fast_signature(frame), "stopped"))

    def test_daemon_rejects_stale_ocr_and_cleans_up(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        old = np.full_like(frame, 255)
        empty = ScreenState("", [], None, [])
        with (
            patch.object(daemon, "build_engine"),
            patch.object(daemon, "capture_logical_game", side_effect=[frame, frame, frame, KeyboardInterrupt]),
            patch.object(daemon, "extract_state", return_value=empty),
            patch.object(daemon, "OptionAudioCache") as cache,
            patch.object(daemon, "DirectLiveSpeaker") as speaker,
            patch.object(daemon, "CachedWavPlayer") as player,
            patch.object(daemon, "FastOcrWorker") as worker,
        ):
            worker.return_value.latest.side_effect = [
                (ScreenState("Obsolete", [], None, []), fast_signature(old), "old", 0, 1, None),
                (ScreenState("Current", [], None, []), fast_signature(frame), "new", 1, 2, None),
            ]
            self.assertEqual(daemon.cmd_daemon(None), 0)
            speaker.return_value.speak.assert_called_once_with("Current")
            self.assertIn("stale-result-refresh", [c.args[2] for c in worker.return_value.request.call_args_list])
            for obj in (cache, speaker, player, worker):
                obj.return_value.stop.assert_called()


class TestCacheReliability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = OptionAudioCache(Path(self.temp.name))
        self.addCleanup(self.cache.stop)

    def test_long_options_keep_rate_and_survive_restart(self):
        pcm = b"\0\0" * (16000 * 8)
        path = self.cache.save_pcm("Listen to the innkeeper.", "voice", pcm, sample_rate=16000)
        self.assertIsNotNone(path)
        with wave.open(str(path)) as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.getnframes(), 128000)
        self.cache.stop()
        other = OptionAudioCache(Path(self.temp.name))
        self.addCleanup(other.stop)
        self.assertEqual(other.get_cached_wav("Listen to the innkeeper.", "voice"), path)

    def test_concurrent_writers_leave_valid_wav_and_no_temporary_files(self):
        barrier = threading.Barrier(4)
        def save(i):
            barrier.wait()
            return self.cache.save_pcm("Same option.", "voice", bytes([i, 0]) * 20000)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool:
            paths = list(pool.map(save, range(4)))
        self.assertTrue(all(paths))
        self.assertTrue(self.cache._valid_wav(paths[0]))
        self.assertFalse(list(Path(self.temp.name).glob("*.tmp.wav")))

    def test_cancellation_between_dequeue_and_launch(self):
        entered, release = threading.Event(), threading.Event()
        def rate(model):
            entered.set()
            release.wait(2)
            return 22050
        with patch("darktext.audio_cache.voice_sample_rate", side_effect=rate), patch("darktext.audio_cache.subprocess.Popen") as popen:
            self.cache.queue_options(["Obsolete option."], lambda i: Path("voice.onnx"))
            self.assertTrue(entered.wait(1))
            self.cache.clear_queue()
            release.set()
            self.cache.stop()
            popen.assert_not_called()

    def test_inflight_job_remains_deduplicated(self):
        entered, release = threading.Event(), threading.Event()
        def rate(model):
            entered.set()
            release.wait(2)
            return 22050
        with patch("darktext.audio_cache.voice_sample_rate", side_effect=rate):
            self.cache.queue_options(["Same option."], lambda i: Path("voice.onnx"))
            self.assertTrue(entered.wait(1))
            for _ in range(10):
                self.cache.queue_options(["Same option."], lambda i: Path("voice.onnx"))
            self.assertTrue(self.cache.queue.empty())
            self.cache.clear_queue()
            release.set()
            self.cache.stop()


class TestSpeechReliability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.voice = self.root / "voice.onnx"
        Path(str(self.voice) + ".json").write_text(json.dumps({"audio": {"sample_rate": 16000}}))
        self.piper = self.script("piper", '''import pathlib, sys, time
text = sys.stdin.read().strip()
sys.stdout.buffer.write(b'\\0\\0' * 1600)
sys.stdout.buffer.flush()
pathlib.Path(__file__).with_name('ready').write_text(text)
if text == 'old': time.sleep(5)
if text == 'fail': sys.exit(7)
''')
        self.aplay = self.script("aplay", '''import pathlib, sys
pathlib.Path(__file__).with_name('aplay-args').write_text(' '.join(sys.argv[1:]))
sys.stdin.buffer.read()
''')
        self.patch = patch("darktext.speaker.DIRECT_PIPER_BIN", self.piper)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.cache = OptionAudioCache(self.root / "cache")
        self.addCleanup(self.cache.stop)
        self.speaker = DirectLiveSpeaker(self.cache)
        self.speaker.aplay = str(self.aplay)
        self.addCleanup(self.speaker.stop)

    def script(self, name, code):
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o755)
        return path

    def test_interrupted_utterance_never_caches_partial_audio(self):
        self.assertTrue(self.speaker.speak_option("old", self.voice))
        old_token = self.speaker._stop_requested
        old_proc = self.speaker.piper_proc
        wait_for(lambda: (self.root / "ready").exists())
        self.assertTrue(self.speaker.speak_option("new", self.voice))
        self.assertTrue(old_token.is_set())
        self.assertIsNot(old_token, self.speaker._stop_requested)
        wait_for(lambda: not self.speaker.is_alive())
        self.assertIsNotNone(old_proc.poll())
        self.assertIsNone(self.cache.get_cached_wav("old", "voice"))
        path = self.cache.get_cached_wav("new", "voice")
        self.assertIsNotNone(path)
        self.assertIn("-r 16000", (self.root / "aplay-args").read_text())
        with wave.open(str(path)) as wav:
            self.assertEqual(wav.getframerate(), 16000)

    def test_failed_synthesis_does_not_cache_partial_audio(self):
        self.assertTrue(self.speaker.speak_option("fail", self.voice))
        wait_for(lambda: not self.speaker.is_alive())
        self.assertIsNone(self.cache.get_cached_wav("fail", "voice"))

    def test_invalid_metadata_fails_before_process_launch(self):
        Path(str(self.voice) + ".json").write_text('{"audio":{"sample_rate":0}}')
        self.assertFalse(self.speaker.speak_option("invalid", self.voice))
        self.assertFalse((self.root / "ready").exists())

    def test_missing_metadata_uses_legacy_rate(self):
        self.assertEqual(voice_sample_rate(self.root / "legacy.onnx"), 22050)
