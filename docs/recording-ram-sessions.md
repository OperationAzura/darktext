# Record a gameplay session without OCR

From this checkout on `experiment-ram-text`, run:

```sh
./scripts/record-ram-session.sh
```

On the current installation this finds the DOSBox accessibility fork in the
sibling `darklands-accessibility/local` directory and the game at
`~/dosGames/darklands/darkland.exe`. Set `DARKLANDS_DOSBOX_BIN` and
`DARKLANDS_GAME_EXE` to override those paths. Only standard Python is needed.
Close an existing Darklands launcher first: the script refuses to start if port
8086 is occupied. It starts the game and recorder together, without the OCR daemon
or automatic speech. Normal gameplay can modify saves just as usual.

The terminal prints the recording directory. By default it is
`../ram-recordings/YYYYMMDD-HHMMSS` relative to the repository. To pick a directory:

```sh
./scripts/record-ram-session.sh /path/to/new-session
```

Close DOSBox or press Ctrl+C in that terminal to end the session. The launcher
stops only the child processes it started. A recording directory must be new;
existing recordings are never overwritten.

To attach to a game already running with the API enabled (and OCR stopped):

```sh
python3 -m darktext.ram_text --exe ~/dosGames/darklands/darkland.exe \
  --record /path/to/new-session
```

The recorder can start before the game initializes and reconnects after API
failures. Profile/transport failures discard the old address and dialog cache;
the next attempt performs a fresh memory search. Retries are limited to once per
second while unavailable. Invalid text in a valid segment does not cause an
expensive rediscovery loop.

## Useful play sequence

1. Enter a dialog with several choices and pause for a few seconds.
2. Hover ordinary choices, then saint/potion choices that produce popups.
3. Move into the popup, back to its owner row, and completely outside it. Pause
   about three seconds at each point so the periodic frame recorder sees it.
4. Select a choice and continue through the next dialog.
5. Open and close another menu, enter/leave a different game screen, and load a
   save if convenient. Please note what you expected to hear and what was visible.
6. For a separate late-start test, attach the recorder while a popup is already
   open. That tests the explicitly unsupported case where no parent was captured.

Play naturally as well; no special game save or prolonged session is required.
Do not rely on these candidate options as an authoritative list of available
choices yet. The default recording mode is silent. Set `DARKTEXT_RAM_SPEAK_SELECTION=1`
with the launcher for [tested alchemist and learning-saints hover speech](ram-hover-findings.md).

Optional notes from another terminal, with the actual session directory:

```sh
python3 -m darktext.ram_mark /path/to/session "Closed potion popup; parent dialog still visible"
python3 -m darktext.ram_mark /path/to/session "Loaded saved game"
```

Notes receive UTC timestamps for alignment with the recorder. You can also write
an ordinary text description afterward and put it in the session directory.

## What is recorded

- `events.jsonl`: UTC and elapsed timestamps, raw buffer changes, monitored state
  fields, cache decisions, errors, recovery events, and snapshot filenames.
- `*.ds.bin.gz`: compressed 64 KiB data-segment snapshots.
- `*.ppm.gz`: compressed framebuffer images, requiring no OCR or image library.
- `notes.jsonl`: optional player annotations.
- `dosbox.log`: emulator output when using the launcher.

RAM is sampled about every 150 ms, plus capture overhead. Frames/data segments
are saved about every two seconds, and on stable transitions no more often than
once per half-second. RAM and frame requests are separate, so their contents may
straddle a transition. Polling can miss very short-lived buffers.

The recorder has a 256 MiB session budget by default (`--max-log-mb` on the Python
command). At the limit it emits a notice and continues tracking without writing
more diagnostics. The launcher's separate `dosbox.log` is outside that budget.
An unwritable/full disk ends recording with an error rather than pretending it
succeeded. Keep the session folder intact; paths in the manifest are relative.
Recordings contain game text, screenshots, and memory, so keep them out of the
source repository. A path to the local folder is enough for the next analysis.

## Current cache behavior

A stable decodable narrative updates a separate parent cache containing its
narrative and candidate options. The verified executable's MSG alternate-window
flag (`DS:EE41`) and saved-background handle (`DS:A776`) now identify the observed
recipe-popup lifecycle. `popup_opened` retains the parent and
`popup_buffer_changed` tracks auxiliary changes. `popup_closed` exposes the
captured parent with `cache_status: parent_restored`, even when the buffer still
contains popup text. Disagreeing signals produce `popup_transition_uncertain`.
See [the recording analysis](recipe-popup-findings.md) for evidence and scope.

Other undecodable streams remain `auxiliary_buffer` or `unsupported_buffer`;
their retained parent is unverified historical context. Those events alone do
not establish a visible popup. Returning to the cached narrative in the buffer
produces `dialog_buffer_restored` once.

The investigated fields `DS:08D6` and `DS:A88D..A892` act as conservative
invalidation signals. They are **not certified dialog/card identifiers**.
Changing them clears the parent, and unchanged old buffer bytes cannot refill it
until the buffer changes. Address relocation and connection loss also clear it.

Recipe and learning-saints popup closure are supported for a parent captured
earlier in the same uninterrupted context. Popup signals inherited across a
context change are ignored until a consistent closed state re-arms detection;
they cannot suppress a new narrative. Other popup families, startup with an already-stale
buffer, and hidden-option filtering remain unresolved. Recordings still help us
extend coverage. Narrative auto-reading remains disabled; opt-in selection speech
is available on the tested alchemist screens and learning-saints popup.
