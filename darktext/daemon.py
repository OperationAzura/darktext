"""Main background daemon for monitoring Darklands screen and speaking text."""

import re
import sys
import time

from .audio_cache import OptionAudioCache
from .capture import capture_logical_game
from .config import (
    FAST_HOVER_POLL,
    FOLLOWUPS,
    SCENE_CHANGE_RATIO,
    SCENE_CHECK_INTERVAL,
    TEMPORAL_DIALOG_HARD_CHANGE,
)
from .fast_detector import (
    FastOcrWorker,
    fast_change_ratio,
    fast_selected_index,
    fast_signature,
)
from .ocr import build_engine, equivalent, extract_state
from .speaker import CachedWavPlayer, DirectLiveSpeaker, get_voice_for_option
from .temporal import TemporalOptionBuilder


def log(msg: str):
    """Output timestamped diagnostic log message."""
    ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    print(f"[{ts}] darktext: {msg}", flush=True)


def _norm_narrative(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def cmd_daemon(args) -> int:
    """Run the 100Hz hover-detection accessibility daemon."""
    log("initializing OCR engine and audio cache...")
    engine = build_engine()
    audio_cache = OptionAudioCache()
    speaker = DirectLiveSpeaker(audio_cache=audio_cache)
    wav_player = CachedWavPlayer()
    worker = FastOcrWorker(engine)
    builder = TemporalOptionBuilder()

    log(
        f"active with persistent audio cache; hover poll={FAST_HOVER_POLL:.3f}s; "
        f"multi-worker pre-rendering enabled"
    )

    try:
        frame = capture_logical_game()
        state = extract_state(engine, frame)
        base_sig = fast_signature(frame)
    except Exception as exc:
        log(f"initial capture error: {exc}")
        frame, state, base_sig = None, None, None

    if state is not None and state.options:
        builder.update(state)
        audio_cache.queue_options(builder.texts(), get_voice_for_option)

    last_narrative: str | None = None
    if state is not None and not state.options and state.narrative.strip():
        last_narrative = _norm_narrative(state.narrative)
        speaker.speak(state.narrative.strip())

    active_hover_slot: int | None = None
    unhover_start: float = 0.0
    last_scene_check = 0.0
    ocr_pending = False
    refresh_after_pending = False

    now = time.monotonic()
    followup_due = [now + x for x in FOLLOWUPS] if state is not None else []

    def queue_ocr(current_frame, reason):
        nonlocal ocr_pending
        sig = fast_signature(current_frame)
        if worker.request(current_frame, sig, reason):
            ocr_pending = True
            return True
        return False

    try:
        while True:
            loop_start = time.monotonic()

            try:
                frame = capture_logical_game()
            except Exception as exc:
                log(f"framebuffer error: {exc}")
                time.sleep(0.05)
                continue

            now = time.monotonic()

            # 1. Process incoming background OCR results
            result = worker.latest()
            if result is not None:
                new_state, new_sig, reason, began, ended, error = result
                ocr_pending = False

                if error is not None:
                    log(f"background OCR error: {error}")
                elif new_state is not None:
                    visual_change = fast_change_ratio(base_sig, new_sig)
                    menu_evidence = bool(
                        new_state.options
                        or any(line.option_start for line in new_state.raw_lines)
                    )

                    same_dialog = False
                    if state is not None:
                        same_dialog = equivalent(state, new_state)
                        if not same_dialog and builder.slots:
                            if menu_evidence:
                                same_dialog = builder.compatible(new_state, visual_change)
                            else:
                                same_dialog = visual_change < TEMPORAL_DIALOG_HARD_CHANGE

                    if state is None or not same_dialog:
                        # Dialog transition / new screen
                        state = new_state
                        base_sig = new_sig
                        builder = TemporalOptionBuilder()
                        active_hover_slot = None
                        unhover_start = 0.0

                        speaker.stop()
                        wav_player.stop()
                        audio_cache.clear_queue()

                        if menu_evidence:
                            builder.update(state)
                            texts_now = builder.texts()
                            audio_cache.queue_options(texts_now, get_voice_for_option)
                            log(
                                f"new menu; {len(builder.slots)} slots; "
                                f"OCR {ended - began:.3f}s; pre-rendering queued"
                            )
                        elif state.narrative.strip():
                            key = _norm_narrative(state.narrative)
                            if key != last_narrative:
                                last_narrative = key
                                speaker.speak(state.narrative.strip())
                                log(f"narrative: {state.narrative.strip()[:80]}...")

                        followup_due = [now + x for x in FOLLOWUPS]

                    else:
                        # Existing dialog continued / updated
                        state = new_state
                        base_sig = new_sig

                        if builder.slots or menu_evidence:
                            before = builder.texts()
                            builder.update(state)
                            after = builder.texts()

                            if after != before:
                                audio_cache.queue_options(
                                    after,
                                    get_voice_for_option,
                                    selected_index=active_hover_slot,
                                )
                                log("option texts refined; updated audio queued")
                                # DO NOT stop ongoing audio! User is still listening.

                if refresh_after_pending and not ocr_pending:
                    refresh_after_pending = False
                    queue_ocr(frame, "deferred-selection-refresh")

            # 2. Fast hover detection (runs every 10ms)
            texts = builder.texts()
            regions = builder.regions()
            selected: int | None = None

            if texts and regions and len(texts) == len(regions):
                selected = fast_selected_index(frame, regions)
                if selected is not None and not (0 <= selected < len(texts)):
                    selected = None

            if selected is not None:
                unhover_start = 0.0
                # Trigger when entering a new option, or re-hovering after unhover
                if selected != active_hover_slot:
                    active_hover_slot = selected
                    text = texts[selected].strip()

                    if text:
                        detected_at = time.monotonic()
                        speaker.stop()
                        wav_player.stop()

                        voice_model = get_voice_for_option(selected)
                        voice_name = voice_model.stem if voice_model else None

                        # Check persistent cache (exact or fuzzy match)
                        cached, match_info, _sim = audio_cache.get_cached_wav_with_info(
                            text, voice_name
                        )

                        if cached is not None and wav_player.play(cached):
                            launch_ms = (time.monotonic() - detected_at) * 1000.0
                            log(f"selected [{match_info}, launch={launch_ms:.1f}ms]: {text}")
                        else:
                            # Live streaming synthesis with simultaneous caching
                            speaker.speak_option(text, voice_model, cache_on_finish=True)
                            launch_ms = (time.monotonic() - detected_at) * 1000.0
                            log(
                                f"selected [live-synthesizing, launch-call={launch_ms:.1f}ms]: "
                                f"{text}"
                            )

                        # Update pre-rendering priorities around new selection
                        audio_cache.queue_options(
                            texts, get_voice_for_option, selected_index=selected
                        )

                        if not ocr_pending:
                            queue_ocr(frame, "selection-change")
                        else:
                            refresh_after_pending = True

            else:
                # No option hovered currently
                if active_hover_slot is not None:
                    if unhover_start == 0.0:
                        unhover_start = now
                    elif now - unhover_start >= 0.120:  # 120ms debounce
                        active_hover_slot = None
                        unhover_start = 0.0

            # 3. Staged followup OCR passes to repair partially obscured lines
            if not ocr_pending and followup_due and now >= followup_due[0]:
                due = followup_due.pop(0)
                queue_ocr(frame, f"staged-cleanup:{due:.3f}")

            # 4. Periodic scene change check
            if now - last_scene_check >= SCENE_CHECK_INTERVAL:
                last_scene_check = now
                sig = fast_signature(frame)
                ratio = fast_change_ratio(base_sig, sig)
                if ratio >= SCENE_CHANGE_RATIO and not ocr_pending:
                    queue_ocr(frame, f"scene-change:{ratio:.3f}")

            # 5. Rate limit loop to FAST_HOVER_POLL
            elapsed = time.monotonic() - loop_start
            if elapsed < FAST_HOVER_POLL:
                time.sleep(FAST_HOVER_POLL - elapsed)

    except KeyboardInterrupt:
        return 0
    finally:
        audio_cache.stop()
        wav_player.stop()
        speaker.stop()
