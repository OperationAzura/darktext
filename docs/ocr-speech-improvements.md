# OCR and speech reliability review

Branch: `improve-ocr-speech-reliability`.

## Implemented improvements

| Finding | Change | Expected benefit / tradeoff |
| --- | --- | --- |
| Reusing a cancellation event lets an older speech pump observe a newer utterance's state. Failed synthesis can leave partial audio in the cache. | Give each utterance its own event; cache only complete successful synthesis and playback; retain child-process ownership until completion. | Prevent interrupted or failed audio from becoming a persistent truncated option. |
| Every voice is played at 22,050 Hz. A 160,000-byte cache limit excludes normal options longer than about 3.6 seconds. | Read `audio.sample_rate` from the voice's `.onnx.json`; preserve that rate in playback and WAV headers; validate WAV structure and allow up to 30 seconds. | Correct speed/pitch for other voice rates; fewer repeated synthesizations. Missing metadata retains the legacy rate; malformed metadata fails explicitly. |
| A claimed background job stops being deduplicated before it finishes. Queue cancellation can miss a job between dequeue and launch. Concurrent writers share a temporary filename. | Keep jobs pending through completion, invalidate old queue generations, coordinate launch with cancellation, and use unique temporary WAV files with atomic replacement. | Avoid duplicate background jobs, cancelled work starting later, and competing writes corrupting audio. |
| The hover loop recalculates temporal consensus and regions despite no new OCR observation. | Cache both until the next observation, returning copies to callers. | Less CPU work in the latency-sensitive hover loop. |
| Two nearby option starts can attach to the same slot in one frame. Similar row positions can identify unrelated menus as the same dialog. | Match each observed start to a distinct slot and require text agreement when both dialogs contain recognized options. | Preserve closely spaced choices and avoid carrying old option text into a different menu. |
| Delayed OCR and one-frame highlights can launch obsolete speech. Small screen edits can escape the change threshold. Workers have no explicit shutdown path. | Compare OCR's captured signature against the current frame, refresh stale results, suppress hover on unsettled scenes, require 40 ms of highlight dwell, refresh OCR at least every three seconds when idle, and stop workers on daemon shutdown/SIGTERM. | Fewer stale selections and accidental interruptions. Dwell adds approximately 40 ms before a stable selection is announced; periodic OCR adds bounded background work. |

Raw PCM is forwarded as it becomes available using `read1` and a flushed audio
pipe, instead of waiting to fill a 4096-byte read. Completion includes final
cache writes, so one-shot commands do not exit while the cache is being saved.

## Validation

The regression suite exercises cancellation during live speech and between
queue dequeue and process launch, duplicate inflight jobs, simultaneous cache
writers, long 16 kHz WAV persistence, failed Piper output, invalid voice metadata,
closely spaced menu rows, unrelated menus with identical geometry, highlight
flicker, stale OCR rejection, and worker cleanup. Fake executable processes make
failure/interruption cases reproducible without an audio device. The existing
opt-in real Piper/playback tests also pass locally.

A local synthetic ten-option benchmark, with three OCR hypotheses per option,
measured 500 unchanged `texts()` / `regions()` reads after warm-up:

- Main branch: approximately 2.44 ms per iteration.
- This branch: approximately 0.0003 ms per iteration.

These numbers measure consensus/region lookup only, not OCR, HTTP capture, or
end-to-end time to speech. They are not a claim of equivalent overall speedup.

## Further candidates requiring captured-screen evaluation

1. **Screen-specific text regions:** the fixed story rectangle is unsuitable for
   some title, save/load, and character screens. Collect labelled examples and
   compare region detection before enabling automatic crops.
2. **Blue-text normalization and font-specific preprocessing:** test original,
   normalized-highlight, and contrast-adjusted crops against transcribed game
   text. Extra OCR passes should be conditional on low confidence.
3. **Stable narrative announcements:** animated logos and scene transitions can
   still produce noisy fragments. Evaluate a short confirmation window while
   measuring the added delay for genuine story text.
4. **Persistent Piper model workers:** retaining loaded models could reduce cold
   synthesis latency, but needs bounded memory use, cancellation, and voice-switch
   tests. Current work still launches Piper for uncached utterances.
5. **Captured regression corpus and end-to-end latency measurements:** track word
   error rate, option-boundary errors, first-audio latency, and interrupted/repeated
   announcements across representative gameplay. Synthetic regressions do not
   establish a measured reduction in OCR word error rate.
