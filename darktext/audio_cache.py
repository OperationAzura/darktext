"""Content-addressable persistent audio cache and background pre-rendering for menu options."""

import hashlib
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from .config import AUDIO_CACHE_DIR, DIRECT_PIPER_BIN, SAMPLE_RATE
from .voice import voice_sample_rate


def normalize_audio_text(text: str) -> str:
    """Normalize text for consistent audio caching."""
    text = re.sub(r"^\s*(?:\.{1,4}|…|[·•\-])\s*", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(".!?,;:")
    return text.lower()


def text_content_hash(text: str) -> str:
    """Stable 16-hex content hash of normalized option text."""
    norm = normalize_audio_text(text)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


class OptionAudioCache:
    """Persistent content-addressable audio cache and background synthesizer."""

    MAX_AUDIO_SECONDS = 30

    WORKERS = 2  # Utilize multi-core processor for parallel background synthesis

    def __init__(self, cache_dir: Path = AUDIO_CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.lock = threading.Lock()
        self.queue: queue.PriorityQueue = queue.PriorityQueue()
        self.counter = 0
        self._generation = 0
        self._pending_keys: set[tuple[str, str]] = set()

        # In-memory index: text_hash -> dict of {voice_name: Path}
        self._index: dict[str, dict[str, Path]] = {}
        # Mapping between normalized text and text_hash for fuzzy matching
        self._norm_to_hash: dict[str, str] = {}
        self._hash_to_norm: dict[str, str] = {}

        self._scan_existing_cache()

        # Background worker threads
        self._stop_event = threading.Event()
        self._active_procs: set[subprocess.Popen] = set()
        self._workers: list[threading.Thread] = []
        for i in range(self.WORKERS):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"darktext-audio-cache-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    @classmethod
    def _valid_wav(cls, path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as wav:
                frames = wav.getnframes()
                return (wav.getnchannels() == 1 and wav.getsampwidth() == 2
                        and 8000 <= wav.getframerate() <= 192000
                        and 500 <= frames <= wav.getframerate() * cls.MAX_AUDIO_SECONDS
                        and len(wav.readframes(frames)) == frames * 2)
        except (OSError, EOFError, wave.Error):
            return False

    def _scan_existing_cache(self):
        """Index complete WAV files, retaining valid longer options at their native rate."""
        count = 0
        for wav_path in self.cache_dir.glob("*.wav"):
            if wav_path.name.endswith(".tmp.wav"):
                continue
            try:
                if not self._valid_wav(wav_path):
                    continue
                # Expected format: <voice_name>_<text_hash>.wav
                parts = wav_path.stem.split("_")
                if len(parts) >= 2:
                    text_hash = parts[-1]
                    voice_name = "_".join(parts[:-1])
                    self._record(text_hash, voice_name, wav_path)
                    count += 1
                    txt_path = wav_path.with_suffix(".txt")
                    if txt_path.is_file():
                        try:
                            norm = txt_path.read_text(encoding="utf-8").strip()
                            if norm:
                                self._norm_to_hash[norm] = text_hash
                                self._hash_to_norm[text_hash] = norm
                        except Exception:
                            pass
            except Exception:
                pass
        if count > 0:
            print(f"darktext: indexed {count} persistent audio cache files", flush=True)

    def _record(self, text_hash: str, voice_name: str, path: Path):
        with self.lock:
            if text_hash not in self._index:
                self._index[text_hash] = {}
            self._index[text_hash][voice_name] = path

    def record_text_mapping(self, text: str):
        """Associate text with its content hash for fuzzy matching."""
        norm = normalize_audio_text(text)
        if norm:
            h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
            with self.lock:
                self._norm_to_hash[norm] = h
                self._hash_to_norm[h] = norm

    def get_cached_wav_with_info(
        self, text: str, preferred_voice_name: str | None = None
    ) -> tuple[Path | None, str, float]:
        """Find cached WAV for this text using deterministic content hash.
        
        Returns (path, match_info, similarity_score).
        """
        if not text or not text.strip():
            return None, "empty", 0.0

        norm = normalize_audio_text(text)
        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

        # 1. Exact content-hash match
        with self.lock:
            entry = self._index.get(h)
            if entry:
                if preferred_voice_name and preferred_voice_name in entry:
                    p = entry[preferred_voice_name]
                    if p.is_file():
                        return p, "exact", 1.0
                for p in entry.values():
                    if p.is_file():
                        return p, "exact", 1.0

        # Check disk directly in case written externally
        if preferred_voice_name:
            candidate = self.cache_dir / f"{preferred_voice_name}_{h}.wav"
            if candidate.is_file() and self._valid_wav(candidate):
                self._record(h, preferred_voice_name, candidate)
                self.record_text_mapping(text)
                return candidate, "exact", 1.0

        return None, "miss", 0.0

    def get_cached_wav(
        self, text: str, preferred_voice_name: str | None = None
    ) -> Path | None:
        """Find cached WAV for this text."""
        p, _, _ = self.get_cached_wav_with_info(text, preferred_voice_name)
        return p

    def save_pcm(self, text: str, voice_name: str, pcm_bytes: bytes,
                 sample_rate: int = SAMPLE_RATE) -> Path | None:
        """Atomically wrap raw PCM into a cached WAV file, write text sidecar, and update index."""
        if (not text or not 8000 <= sample_rate <= 192000
                or len(pcm_bytes) < 1000 or len(pcm_bytes) % 2
                or len(pcm_bytes) > sample_rate * 2 * self.MAX_AUDIO_SECONDS):
            return None

        norm = normalize_audio_text(text)
        if len(norm.split()) > 20 or len(norm) > 130:
            return None

        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
        self.record_text_mapping(text)

        final_path = self.cache_dir / f"{voice_name}_{h}.wav"
        tmp_path = None
        txt_path = self.cache_dir / f"{voice_name}_{h}.txt"

        try:
            with tempfile.NamedTemporaryFile(
                dir=self.cache_dir, prefix=f"{voice_name}_{h}_", suffix=".tmp.wav",
                delete=False,
            ) as temporary:
                tmp_path = Path(temporary.name)
            with wave.open(str(tmp_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm_bytes)
            os.replace(tmp_path, final_path)
            try:
                txt_path.write_text(norm, encoding="utf-8")
            except Exception:
                pass
            self._record(h, voice_name, final_path)
            return final_path
        except Exception as exc:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            print(f"darktext: failed to save audio cache file: {exc}", file=sys.stderr, flush=True)
            return None

    def queue_options(
        self,
        options: list[str],
        voice_resolver_fn,
        selected_index: int | None = None,
    ):
        """Enqueue uncached options for background pre-rendering."""
        for i, text in enumerate(options):
            cleaned = re.sub(r"\s+", " ", (text or "").strip())
            if not cleaned:
                continue
            # Never queue narrative paragraphs or oversized merged strings as menu choices
            if len(cleaned) > 130 or len(cleaned.split()) > 20:
                continue

            voice_model = voice_resolver_fn(i)
            if voice_model is None:
                continue

            voice_name = voice_model.stem

            # Skip if already cached (exact or high-confidence fuzzy)
            cached_p, match_info, _ = self.get_cached_wav_with_info(cleaned, voice_name)
            if cached_p is not None:
                continue

            h = text_content_hash(cleaned)
            key = (h, voice_name)
            with self.lock:
                if self._stop_event.is_set() or key in self._pending_keys:
                    continue
                self._pending_keys.add(key)
                self.counter += 1

                # Prioritize: selected option (0), adjacent options (1..2), others (5+)
                if selected_index is not None:
                    dist = abs(i - selected_index)
                    priority = 0 if dist == 0 else (1 if dist == 1 else 5 + dist)
                else:
                    priority = 5 + i

                self.queue.put((priority, self.counter, cleaned, voice_name, voice_model, self._generation))

    def clear_queue(self):
        """Invalidate queued/claimed jobs and terminate active synthesis."""
        with self.lock:
            self._generation += 1
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            self._pending_keys.clear()
            procs = list(self._active_procs)
        for proc in procs:
            self._terminate(proc)

    @staticmethod
    def _terminate(proc):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

    def stop(self):
        """Shut down background workers and reap their subprocesses."""
        self._stop_event.set()
        self.clear_queue()
        for worker in self._workers:
            worker.join(timeout=1.0)

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                item = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            priority, _counter, text, voice_name, voice_model, generation = item
            key = (text_content_hash(text), voice_name)
            proc = None
            started = time.monotonic()
            try:
                if self.get_cached_wav(text, voice_name) is not None:
                    continue
                sample_rate = voice_sample_rate(voice_model)
                piper_cmd = [str(DIRECT_PIPER_BIN), "-m", str(voice_model), "--output-raw"]
                nice_bin = shutil.which("nice")
                if nice_bin:
                    piper_cmd = [nice_bin, "-n", "10"] + piper_cmd
                # Claim and launch under the same lock as cancellation. A job
                # removed from the queue cannot slip past clear_queue().
                with self.lock:
                    if generation != self._generation or self._stop_event.is_set():
                        continue
                    proc = subprocess.Popen(
                        piper_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, start_new_session=True,
                    )
                    self._active_procs.add(proc)
                raw, _ = proc.communicate(input=(text + "\n").encode(), timeout=25)
                with self.lock:
                    current = generation == self._generation and not self._stop_event.is_set()
                if current and proc.returncode == 0:
                    saved = self.save_pcm(text, voice_name, raw, sample_rate=sample_rate)
                    if saved is not None:
                        log_time = time.monotonic() - started
                        print(f"darktext: CACHE DONE [{voice_name}, {log_time:.2f}s]: {text}", flush=True)
            except subprocess.TimeoutExpired:
                if proc is not None:
                    self._terminate(proc)
            except Exception as exc:
                print(f"darktext: background Piper error: {exc}", file=sys.stderr, flush=True)
            finally:
                if proc is not None:
                    for stream in (proc.stdin, proc.stdout):
                        if stream is not None:
                            stream.close()
                with self.lock:
                    self._active_procs.discard(proc)
                    # Do not remove a newer generation's job for the same text.
                    if generation == self._generation:
                        self._pending_keys.discard(key)
