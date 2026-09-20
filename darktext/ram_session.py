"""Conservative dialog cache and bounded, OCR-free gameplay diagnostics."""

from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import signal
from pathlib import Path
import time
from urllib.request import urlopen

from .ram_text import BUFFER_OFFSET, BUFFER_SIZE, EXE_SHA256, READ_ERRORS, decode_buffer


def digest(data):
    return hashlib.sha256(data).hexdigest()


def context(segment):
    # Investigation fields, NOT certified screen IDs. Changes invalidate caches;
    # equality does not prove that two samples represent the same visible screen.
    return {f'{offset:04x}': segment[offset:offset + size].hex()
            for offset, size in [(0x8D6, 2), (0xA88D, 6)]}


class DialogCache:
    """Keep narrative/options out of the shared popup scratch-buffer lifecycle."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.dialog = None
        self.dialog_hash = None
        self.context = None
        self.pending = None
        self.last_event = None
        self.captured_at = None
        self.blocked_hash = None

    def observe(self, segment, base):
        raw = segment[BUFFER_OFFSET:BUFFER_OFFSET + BUFFER_SIZE]
        buffer_hash = digest(raw.split(b'\0', 1)[0])
        ctx = (base, context(segment))
        invalidated = self.context is not None and self.context != ctx
        if invalidated:
            previous_hash = self.pending[0] if self.pending else None
            self.reset()
            self.blocked_hash = previous_hash
        self.context = ctx
        key = (buffer_hash, ctx)
        if key != self.pending:
            self.pending = key
            if invalidated:
                return {'kind': 'context_changed', 'cached_dialog': None,
                        'visibility': 'unverified', 'context': ctx[1]}
            return None
        if self.blocked_hash == buffer_hash:
            # A new owner can inherit the old scratch bytes. Do not immediately
            # repopulate the cache we just invalidated from those same bytes.
            return None
        self.blocked_hash = None
        try:
            parsed = decode_buffer(raw)
        except ValueError as exc:
            # Recognize only the observed scratch-stream shape. This does not
            # establish popup visibility, row availability, or popup closure.
            text = raw.split(b'\0', 1)[0]
            candidate = (text.startswith(b'\x15') and b'\n\x15' in text)
            kind = 'auxiliary_buffer' if candidate else 'unsupported_buffer'
            event = {'kind': kind, 'reason': str(exc),
                     'popup_candidate': candidate,
                     'cached_dialog': self.dialog,
                     'cached_at': self.captured_at,
                     'cache_status': 'retained_unverified' if self.dialog else 'empty',
                     'visibility': 'unverified'}
        else:
            value = asdict(parsed)
            if value != self.dialog:
                self.dialog = value
                self.dialog_hash = key[0]
                self.captured_at = datetime.now(timezone.utc).isoformat()
                kind = 'dialog_buffer_changed'
            elif self.last_event and self.last_event[0] != 'dialog_buffer_changed':
                kind = 'dialog_buffer_restored'
            else:
                kind = 'dialog_buffer_changed'
            event = {'kind': kind, 'dialog': value,
                     'cache_status': 'matches_buffer', 'visibility': 'unverified'}
        identity = (event['kind'], key[0], self.dialog_hash)
        # A restoration is one event, not an alternating restored/changed loop.
        if self.last_event and key[0] == self.last_event[1]:
            return None
        self.last_event = identity
        event.update(buffer_sha256=key[0], context=ctx[1], data_segment=base)
        return event


class Recorder:
    """Append-only manifest and compressed snapshots, capped per session."""
    def __init__(self, directory: Path, api: str, max_mb: int):
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.api = api
        self.limit = max_mb * 1024 * 1024
        self.used = 0
        self.sequence = 0
        self.full = False
        self.last_snapshot = -float('inf')
        self.last_sample = None
        self.started = time.monotonic()
        self.log = (directory / 'events.jsonl').open('x', encoding='utf-8')
        self.write({'kind': 'session_start', 'schema': 1, 'exe_sha256': EXE_SHA256,
                    'api': api, 'budget_bytes': self.limit,
                    'note': 'Snapshots contain game data. Frames and RAM are not atomic together.'})

    def write(self, event):
        if self.full:
            return
        line = json.dumps(dict(event, utc=datetime.now(timezone.utc).isoformat(),
                               elapsed_seconds=round(time.monotonic() - self.started, 3))) + '\n'
        data = line.encode('utf-8')
        if self.used + len(data) > self.limit - 1024:
            self.stop_at_limit()
            return
        self.log.write(line)
        self.log.flush()
        self.used += len(data)

    def stop_at_limit(self):
        if not self.full:
            self.log.write(json.dumps({'kind': 'recording_limit_reached', 'bytes': self.used}) + '\n')
            self.log.flush()
            print('darktext-ram: recording limit reached; capture continues without logging', flush=True)
            self.full = True

    def artifact(self, name, data):
        if self.full or self.used + len(data) > self.limit - 4096:
            self.stop_at_limit()
            return None
        (self.directory / name).write_bytes(data)
        self.used += len(data)
        return name

    def sample(self, segment, base, event):
        if self.full:
            return
        raw = segment[BUFFER_OFFSET:BUFFER_OFFSET + BUFFER_SIZE]
        sample_key = (base, digest(raw), context(segment))
        if sample_key != self.last_sample:
            self.write({'kind': 'raw_buffer_change', 'data_segment': base,
                        'buffer_hex': raw.hex(), 'context': sample_key[2]})
            self.last_sample = sample_key
        if event:
            self.write(event)
        now = time.monotonic()
        # Periodic snapshots catch popup closure even when the text is unchanged.
        # Transition snapshots are rate-limited so rapid hover cannot flood disk.
        if now - self.last_snapshot < (0.5 if event else 2.0):
            return
        self.last_snapshot = now
        self.sequence += 1
        stem = f'{self.sequence:06d}'
        entry = {'kind': 'snapshot', 'data_segment': base,
                 'ram': self.artifact(stem + '.ds.bin.gz', gzip.compress(segment)),
                 'ram_sha256': digest(segment)}
        try:
            with urlopen(self.api.rstrip('/') + '/api/v1/video/frame', timeout=1) as response:
                frame = response.read(4 * 1024 * 1024 + 1)
            if len(frame) > 4 * 1024 * 1024 or not frame.startswith(b'P6'):
                raise ValueError('Unsupported or oversized video frame')
            entry['frame'] = self.artifact(stem + '.ppm.gz', gzip.compress(frame))
            entry['frame_sha256'] = digest(frame)
        except READ_ERRORS as exc:
            entry['frame_error'] = str(exc)
        self.write(entry)

    def close(self):
        self.write({'kind': 'session_end'})
        self.log.close()


def watch(reader, directory=None, max_mb=256):
    cache = DialogCache()
    recorder = Recorder(directory, reader.api, max_mb) if directory else None
    last_error = None
    old_handler = signal.getsignal(signal.SIGTERM)
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            try:
                segment = reader.read_segment()
            except READ_ERRORS as exc:
                cache.reset()
                if str(exc) != last_error:
                    event = {'kind': 'unavailable', 'reason': str(exc), 'cached_dialog': None}
                    print(json.dumps(event), flush=True)
                    if recorder:
                        recorder.write(event)
                last_error = str(exc)
                time.sleep(1)  # bounded reconnect/discovery rate
                continue
            if last_error:
                event = {'kind': 'reconnected', 'data_segment': reader.base}
                print(json.dumps(event), flush=True)
                if recorder:
                    recorder.write(event)
            last_error = None
            event = cache.observe(segment, reader.base)
            if event:
                print(json.dumps(event), flush=True)
            if recorder:
                recorder.sample(segment, reader.base, event)
            time.sleep(.15)
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        if recorder:
            recorder.close()
