"""Experimental, read-only Darklands resolved-text buffer capture. No OCR imports."""

import argparse
import base64
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time
from urllib.request import Request, urlopen

EXE_SHA256 = '90138ae88acf66ad765e96c0465c1a4aa11eb09b74f365ed18c0f01beb9a86c2'
SIGNATURE_FILE_OFFSET = 0x1915E0
SIGNATURE_DS_OFFSET = 0x820
SIGNATURE_SIZE = 0x60
BUFFER_OFFSET = 0x9085
BUFFER_SIZE = 0x320
POINTER_OFFSET = 0x8CA
OPTION_CODES = b'\x06\x10\x15\x16'


@dataclass(frozen=True)
class BufferText:
    narrative: str
    candidate_options: list[str]
    # The buffer precedes visibility filtering and is also used by other UI.
    visibility: str = 'unverified'
    selection: None = None


def decode_buffer(data: bytes) -> BufferText:
    """Reject ambiguous control streams rather than guess popup/menu semantics."""
    end = data.find(b'\0')
    if end < 0:
        raise ValueError('Text buffer is not terminated')
    data = data[:end]
    allowed = set(b'\n\r\x14\x1d' + OPTION_CODES)
    if not data or any(not (32 <= c < 127 or c in allowed) for c in data):
        raise ValueError('Unsupported or empty text buffer')
    if b'$' in data:
        raise ValueError('Unresolved template token in text buffer')
    pieces = re.split(b'[\x06\x10\x15\x16]', data)

    def clean(part: bytes) -> str:
        return ' '.join(part.translate(bytes.maketrans(b'\x14\x1d', b'  ')).decode('ascii').split())

    narrative = clean(pieces[0])
    if len(narrative) < 20 or not any(c.isalpha() for c in narrative):
        raise ValueError('No supported narrative in shared buffer (possibly a popup)')
    return BufferText(narrative, [clean(p) for p in pieces[1:] if clean(p)])


def memory(api: str, address: int, size: int) -> bytes:
    request = Request(f'{api.rstrip("/")}/api/v1/memory/{address}/{size}',
                      headers={'Accept': 'application/json'})
    with urlopen(request, timeout=3) as response:
        payload = response.read(2 * size + 8192)
    result = json.loads(payload)['memory']
    data = base64.b64decode(result['data'], validate=True)
    if result['addr'] != address or len(data) != size:
        raise ValueError('DOSBox returned an unexpected memory range')
    return data


class RamReader:
    def __init__(self, executable: Path, api: str):
        exe = executable.read_bytes()
        if hashlib.sha256(exe).hexdigest() != EXE_SHA256:
            raise ValueError('Unsupported DARKLAND.EXE: this research profile requires the tested SHA-256')
        self.signature = exe[SIGNATURE_FILE_OFFSET:SIGNATURE_FILE_OFFSET + SIGNATURE_SIZE]
        self.api = api
        self.base = None

    def locate(self, image: bytes) -> int:
        candidates = []
        start = 0
        while (found := image.find(self.signature, start)) >= 0:
            base = found - SIGNATURE_DS_OFFSET
            start = found + 1
            if base >= 0 and base % 16 == 0 and base + 0x10000 <= len(image):
                segment = image[base:base + 0x10000]
                if self.valid_segment(segment, base):
                    candidates.append(base)
        if len(candidates) != 1:
            raise ValueError('Cannot uniquely identify the initialized Darklands data segment')
        return candidates[0]

    def valid_segment(self, segment: bytes, base: int) -> bool:
        return (len(segment) == 0x10000
                and segment[SIGNATURE_DS_OFFSET:SIGNATURE_DS_OFFSET + SIGNATURE_SIZE] == self.signature
                and struct.unpack_from('<HH', segment, POINTER_OFFSET) == (BUFFER_OFFSET, base >> 4))

    def read(self) -> BufferText:
        if self.base is None:
            self.base = self.locate(memory(self.api, 0, 0xA0000))
        segment = memory(self.api, self.base, 0x10000)
        if not self.valid_segment(segment, self.base):
            self.base = None
            raise ValueError('Darklands memory profile no longer matches')
        return decode_buffer(segment[BUFFER_OFFSET:BUFFER_OFFSET + BUFFER_SIZE])


def main() -> int:
    parser = argparse.ArgumentParser(description='Experimental RAM text capture; no OCR. '
        'The shared buffer may be stale and options may be hidden. Not a full screen reader.')
    parser.add_argument('--exe', type=Path, required=True, help='local DARKLAND.EXE used for profile verification')
    parser.add_argument('--api', default='http://127.0.0.1:8086')
    parser.add_argument('--watch', action='store_true', help='print changed stable buffers as JSON lines')
    parser.add_argument('--speak', action='store_true', help='read narrative once; options are never spoken')
    args = parser.parse_args()
    if args.watch and args.speak:
        parser.error('automatic speech is disabled until active-screen identity is verified; use --speak once')
    try:
        reader = RamReader(args.exe, args.api)
        if not args.watch:
            first = reader.read()
            time.sleep(.15)
            if reader.read() != first:
                raise ValueError('Text changed during capture; retry after the dialog settles')
            print(json.dumps(asdict(first)), flush=True)
            if args.speak:
                from .speaker import DirectLiveSpeaker
                speaker = DirectLiveSpeaker()
                try:
                    if not speaker.speak(first.narrative):
                        raise ValueError('Speech could not be started')
                    while speaker.is_alive():
                        time.sleep(.05)
                finally:
                    speaker.stop()
            return 0
        previous = emitted = None
        last_error = None
        while True:
            try:
                current = reader.read()
                if current == previous and current != emitted:
                    print(json.dumps(asdict(current)), flush=True)
                    emitted = current
                previous = current
                last_error = None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                previous = emitted = None
                if str(exc) != last_error:
                    print(json.dumps({'unavailable': str(exc)}), flush=True)
                last_error = str(exc)
            time.sleep(.15)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'darktext-ram: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
