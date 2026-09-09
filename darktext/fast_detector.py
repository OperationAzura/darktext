"""Fast frame differencing, signature comparison, and hover detection."""

import queue
import threading
import time
import cv2
import numpy as np

from .config import TEXT_RECT
from .ocr import extract_state
from .models import ScreenState


def fast_signature(game: np.ndarray) -> np.ndarray:
    """Downsample story pane to 96x52 grayscale with blue text masked to neutral."""
    x1, y1, x2, y2 = TEXT_RECT
    story = game[y1:y2, x1:x2].copy()
    if story.size == 0:
        return np.zeros((52, 96), dtype=np.uint8)
    b = story[:, :, 0].astype(np.int16)
    g = story[:, :, 1].astype(np.int16)
    r = story[:, :, 2].astype(np.int16)
    blue = (b >= 90) & (b >= r * 1.40) & (b >= g * 1.22) & ((b - r) >= 32)
    story[blue] = (225, 225, 225)
    gray = cv2.cvtColor(story, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (96, 52), interpolation=cv2.INTER_AREA)


def fast_change_ratio(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Return proportion of pixels that changed significantly between signatures."""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.mean(cv2.absdiff(a, b) >= 26))


def fast_blue_score(reg: np.ndarray) -> tuple[float, int]:
    """Calculate blue pixel ratio and total blue count for a region."""
    if reg.size == 0:
        return 0.0, 0
    b = reg[:, :, 0].astype(np.int16)
    g = reg[:, :, 1].astype(np.int16)
    r = reg[:, :, 2].astype(np.int16)
    blue = (b >= 95) & (b >= r * 1.45) & (b >= g * 1.28) & ((b - r) >= 38)
    bright = (r >= 155) & (g >= 155) & (b >= 155)
    bn = int(blue.sum())
    wn = int(bright.sum())
    return (bn / max(1.0, float(bn + wn))), bn


def fast_selected_index(
    game: np.ndarray, regions: list[tuple[int, int, int, int]]
) -> int | None:
    """Find which option region currently has blue highlight."""
    if not regions:
        return None
    x1, y1, x2, y2 = TEXT_RECT
    story = game[y1:y2, x1:x2]
    h, w = story.shape[:2]
    winner = None
    winner_score = 0.0
    winner_blue = 0
    for i, (a, b, c, d) in enumerate(regions):
        a, c = max(0, min(w, a)), max(0, min(w, c))
        b, d = max(0, min(h, b)), max(0, min(h, d))
        if c <= a or d <= b:
            continue
        score, blue_n = fast_blue_score(story[b:d, a:c])
        if blue_n >= 6 and (score >= 0.04 or blue_n >= 14):
            if score > winner_score or (abs(score - winner_score) < 0.015 and blue_n > winner_blue):
                winner, winner_score, winner_blue = i, score, blue_n
    return winner


class FastOcrWorker:
    """Background worker executing full OCR requests without blocking the 100Hz loop."""

    def __init__(self, engine):
        self.engine = engine
        self.inbox = queue.Queue(maxsize=1)
        self.outbox = queue.Queue(maxsize=2)
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, frame: np.ndarray, signature: np.ndarray, reason: str) -> bool:
        item = (frame.copy(), signature.copy(), reason, time.monotonic())
        try:
            self.inbox.put_nowait(item)
            return True
        except queue.Full:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                pass
            try:
                self.inbox.put_nowait(item)
                return True
            except queue.Full:
                return False

    def latest(self):
        newest = None
        while True:
            try:
                newest = self.outbox.get_nowait()
            except queue.Empty:
                return newest

    def _run(self):
        while True:
            frame, sig, reason, started = self.inbox.get()
            try:
                state = extract_state(self.engine, frame)
                item = (state, sig, reason, started, time.monotonic(), None)
            except Exception as exc:
                item = (None, sig, reason, started, time.monotonic(), exc)
            try:
                self.outbox.put_nowait(item)
            except queue.Full:
                try:
                    self.outbox.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.outbox.put_nowait(item)
                except queue.Full:
                    pass
