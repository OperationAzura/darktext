"""Command line interface and entry points for darktext."""

import argparse
import sys
import time

from .audio_cache import OptionAudioCache
from .capture import capture_logical_game
from .daemon import cmd_daemon
from .ocr import build_engine, extract_state, print_state
from .speaker import (
    CachedWavPlayer,
    DirectLiveSpeaker,
    get_voice_for_narrative,
    get_voice_for_option,
    read_tts_command,
)


def cmd_once(args) -> int:
    """Capture current story pane once, OCR, and print structured result."""
    print("darktext: initializing OCR engine...", flush=True)
    engine = build_engine()
    try:
        game = capture_logical_game()
    except Exception as exc:
        print(f"darktext: capture error: {exc}", file=sys.stderr)
        return 1

    state = extract_state(engine, game, save_debug=True)
    print_state(state)

    if args.speak:
        audio_cache = OptionAudioCache()
        speaker = DirectLiveSpeaker(audio_cache=audio_cache)
        wav_player = CachedWavPlayer()

        if state.selected is not None and 0 <= state.selected - 1 < len(state.options):
            idx = state.selected - 1
            text = state.options[idx].strip()
            voice = get_voice_for_option(idx)
            voice_name = voice.stem if voice else None

            cached = audio_cache.get_cached_wav(text, voice_name)
            if cached is not None and wav_player.play(cached):
                print(f"darktext: spoke selection [cache]: {text}")
            else:
                speaker.speak_option(text, voice, cache_on_finish=True)
                print(f"darktext: spoke selection [live]: {text}")
        elif state.narrative.strip():
            speaker.speak(state.narrative.strip())
            print(f"darktext: spoke narrative: {state.narrative.strip()}")

        time.sleep(2.0)
        audio_cache.stop()
        wav_player.stop()
        speaker.stop()

    return 0


def cmd_speak_test(args) -> int:
    """Test the Piper synthesis and aplay pipeline with a sample utterance."""
    phrase = args.text or "Darklands story accessibility reader test."
    print(f"darktext: speaking test phrase: {phrase}")
    audio_cache = OptionAudioCache()
    speaker = DirectLiveSpeaker(audio_cache=audio_cache)
    model = get_voice_for_narrative()
    if model is None:
        print("darktext: error: no Piper ONNX voices found", file=sys.stderr)
        return 1

    ok = speaker.speak(phrase)
    if not ok:
        print("darktext: speak test failed", file=sys.stderr)
        return 1

    while speaker.is_alive():
        time.sleep(0.05)

    audio_cache.stop()
    return 0


def cmd_tts_command(args) -> int:
    """Show detected configured TTS command."""
    try:
        print(read_tts_command())
        return 0
    except Exception as exc:
        print(f"darktext: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    """Parse command line arguments and dispatch to command handlers."""
    p = argparse.ArgumentParser(
        description="Darklands story/dialog accessibility reader"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser(
        "once", help="OCR current story pane once and print structured result"
    )
    q.add_argument("--speak", action="store_true", help="read extracted text aloud")
    q.set_defaults(func=cmd_once)

    q = sub.add_parser("speak-test", help="test TTS playback")
    q.add_argument(
        "text", nargs="?", default=None, help="optional custom text to speak"
    )
    q.set_defaults(func=cmd_speak_test)

    q = sub.add_parser("daemon", help="watch story state and speak changes")
    q.set_defaults(func=cmd_daemon)

    q = sub.add_parser("tts-command", help="show configured external TTS command")
    q.set_defaults(func=cmd_tts_command)

    args = p.parse_args()
    return args.func(args) or 0
