"""Fast frame differencing, signature comparison, and hover detection."""

import queue
import threading
import time
import cv2
import numpy as np

from .geometry import FrameGeometry
from .ocr import extract_state
from .text_region import detect_text_region


def fast_signature(game: np.ndarray) -> np.ndarray:
    """Downsample the full native framebuffer for stable scene comparison."""
    if game.size == 0:
        return np.zeros((52, 96), dtype=np.uint8)
    frame = game.copy()
    b = frame[:, :, 0].astype(np.int16)
    g = frame[:, :, 1].astype(np.int16)
    r = frame[:, :, 2].astype(np.int16)
    blue = (b >= 90) & (b >= r * 1.40) & (b >= g * 1.22) & ((b - r) >= 32)
    frame[blue] = (225, 225, 225)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (96, 52), interpolation=cv2.INTER_AREA)


def fast_change_ratio(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Return proportion of signature pixels that changed significantly."""
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


def _selected_from_absolute_regions(
    game: np.ndarray,
    regions: list[tuple[int, int, int, int]],
    geom: FrameGeometry,
) -> int | None:
    h, w = game.shape[:2]
    winner = None
    winner_score = 0.0
    winner_blue = 0
    min_blue = geom.area_px(6, minimum=2)
    strong_blue = geom.area_px(14, minimum=4)

    for i, (a, b, c, d) in enumerate(regions):
        a, c = max(0, min(w, a)), max(0, min(w, c))
        b, d = max(0, min(h, b)), max(0, min(h, d))
        if c <= a or d <= b:
            continue
        score, blue_n = fast_blue_score(game[b:d, a:c])
        if blue_n >= min_blue and (score >= 0.04 or blue_n >= strong_blue):
            if score > winner_score or (
                abs(score - winner_score) < 0.015 and blue_n > winner_blue
            ):
                winner, winner_score, winner_blue = i, score, blue_n
    return winner


def fast_selected_index(
    game: np.ndarray, regions: list[tuple[int, int, int, int]]
) -> int | None:
    """Find the highlighted option using native framebuffer regions.

    Current DarkText supplies absolute native coordinates. As a compatibility
    path for older callers that still provide crop-local regions, a miss is
    retried relative to the automatically detected text region. No fixed
    screen coordinates are used in either path.
    """
    if not regions:
        return None
    geom = FrameGeometry.from_frame(game)
    selected = _selected_from_absolute_regions(game, regions, geom)
    if selected is not None:
        return selected

    x1, y1, _, _ = detect_text_region(game)
    shifted = [(a + x1, b + y1, c + x1, d + y1) for a, b, c, d in regions]
    if shifted == regions:
        return None
    return _selected_from_absolute_regions(game, shifted, geom)


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
