"""Regression tests for one-shot speech completion and cleanup."""

import argparse
import unittest
from unittest.mock import patch

from darktext import cli
from darktext.models import ScreenState


class TestOnceSpeech(unittest.TestCase):
    def run_once(self, *, cached=False, launch_ok=True):
        with (
            patch.object(cli, "build_engine"),
            patch.object(cli, "capture_logical_game"),
            patch.object(cli, "extract_state", return_value=ScreenState(
                "", ["Listen to this entire menu choice."], 1, [])),
            patch.object(cli, "print_state"),
            patch.object(cli, "get_voice_for_option"),
            patch.object(cli, "OptionAudioCache") as cache_cls,
            patch.object(cli, "DirectLiveSpeaker") as speaker_cls,
            patch.object(cli, "CachedWavPlayer") as player_cls,
            patch.object(cli.time, "sleep") as sleep,
        ):
            cache = cache_cls.return_value
            speaker = speaker_cls.return_value
            player = player_cls.return_value
            cache.get_cached_wav.return_value = "cached.wav" if cached else None
            player.play.return_value = True
            speaker.speak_option.return_value = launch_ok
            # Playback lasts longer than the old two-second cutoff.
            active = player if cached else speaker
            inactive = speaker if cached else player
            active.is_alive.side_effect = [True] * 50 + [False]
            inactive.is_alive.return_value = False
            result = cli.cmd_once(argparse.Namespace(speak=True))
            cache.stop.assert_called_once()
            player.stop.assert_called_once()
            speaker.stop.assert_called_once()
            if launch_ok:
                self.assertEqual(active.is_alive.call_count, 51)
                self.assertGreaterEqual(sleep.call_count, 50)
            return result

    def test_live_speech_waits_until_finished(self):
        self.assertEqual(self.run_once(), 0)

    def test_cached_speech_waits_until_finished(self):
        self.assertEqual(self.run_once(cached=True), 0)

    def test_failed_speech_returns_failure_and_cleans_up(self):
        self.assertEqual(self.run_once(launch_ok=False), 1)
