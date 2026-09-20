# Recipe popup lifecycle: recording analysis

Analyzed the local gameplay session `20260920-135118` (approximately 190 seconds,
96 RAM/frame snapshot pairs). No game data or screenshots are included here.

The formula-purchase dialog appears around 147.5 seconds. Its dropdown was visibly
open in snapshots 79, 85–87, 89–90, and 92, and closed in 80–84, 88, 91, and 93–96.
The buffer changes to character headings and recipe rows at the first opening,
then stays in that form after closing. This confirms that buffer-only detection
cannot recover the displayed parent dialog in this case.

## Signals

For the executable hash already enforced by RamReader:

| Field | Open in observed snapshots | Closed in observed snapshots |
|---|---|---|
| `DS:EE41` byte | `1` | `0` |
| `DS:A776` word | nonzero saved-background handle | `0` |

These signals agree in all 18 classified post-opening recipe snapshots. They
also agree with the earlier wolf-encounter hover-popup snapshots, using a
different nonzero handle. The handle value itself must not be hardcoded.

Local executable disassembly supports this interpretation (offsets below are
relative to the MZ image with its header removed, not runtime CPU addresses):

- `0x8DA3A` stores a graphics-operation result in `DS:A776`.
- `0x8D253` sets `DS:EE41` to one after the alternate-window preparation calls.
- The path near `0x8D0D9` checks the flag, uses the saved handle, and performs
  restoration/cleanup calls. `0x8D140` clears the handle and `0x8D146` clears the flag.
- Another exit path clears the same fields around `0x8D43C` and `0x8D450`.

This is evidence for this MSG alternate-window mechanism, not a universal flag
for every menu, modal dialog, inventory window, or combat screen.

## Implemented behavior

The stability key now includes popup state as well as buffer content and context.
Previously, unchanged text meant that popup closure never reached the cache.
Two consistent samples are still required. Both fields must agree; inconsistent
pairs produce `popup_transition_uncertain` without restoring or replacing text.

- `popup_opened`: preserve the captured parent narrative and candidate options;
  never parse the popup's buffer as a new parent, even if it resembles prose.
- `popup_buffer_changed`: record changes while that alternate window is open.
- `popup_closed`: when the same context remains and the buffer is still a known
  popup buffer (or already equals the parent), expose `cached_dialog` with
  `cache_status: parent_restored`. The shared RAM buffer is never written to.
- If a genuinely different decodable narrative arrives at closure, use that
  narrative rather than restoring the previous dialog.
- Starting during a popup without a captured parent produces an empty cache on
  closure. Context changes and disconnects still invalidate the parent.

Offline replay of all 96 snapshots produces four paired openings/closures:
153.5/155.5, 165.8/172.0, 174.0/178.1, and 180.2/182.3 seconds, matching the screenshots.
Replay feeds each snapshot twice to exercise the production stability gate;
this verifies recorded states, not the timing or coverage of a fresh live run.
Synthetic tests cover repeated closure with unchanged bytes, late attachment,
prose-like popup content, inconsistent signals, owner changes, and new dialog on
closure. The full suite has 56 passing tests, including audio tests.

The recorder now includes these signal values and popup state on raw-change
records (schema 2), allowing future recordings to resolve shorter transitions
than the periodic screenshots. Automatic speech and selectable-row filtering
remain disabled/unverified respectively. A retained parent is identified, but
its candidate option list is not thereby proven to match visible enabled rows.

## Learning-saints follow-up

Session `20260920-140443` contains 60 snapshot pairs over approximately 109
seconds. The learning-saints dropdown uses the same flag/handle mechanism.
Snapshots 46–47 and 49–52 show it open; 48 and 53 show it closed. The recorded
reader restored the library parent at 87.4 and 96.5 seconds. A third opening
occurs around 97.5 seconds; selection leads to a time-passage overlay and then
a different game context rather than another simple cancellation.

This revealed a lifecycle constraint: popup fields can outlive their owner.
At snapshot 58, the monitored context changes from `360013000000` to
`390036000000` while the flag and handle still indicate open. At snapshot 59,
the new church narrative is already in RAM and on screen, but those fields are
still set. Snapshot 60 has flag zero with a nonzero handle. Previously, those
residual fields suppressed the new narrative.

The cache now disregards inherited popup signals after a context change until
both fields consistently report closed. It still invalidates the old parent and
blocks unchanged old text; a changed, valid narrative in the new context can be
captured. Events identify these signals as `popup_scope: previous_context`.
Starting the reader during an open popup remains conservative and does not
invent a parent. A fresh closed state re-arms ordinary popup detection.

Replay of both recordings retains all four recipe open/close pairs and the two
saints cancellations, while capturing the church narrative at 104.9 seconds.
The third saints selection no longer produces a spurious popup opening under
the new owner. The updated suite has 57 passing tests, including audio tests.
No claim is made here that the saint-learning action's gameplay result, selected
row, or all intermediate messages have been decoded.
