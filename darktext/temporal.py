"""Temporal multi-frame option builder and n-gram consensus engine."""

from difflib import SequenceMatcher
import re
import time

from .config import (
    TEMPORAL_DIALOG_HARD_CHANGE,
    TEMPORAL_LINE_Y_TOL,
    TEMPORAL_MAX_SAMPLES,
    TEMPORAL_OPTION_Y_TOL,
)
from .models import Line, ScreenState


def _temporal_tokens(text: str) -> list[str]:
    return re.findall(
        r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*|[.,;:!?()]",
        (text or "").strip(),
    )


def _temporal_norm_tokens(text: str) -> list[str]:
    return [x.lower() for x in _temporal_tokens(text) if re.search(r"[A-Za-z0-9]", x)]


def _temporal_join_tokens(tokens: list[str]) -> str:
    out = ""
    no_space_before = {".", ",", ";", ":", "!", "?", ")"}
    no_space_after = {"("}
    for token in tokens:
        if not out:
            out = token
        elif token in no_space_before:
            out += token
        elif out[-1:] in no_space_after:
            out += token
        else:
            out += " " + token
    return out.strip()


def _temporal_sample_key(text: str) -> str:
    return " ".join(_temporal_norm_tokens(text))


def _temporal_text_quality(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    alpha = sum(ch.isalpha() for ch in text)
    weird = sum(
        not (ch.isalnum() or ch.isspace() or ch in ".,;:'\"!?()-")
        for ch in text
    )
    words = len(_temporal_norm_tokens(text))
    n = max(1, len(text))
    return max(
        0.0,
        min(
            1.0,
            0.45 * (alpha / n)
            + 0.35 * min(words / 12.0, 1.0)
            + 0.20 * (1.0 - min(weird / n * 5.0, 1.0)),
        ),
    )


def _temporal_ngram_set(text: str, n: int = 2) -> set[tuple[str, ...]]:
    words = _temporal_norm_tokens(text)
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _temporal_similarity(a: str, b: str) -> float:
    aa = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
    bb = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
    if not aa or not bb:
        return 0.0
    seq = SequenceMatcher(None, aa, bb).ratio()
    aw = set(aa.split())
    bw = set(bb.split())
    if not aw or not bw:
        return seq
    overlap = len(aw & bw) / len(aw | bw)
    return 0.50 * seq + 0.50 * overlap


def _temporal_best_sample(samples: list[dict]) -> str:
    """Pick the best complete observed hypothesis for one physical text line."""
    if not samples:
        return ""

    dedup = {}
    for sample in samples:
        text = sample["text"].strip()
        key = _temporal_sample_key(text)
        if not key:
            continue
        old = dedup.get(key)
        if old is None or sample["ocr"] > old["ocr"]:
            dedup[key] = sample

    candidates = list(dedup.values())
    if not candidates:
        return ""

    bigram_support: dict[tuple[str, ...], int] = {}
    trigram_support: dict[tuple[str, ...], int] = {}
    for candidate in candidates:
        for gram in _temporal_ngram_set(candidate["text"], 2):
            bigram_support[gram] = bigram_support.get(gram, 0) + 1
        for gram in _temporal_ngram_set(candidate["text"], 3):
            trigram_support[gram] = trigram_support.get(gram, 0) + 1

    def score(candidate):
        text = candidate["text"]
        words = _temporal_norm_tokens(text)
        if not words:
            return -999.0

        if len(candidates) == 1:
            consensus = 1.0
        else:
            consensus = sum(
                _temporal_similarity(text, other["text"])
                for other in candidates
                if other is not candidate
            ) / (len(candidates) - 1)

        bgs = _temporal_ngram_set(text, 2)
        tgs = _temporal_ngram_set(text, 3)
        b_support = (
            sum(bigram_support[g] for g in bgs) / max(1, len(bgs) * len(candidates))
            if bgs
            else 0.0
        )
        t_support = (
            sum(trigram_support[g] for g in tgs) / max(1, len(tgs) * len(candidates))
            if tgs
            else 0.0
        )

        completeness = min(len(words) / 14.0, 1.0)
        quality = _temporal_text_quality(text)
        ocr = max(0.0, min(1.0, float(candidate["ocr"])))

        return (
            0.28 * ocr
            + 0.24 * consensus
            + 0.16 * b_support
            + 0.10 * t_support
            + 0.14 * completeness
            + 0.08 * quality
        )

    return max(candidates, key=score)["text"].strip()


def _temporal_clean_line(line: Line) -> str:
    text = (line.text or "").strip()
    if line.option_start:
        text = re.sub(r"^\s*(?:\.{1,}|…|[·•]{1,})\s*", "", text).strip()
    return text


class TemporalOptionBuilder:
    """Persistent dialog model keyed by vertical geometry.
    
    Handles multi-line menu choices and repairs cursor-obscured characters
    across multiple video frames.
    """

    def __init__(self):
        self.slots: list[dict] = []
        self.next_id = 1
        self.observation_count = 0

    def _new_option_slot(self, start_y: float) -> dict:
        slot = {
            "id": self.next_id,
            "start_y": float(start_y),
            "lines": [],
        }
        self.next_id += 1
        self.slots.append(slot)
        self.slots.sort(key=lambda x: x["start_y"])
        return slot

    def _nearest_option(self, start_y: float) -> dict | None:
        if not self.slots:
            return None
        slot = min(self.slots, key=lambda x: abs(x["start_y"] - start_y))
        if abs(slot["start_y"] - start_y) <= TEMPORAL_OPTION_Y_TOL:
            return slot
        return None

    def _discover_option_starts(self, state: ScreenState):
        previous = None
        options_started = False

        for line in sorted(state.raw_lines, key=lambda item: (item.y1, item.x1)):
            # Darklands option rows sometimes lose their three-dot marker to
            # OCR or cursor overlap. Once a menu has started, a line following
            # terminal punctuation is therefore also a likely option start.
            inferred_start = bool(
                options_started
                and previous is not None
                and previous.text.rstrip().endswith((".", "!", "?", ":"))
            )

            if not line.option_start and not inferred_start:
                previous = line
                continue

            options_started = True
            y = (float(line.y1) + float(line.y2)) / 2.0
            slot = self._nearest_option(y)
            if slot is None:
                self._new_option_slot(y)
            else:
                slot["start_y"] = 0.82 * slot["start_y"] + 0.18 * y
            previous = line
        self.slots.sort(key=lambda x: x["start_y"])
        self._rebalance_lines()

    def _rebalance_lines(self):
        """Move lines if a previously hidden option start is discovered."""
        if len(self.slots) < 2:
            return
        all_lines = []
        for slot in self.slots:
            all_lines.extend(slot["lines"])
            slot["lines"] = []
        for line in all_lines:
            target = self._option_for_line_y(line["y"])
            if target is not None:
                target["lines"].append(line)
        for slot in self.slots:
            slot["lines"].sort(key=lambda x: x["y"])

    def _option_for_line_y(self, y: float) -> dict | None:
        if not self.slots:
            return None
        starts = [x["start_y"] for x in self.slots]
        if y < starts[0] - 5:
            return None
        chosen = self.slots[0]
        for slot in self.slots:
            if y >= slot["start_y"] - 5:
                chosen = slot
            else:
                break
        # In Darklands, menu choices span at most 2 physical lines (~24px).
        # If a line is farther down, it cannot belong to this option.
        if y - chosen["start_y"] > 24.0:
            nearby = self._nearest_option(y)
            if nearby is not None and abs(nearby["start_y"] - y) <= 12.0:
                return nearby
            return self._new_option_slot(y)
        return chosen

    def _line_slot(self, option: dict, y: float) -> dict:
        if option["lines"]:
            line = min(option["lines"], key=lambda x: abs(x["y"] - y))
            if abs(line["y"] - y) <= TEMPORAL_LINE_Y_TOL:
                return line

        line = {
            "y": float(y),
            "x1": 10**9,
            "y1": 10**9,
            "x2": 0.0,
            "y2": 0.0,
            "samples": [],
        }
        option["lines"].append(line)
        option["lines"].sort(key=lambda x: x["y"])
        return line

    def update(self, state: ScreenState | None):
        if state is None:
            return

        self.observation_count += 1
        self._discover_option_starts(state)
        if not self.slots:
            return

        now = time.monotonic()

        for raw in state.raw_lines:
            y = (float(raw.y1) + float(raw.y2)) / 2.0
            option = self._option_for_line_y(y)
            if option is None:
                continue

            text = _temporal_clean_line(raw)
            if not text:
                continue

            line = self._line_slot(option, y)
            line["y"] = 0.85 * line["y"] + 0.15 * y
            line["x1"] = min(line["x1"], float(raw.x1))
            line["y1"] = min(line["y1"], float(raw.y1))
            line["x2"] = max(line["x2"], float(raw.x2))
            line["y2"] = max(line["y2"], float(raw.y2))

            sample = {
                "text": text,
                "ocr": max(0.0, min(1.0, float(raw.score))),
                "when": now,
            }

            key = _temporal_sample_key(text)
            replaced = False
            for i, old in enumerate(line["samples"]):
                if _temporal_sample_key(old["text"]) == key:
                    if sample["ocr"] >= old["ocr"]:
                        line["samples"][i] = sample
                    replaced = True
                    break
            if not replaced:
                line["samples"].append(sample)

            line["samples"] = sorted(
                line["samples"],
                key=lambda x: (x["when"], x["ocr"]),
                reverse=True,
            )[:TEMPORAL_MAX_SAMPLES]

    def texts(self) -> list[str]:
        out = []
        for option in sorted(self.slots, key=lambda x: x["start_y"]):
            parts = []
            for line in sorted(option["lines"], key=lambda x: x["y"]):
                best = _temporal_best_sample(line["samples"])
                if best:
                    parts.append(best)
            out.append(re.sub(r"\s+", " ", " ".join(parts)).strip())
        return out

    def ids(self) -> list[int]:
        return [x["id"] for x in sorted(self.slots, key=lambda x: x["start_y"])]

    def regions(self) -> list[tuple[int, int, int, int]]:
        ordered = sorted(self.slots, key=lambda x: x["start_y"])
        out = []
        for i, option in enumerate(ordered):
            lines = option["lines"]
            if lines:
                x1 = max(0, int(min(x["x1"] for x in lines)) - 7)
                x2 = int(max(x["x2"] for x in lines)) + 9
                y1 = max(0, int(option["start_y"] - 7))
                observed_bottom = int(max(x["y2"] for x in lines)) + 7
            else:
                x1, x2 = 0, 470
                y1 = max(0, int(option["start_y"] - 7))
                observed_bottom = y1 + 24

            if i + 1 < len(ordered):
                y2 = max(y1 + 6, int(ordered[i + 1]["start_y"] - 6))
            else:
                y2 = observed_bottom + 8

            out.append((x1, y1, x2, y2))
        return out

    def compatible(self, new_state: ScreenState | None, visual_change: float) -> bool:
        if not self.slots or new_state is None:
            return False

        starts = [
            (float(x.y1) + float(x.y2)) / 2.0
            for x in new_state.raw_lines
            if x.option_start
        ]

        geometry = 0.0
        if starts:
            matched = sum(
                1
                for y in starts
                if any(abs(slot["start_y"] - y) <= TEMPORAL_OPTION_Y_TOL for slot in self.slots)
            )
            geometry = matched / max(1, len(starts))

        built = [x for x in self.texts() if x]
        fresh = [x for x in new_state.options if x]
        text_score = 0.0
        if built and fresh:
            scores = []
            for text in fresh:
                scores.append(max(_temporal_similarity(text, old) for old in built))
            text_score = sum(scores) / len(scores)

        if visual_change >= TEMPORAL_DIALOG_HARD_CHANGE and text_score < 0.35:
            return False

        return geometry >= 0.55 or text_score >= 0.46
