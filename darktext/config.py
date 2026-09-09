"""Centralized configuration and constants for Darklands text reader."""

import os
import shutil
from pathlib import Path

# Base directories
PROJECT = Path(
    os.environ.get("DARKTEXT_DATA_DIR", Path.home() / "darklands-accessibility")
).expanduser()
TTS_COMMAND_FILE = PROJECT / "tts-command.txt"
DEBUG_DIR = PROJECT / "debug"
LOGS_DIR = PROJECT / "logs"

# Audio cache settings
TTS_CACHE_DIR = PROJECT / "tts-cache"
AUDIO_CACHE_DIR = TTS_CACHE_DIR / "menu-options"

# Piper TTS settings
DIRECT_PIPER_BIN = Path(
    os.environ.get(
        "DARKTEXT_PIPER_BIN",
        shutil.which("piper")
        or Path.home() / "projects/readSelectedText/.venv/bin/piper",
    )
).expanduser()
DIRECT_VOICE_DIR = Path(
    os.environ.get(
        "DARKTEXT_VOICE_DIR", Path.home() / ".local/share/piper-tts/voices"
    )
).expanduser()
SAMPLE_RATE = 22050

# Native Darklands 640x480 logical coordinates.
# Story/dialog box excluding character stats, time, and sidebar.
TEXT_RECT = (135, 45, 625, 455)  # x1, y1, x2, y2
OCR_SCALE = 3
POLL_SECONDS = 0.35
CONTENT_SIMILARITY = 0.93

# Fast hover detection loop settings
FAST_HOVER_POLL = 0.010       # 10ms poll interval
FAST_HOVER_DWELL = 0.0
SCENE_CHECK_INTERVAL = 0.120  # 120ms between scene change checks
SCENE_CHANGE_RATIO = 0.035
FORCED_OCR_INTERVAL = 0.8

# Temporal multi-frame option builder settings
TEMPORAL_MAX_SAMPLES = 12
TEMPORAL_LINE_Y_TOL = 9.0
TEMPORAL_OPTION_Y_TOL = 15.0
TEMPORAL_DIALOG_HARD_CHANGE = 0.18
FOLLOWUPS = (0.8, 1.8)
