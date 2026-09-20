# DarkText

> Part of [OperationAzura's Darklands Accessibility project](https://github.com/OperationAzura/darklands-accessibility).
> Requires the [DOSBox Staging accessibility fork](https://github.com/OperationAzura/dosboxStagingAccess).

DarkText is a screen-reader companion for the DOS game **Darklands**. It reads
the game's story pane and highlighted menu choices using OCR, then speaks them
with local Piper voices. It captures the native emulated framebuffer from the
companion DOSBox Staging fork, so it does not depend on window focus or desktop
screen capture.

## Experimental RAM capture branch

This branch adds `python -m darktext.ram_text --exe /path/to/darkland.exe`
for direct resolved-text capture without OCR. It is a research prototype: the
shared buffer can contain stale text and hidden choices. See
[RAM capture findings and usage](docs/ram-text-research.md) before testing.

For gameplay diagnostics, run `./scripts/record-ram-session.sh`. It starts the
game and recorder without OCR. See the [recording guide](docs/recording-ram-sessions.md)
for popup tests, notes, recovery behavior, and where the data is saved.

## Features

- OCR of Darklands narrative text and menu choices
- Fast highlighted-option tracking
- Multi-frame reconstruction for text obscured by the game cursor
- Low-latency Piper speech with a persistent, content-addressed audio cache
- One-shot diagnostics and a continuous reader daemon

## Requirements

- Python 3.11 or newer
- `piper` and `aplay` available on `PATH`
- One or more Piper `.onnx` voice models
- The Darklands accessibility fork of DOSBox Staging, listening on localhost

On Debian or Ubuntu, `alsa-utils` provides `aplay`. Piper and voice installation
varies by distribution.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
source .venv/bin/activate
```

Configure non-default locations with environment variables:

| Variable | Default |
| --- | --- |
| `DOSBOX_API_URL` | `http://127.0.0.1:8086` |
| `DARKTEXT_DATA_DIR` | `~/darklands-accessibility` (backward compatible) |
| `DARKTEXT_PIPER_BIN` | `piper` on `PATH` |
| `DARKTEXT_VOICE_DIR` | `~/.local/share/piper-tts/voices` |

## Usage

With the modified DOSBox running Darklands:

```bash
darktext once
darktext once --speak
darktext speak-test
darktext daemon
```

The daemon announces new dialog and follows the currently highlighted option.
`once --speak` waits for the selected option or narrative to finish playing
before exiting, and returns a nonzero status if speech cannot be launched.
Generated cache files and diagnostics are stored beneath `DARKTEXT_DATA_DIR`.

## Test

```bash
python -m unittest discover -s tests -v
```

Audio playback tests require a working sound device, Piper, and voice models;
enable them explicitly with `DARKTEXT_RUN_AUDIO_TESTS=1`.

## Relationship to DOSBox Staging

DarkText requires `GET /api/v1/video/frame` from the sibling DOSBox fork. The
server must remain bound to localhost because its broader API exposes emulator
memory and input controls.

## License

[MIT](LICENSE). DarkText's runtime dependencies retain their own licenses.

## OCR and speech reliability

The reader debounces highlights for 40 ms, rejects OCR results from a screen
that has changed, and periodically refreshes small text changes. Speech uses
voice-model sample-rate metadata, and completed menu audio up to 30 seconds can
be cached. Interrupted or failed synthesis is not cached.

See the [implementation review and validation notes](docs/ocr-speech-improvements.md)
for the changes, tradeoffs, and further accuracy work that needs captured-game
benchmarks.
