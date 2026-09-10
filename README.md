# DarkText

> Part of [OperationAzura's Darklands Accessibility project](https://github.com/OperationAzura/darklands-accessibility).
> Requires the [DOSBox Staging accessibility fork](https://github.com/OperationAzura/dosboxStagingAccess).

DarkText is a screen-reader companion for the DOS game **Darklands**. It reads
the game's story pane and highlighted menu choices using OCR, then speaks them
with local Piper voices. It captures the native emulated framebuffer from the
companion DOSBox Staging fork, so it does not depend on window focus or desktop
screen capture.

On the `native-resolution` branch, DarkText keeps that framebuffer at its real
DOSBox resolution throughout capture and UI analysis. It no longer stretches a
640x400 or 320x200 frame to 640x480 before OCR. Story-pane coordinates, option
regions, highlight detection, frame-difference input, and debug images all use
native framebuffer pixels. Only the cropped story pane is enlarged for OCR.

## Features

- OCR of Darklands narrative text and menu choices
- Native-resolution DOSBox framebuffer processing
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
```

Configure non-default locations with environment variables:

| Variable | Default |
| --- | --- |
| `DOSBOX_API_URL` | `http://127.0.0.1:8086` |
| `DARKTEXT_DATA_DIR` | `~/darklands-accessibility` (backward compatible) |
| `DARKTEXT_PIPER_BIN` | `piper` on `PATH`, then the existing local installation |
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
Generated cache files and diagnostics are stored beneath `DARKTEXT_DATA_DIR`.
On the native-resolution branch, one-shot debug captures are written as
`game-native.png`, `story-native.png`, and `story-boxes-native.png`.

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
