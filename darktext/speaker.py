"""Direct audio output and TTS playback with low-latency streaming."""

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

from .audio_cache import OptionAudioCache
from .voice import voice_sample_rate
from .config import DIRECT_PIPER_BIN, DIRECT_VOICE_DIR, TTS_COMMAND_FILE


def read_tts_command() -> str:
    """Read legacy configured external TTS command."""
    if not TTS_COMMAND_FILE.exists():
        raise RuntimeError(f"Missing {TTS_COMMAND_FILE}; rerun installer")
    cmd = TTS_COMMAND_FILE.read_text(encoding="utf-8").strip()
    if not cmd:
        raise RuntimeError(f"{TTS_COMMAND_FILE} is empty")
    return cmd


def get_available_voices() -> list[Path]:
    """List available Piper ONNX voice models sorted alphabetically."""
    return sorted(DIRECT_VOICE_DIR.glob("*.onnx"))


def get_voice_for_option(slot_idx: int) -> Path | None:
    """Deterministic voice model selection for menu option slots."""
    models = get_available_voices()
    if not models:
        return None
    return models[slot_idx % len(models)]


def get_voice_for_narrative() -> Path | None:
    """Voice model selection for narrative/story text."""
    models = get_available_voices()
    if not models:
        return None
    # Use first available model or cycle
    return models[0]


class CachedWavPlayer:
    """High-speed player for cached WAV files using aplay."""

    def __init__(self):
        self.pgid: int | None = None
        self.proc: subprocess.Popen | None = None
        self.aplay = shutil.which("aplay") or "/usr/bin/aplay"

    def stop(self):
        """Immediately kill running aplay process."""
        proc = self.proc
        pgid = self.pgid
        self.proc = None
        self.pgid = None

        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

        if pgid is not None and proc is not None and proc.poll() is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        if proc is not None:
            try:
                proc.wait(timeout=0.1)
            except Exception:
                pass

    def is_alive(self) -> bool:
        """Check whether audio is currently playing."""
        if self.proc is not None:
            if self.proc.poll() is None:
                return True
            self.proc = None
            self.pgid = None
        return False

    def play(self, wav_path: Path | str) -> bool:
        """Play WAV file with low buffer latency."""
        self.stop()
        path = Path(wav_path)
        if not path.is_file():
            return False
        try:
            proc = subprocess.Popen(
                [
                    self.aplay,
                    "-q",
                    "--buffer-time=20000",
                    "--period-time=5000",
                    str(path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.proc = proc
            self.pgid = proc.pid
            return True
        except Exception as exc:
            print(f"darktext: aplay error: {exc}", file=sys.stderr, flush=True)
            return False


class DirectLiveSpeaker:
    """Foreground live TTS owned entirely by Darktext.
    
    Streams live Piper audio directly to aplay for minimum time-to-sound,
    while simultaneously teeing the raw PCM to the persistent audio cache.
    """

    def __init__(self, audio_cache: OptionAudioCache | None = None):
        self.audio_cache = audio_cache
        self.aplay = shutil.which("aplay") or "/usr/bin/aplay"
        self.piper_proc: subprocess.Popen | None = None
        self.aplay_proc: subprocess.Popen | None = None
        self.pgid: int | None = None
        self._pump_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()

    def stop(self):
        """Cleanly terminate both Piper and aplay immediately."""
        self._stop_requested.set()
        for proc in (self.aplay_proc, self.piper_proc):
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                try:
                    proc.wait(timeout=0.1)
                except Exception:
                    pass

        self.aplay_proc = None
        self.piper_proc = None
        self.pgid = None
        thread = self._pump_thread
        if thread is not None:
            thread.join(timeout=0.2)
        self._pump_thread = None

    def is_alive(self) -> bool:
        """Include synthesis and cache finalization in the utterance lifetime."""
        return bool(self._pump_thread and self._pump_thread.is_alive())

    def speak(self, text: str) -> bool:
        """Speak narrative using narrative voice."""
        model = get_voice_for_narrative()
        return self._launch(text, model, cache_on_finish=False)

    def speak_option(
        self, text: str, voice_model: Path | None, cache_on_finish: bool = True
    ) -> bool:
        """Speak option choice using designated voice and save to cache."""
        return self._launch(text, voice_model, cache_on_finish=cache_on_finish)

    def _launch(self, text: str, model: Path | None, cache_on_finish: bool) -> bool:
        text = re.sub(r"\s+", " ", (text or "")).strip()
        if not text or model is None:
            return False

        self.stop()
        # Never clear a token an older pump still holds.
        stop = threading.Event()
        self._stop_requested = stop

        piper = None
        aplay = None
        try:
            sample_rate = voice_sample_rate(model)
            piper = subprocess.Popen(
                [
                    str(DIRECT_PIPER_BIN),
                    "-m",
                    str(model),
                    "--output-raw",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            aplay = subprocess.Popen(
                [
                    self.aplay,
                    "-q",
                    "-f",
                    "S16_LE",
                    "-r",
                    str(sample_rate),
                    "-c",
                    "1",
                    "--buffer-time=20000",
                    "--period-time=5000",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            if piper.stdin is None or piper.stdout is None or aplay.stdin is None:
                raise RuntimeError("Failed to create subprocess pipes")

            # Send input text to Piper
            piper.stdin.write((text + "\n").encode("utf-8"))
            piper.stdin.close()

        except Exception as exc:
            for proc in (aplay, piper):
                if proc is not None:
                    try:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception:
                        pass
                    for stream in (proc.stdin, proc.stdout):
                        if stream is not None:
                            stream.close()
            print(f"darktext: direct live TTS launch error: {exc}", file=sys.stderr, flush=True)
            return False

        self.piper_proc = piper
        self.aplay_proc = aplay
        self.pgid = aplay.pid

        # Background thread pumps from Piper to aplay and collects PCM for caching
        def pump():
            chunks = []
            p_stdout = piper.stdout
            a_stdin = aplay.stdin
            cache = self.audio_cache
            voice_name = model.stem

            complete = False
            try:
                while not stop.is_set():
                    buf = p_stdout.read1(4096)
                    if not buf:
                        complete = True
                        break
                    try:
                        a_stdin.write(buf)
                        a_stdin.flush()
                    except (BrokenPipeError, OSError):
                        break
                    if cache_on_finish:
                        chunks.append(buf)
            except Exception:
                pass
            finally:
                try:
                    a_stdin.close()
                except Exception:
                    pass
                try:
                    p_stdout.close()
                except Exception:
                    pass

                # On a broken audio pipe the producer may still be running.
                # Reap both children before reporting this utterance finished.
                for proc in (piper, aplay):
                    if not complete or stop.is_set():
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                    proc.wait()

                # If speech completed cleanly without interruption, cache the audio!
                if (cache_on_finish and complete and not stop.is_set()
                        and piper.returncode == 0 and aplay.returncode == 0
                        and cache is not None):
                    raw_pcm = b"".join(chunks)
                    if len(raw_pcm) >= 1000:
                        cache.save_pcm(text, voice_name, raw_pcm, sample_rate=sample_rate)

        thread = threading.Thread(target=pump, daemon=True)
        self._pump_thread = thread
        thread.start()
        return True
