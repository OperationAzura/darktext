# Experimental DOSBox RAM text capture

Branch: `experiment-ram-text`. This is a working buffer-capture prototype,
**not yet a replacement for the OCR screen reader**. The dedicated command uses
no OCR, frame capture, OpenCV, or neural recognition. Existing OCR commands remain
available. Do not run the OCR daemon alongside this experiment.

## What the game stores

Most dialog templates are in the uncompressed `MSGFILES` archive. Our local copy
has 419 entries. Its little-endian 16-bit count is followed by 24-byte directory
entries: a 12-byte filename, a four-byte metadata field, a four-byte payload size,
and a four-byte absolute file offset. Identity comes from the filename (for
example `$CITYS00.MSG`) plus the selected card index, not from the metadata field.
Some messages also live in loose `.MSG` files; other UI text is embedded in code/data.

An MSG starts with a card count. Each card has a five-byte layout header and a
NUL-terminated text stream. Templates contain `$` substitutions for names, places,
and pronouns. Control bytes separate paragraphs, choices, saint/potion choices,
and prompt text. The original game expands templates before laying out pixels.

Background: [Restoration Project format documentation](https://github.com/Arkana-Mechanika-Labs/arkana-mechanika-labs.github.io/blob/main/content/formats/graphics/msg-files.md)
and [message pipeline investigation](https://github.com/Arkana-Mechanika-Labs/arkana-mechanika-labs.github.io/blob/main/content/posts/038-from-msgfiles-to-a-clickable-screen.md).
The offsets below are our observations of the local executable, not a claim that
published disassembly addresses apply to every edition.

## Verified RAM profile

Tested executable SHA-256:
`90138ae88acf66ad765e96c0465c1a4aa11eb09b74f365ed18c0f01beb9a86c2`.

- Resolved shared text buffer: data segment offset `0x9085`.
- Far pointer to it: `DS:0x08CA` (offset), `DS:0x08CC` (segment).
- Conservative capture bound: `0x320` bytes. Overlong/unterminated text is rejected.
- Static identification span: `DS:0x0820`, length `0x60`, corresponding to
  executable file offset `0x1915E0`. The signature is read from the user's verified
  executable; no game bytes are distributed.
- The observed data segment was `0x33BD`, but the implementation discovers its
  location and does not assume that value or use the current CPU's DS register.
- Disassembly of the MZ image showed initialization of the far pointer to this
  buffer at image offset `0xEFD4`, and subsequent references to the same pointer.

The API reads emulated DOS physical memory, not the host DOSBox process's RAM.
An initial conventional-memory snapshot locates the data segment; subsequent
polls read 64 KiB atomically through the existing DOSBox memory API. The profile
and far pointer are revalidated each time. Two matching samples 150 ms apart
reduce partial-build captures, but do not prove that a buffer is visible.

## Run without OCR

Start only the DOSBox accessibility fork with `core=normal`, its webserver enabled,
and the game running. From this checkout, ordinary Python 3.11+ is enough for
text capture:

```sh
python -m darktext.ram_text --exe /path/to/darkland.exe
python -m darktext.ram_text --exe /path/to/darkland.exe --watch
```

After installing this branch, the equivalent entry point is `darktext-ram`.
`--api http://127.0.0.1:8086` selects the API base URL. Output is JSON, including
`narrative`, `candidate_options`, `visibility: "unverified"`, and `selection: null`.
Watch mode now emits structured lifecycle events, including `dialog_buffer_changed`,
`auxiliary_buffer`, `dialog_buffer_restored`, `context_changed`, and `unavailable`.
The observed recipe-popup mechanism now emits `popup_opened` and `popup_closed`;
closure restores a previously captured parent even if its text is absent from RAM.
See [recipe-popup findings](recipe-popup-findings.md) for scope and evidence.
Other retained text remains unverified. See the [gameplay recording guide](recording-ram-sessions.md)
for the cache, strengthened recovery, and diagnostic launcher.

For a deliberate one-time narrative reading, configure the existing Piper/voice
environment variables and add `--speak`. Options are never spoken. `--watch --speak`
is intentionally rejected until active-screen identity is understood.

## What testing established, and what remains

Live testing used a disposable copy of the game, not the user's original saves.
Two successive encounter cards produced resolved narratives matching the display.
The first contained a substituted party name and pronoun. A choice about horses
was present in RAM but hidden on screen. Opening a potion popup overwrote the
shared buffer with another control stream; the decoder rejected that stream.
The following encounter card restored a supported narrative. Manual narrative
speech completed successfully through the existing Piper pipeline. All 40 tests
passed with `DARKTEXT_RUN_AUDIO_TESTS=1`. Twenty warm live fetch/decode samples
averaged 1.8 ms on this machine, excluding the 150 ms stabilization delay and TTS.

Consequences:

- Buffer contents alone do **not** establish current screen identity, visibility,
  enabled state, hover selection, or safe action numbers.
- Leaving a dialog can leave readable stale text. The prototype may return it.
- A popup can destroy the narrative even while its background remains visible.
- Unsupported controls, high-bit characters, unexpanded tokens, and unknown
  executable versions fail closed. Non-English editions are not supported.
- Polling can miss short-lived buffers; two stable samples cannot fix that.
- No performance advantage is claimed beyond eliminating OCR inference; a full
  latency and screen-coverage comparison has not yet been performed.

The remaining implementation step is tracing the shared layout/input controller's row
publication table and lifecycle. We need the active owner/card, raw row ordinals,
hidden/disabled/selectable status, geometry, and hover index. If those cannot be
sampled reliably, add a narrow DOSBox event hook after expansion/publication and
before rasterization, with explicit screen-exit events. Do not promote candidate
options to selectable actions or enable automatic speech by guessing from text.

Synthetic unit tests cover control parsing, unsupported streams, relocated data
segments, ambiguous signatures, profile invalidation, and absence of OCR imports.
No executable, archive, screenshot, save, or memory dump is committed.
