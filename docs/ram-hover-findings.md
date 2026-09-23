# RAM hover tracking and speech

The `20260920-154504` recording contains 149 snapshot pairs. It captures the
alchemist's main menu in both directions, then the four-character formula list.
The RAM reader now emits `selection_changed` and `selection_cleared` events and
supports opt-in speech for these screens, without OCR. The recorded learning-saints
popup is now supported as well (see the follow-up below).

## Run with hover speech

```sh
DARKTEXT_RAM_SPEAK_SELECTION=1 ./scripts/record-ram-session.sh
```

The launcher uses the installed Piper and voice directories unless overridden by
`DARKTEXT_PIPER_BIN` / `DARKTEXT_VOICE_DIR`. The normal recording command remains
silent. To attach to an already-running game with speech configured:

```sh
python3 -m darktext.ram_text --exe /path/to/darkland.exe --watch --speak-selection
```

Coverage is deliberately limited to the tested executable, the alchemist
menu (owner `0x1A`), formula-purchase dialog (owner `0x59`), and learning-saints
popup (owner `0x36`, alternate input mode only). Other menus remain silent. Formula speech includes the character's name, so identical formulas
under different characters do not sound like the same selection. Character
headings, blank areas, invalid rows, and unsupported buffers do not speak.

Two matching hover samples are required. Movement to a different row or outside
the menu stops the current utterance immediately on the next poll. Returning to
the same option reads it again. Polling remains approximately 150 ms plus capture
and speech-start overhead; this is not yet the faster hover loop used by OCR.
Narrative auto-reading remains off.

## Recovered fields

All addresses are offsets within the automatically located data segment:

| Offset | Observed role |
|---|---|
| `E96E` byte | Number of published rows in active table (maximum 20) |
| `E96F` byte | Highlighted published-row index; `FF` means none |
| `E9B0 + row` byte | Published-row to raw owner-option ordinal |
| `E99C + raw ordinal` byte | Row status; only observed selectable value `1` is accepted |
| `EE42` byte | Active table: `0` primary, `1` alternate popup |

The highlight index alone is insufficient. In the main alchemist menu, visible
row 5 maps to raw ordinal 9: hidden tasks and filler entries precede the leave
option. The reader preserves raw text option positions, including empty slots,
and uses the table mapping instead of compact-list indexing.

Popup visibility and active table are different. The popup can remain visible
while the pointer is over its primary owner row. Disassembly around MZ-image
`0x8D2D4` copies the primary table into `DS:A778`, installs the alternate table at
`DS:E96E`, clears the highlight, and sets `DS:EE42=1`. The return path near
`0x8D347` restores the primary table and clears the mode. The input path near
`0x8D522` maps the row through `E9B0` and divides the ordinal by four to separate
character and item indices. This matches the recorded four formula slots per
character. No hardcoded character names or formula strings are required.

The strict formula parser accepts the observed character-heading and row control
format, preserving character slot, formula slot, and text. Unknown control
formats fail closed. Supported slots are never deduplicated by formula name.
Main-menu speech additionally requires a stable, usable parent cache; arbitrary
unsupported scratch text cannot authorize speaking an old parent selection.

## Validation and remaining scope

Offline replay maps all six main-menu choices and all 16 formula/character rows.
The ascending and descending passes agree, and character headings produce no
selection. Screenshot/RAM captures are separate requests, so boundary frames may
straddle a change. Replay validates recorded states, not a live end-to-end speech
session. A synthetic phrase completed through the installed Piper pipeline.
Synthetic tests cover hidden and empty slots, repeated formulas, input-table
switches, invalid/disabled rows, transient text, hover cancellation/revisit,
connection-loss cleanup, and speech dispatch.

Recordings use schema 3 and include the active row table and input mode whenever
those fields change, as well as decoded selection events. Existing narrative
records still label whole option lists as candidate/unverified; the separate
selection events carry the tested hover mapping. This does not certify other
owners, other saint selectors, inventory screens, or disabled-row semantics beyond
rejecting status values other than `1`.


## Learning-saints hover follow-up

Replay of `20260920-140443` provides two clear selected alternate rows:

| Snapshot | Published/raw row | Character slot | Item slot | Resolved speech |
|---|---|---|---|---|
| 52 | 5 | 1 | 1 | Udalrich: Saint Charity |
| 55 | 10 | 2 | 2 | Judith: Saint Engelbert |

Both match the visible highlights. The character-heading/four-item control
format is shared with the formula list. The decoder preserves the raw saint
label and expands only its `S.` prefix to `Saint` for speech; it does not guess
expansions of shortened names. Events use `source: saint_popup` and `saint_slot`,
with the same character slot/name fields as formula selections.

Primary library options remain unsupported for speech. Character headings with
no selected row, unknown control formats, and time-passage scratch text produce
no selection. This validates the two observed saint rows and the shared mapping,
not every possible saint or party configuration. Synthetic tests cover the
character/item mapping and rejection cases. All 69 tests pass; the earlier
alchemist replay retains its 22 distinct supported selections.
