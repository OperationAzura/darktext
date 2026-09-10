"""Automatic text-region discovery for native Darklands framebuffer images."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import FrameGeometry


Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class TextFragment:
    """One horizontal text-like component in native framebuffer coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int
    ink: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


def clamp_rect(rect: Rect, width: int, height: int) -> Rect:
    """Clamp a rectangle to an image while keeping it non-empty."""
    x1, y1, x2, y2 = (int(v) for v in rect)
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))
    return x1, y1, x2, y2


def text_pixel_mask(frame: np.ndarray) -> np.ndarray:
    """Return a cheap mask for Darklands' bright and blue text pixels."""
    if frame.size == 0:
        return np.zeros(frame.shape[:2], dtype=np.uint8)

    b = frame[:, :, 0].astype(np.int16)
    g = frame[:, :, 1].astype(np.int16)
    r = frame[:, :, 2].astype(np.int16)

    # Normal dialog text is bright/near-neutral. Selected menu text is blue.
    bright = (r >= 150) & (g >= 150) & (b >= 150)
    blue = (b >= 90) & (b >= r * 1.35) & (b >= g * 1.20) & ((b - r) >= 32)
    return ((bright | blue).astype(np.uint8) * 255)


def _line_fragments(frame: np.ndarray, geom: FrameGeometry) -> list[TextFragment]:
    """Find horizontal text-like runs without assuming where the dialog lives."""
    mask = text_pixel_mask(frame)

    # Join neighboring glyphs into word/line fragments. This is deliberately
    # much cheaper than OCR and runs on the untouched native framebuffer.
    kernel_w = geom.x_px(9, minimum=3)
    kernel_h = geom.y_px(2, minimum=1)
    kernel = np.ones((kernel_h, kernel_w), dtype=np.uint8)
    joined = cv2.dilate(mask, kernel, iterations=1)

    count, _, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
    min_width = geom.x_px(16, minimum=4)
    min_height = geom.y_px(3, minimum=2)
    max_height = geom.y_px(24, minimum=6)
    min_ink = geom.area_px(8, minimum=3)

    out: list[TextFragment] = []
    for idx in range(1, count):
        x, y, w, h, _area = (int(v) for v in stats[idx])
        if w < min_width or h < min_height or h > max_height:
            continue
        ink = int(np.count_nonzero(mask[y : y + h, x : x + w]))
        if ink < min_ink:
            continue
        out.append(TextFragment(x, y, x + w, y + h, ink))
    return out


def _horizontal_overlap(a: TextFragment, b: TextFragment) -> float:
    overlap = max(0, min(a.x2, b.x2) - max(a.x1, b.x1))
    return overlap / max(1.0, float(min(a.width, b.width)))


def _horizontal_gap(a: TextFragment, b: TextFragment) -> int:
    if a.x2 < b.x1:
        return b.x1 - a.x2
    if b.x2 < a.x1:
        return a.x1 - b.x2
    return 0


def _same_text_cluster(a: TextFragment, b: TextFragment, geom: FrameGeometry) -> bool:
    """Heuristic adjacency for fragments belonging to one text block."""
    dy = abs(a.cy - b.cy)

    # Separate fragments from the same physical row can have larger word gaps.
    if dy <= geom.y(6.0) and _horizontal_gap(a, b) <= geom.x_px(32, minimum=8):
        return True

    if dy > geom.y(30.0):
        return False

    overlap = _horizontal_overlap(a, b)
    left_aligned = abs(a.x1 - b.x1) <= geom.x(56.0)
    center_near = abs(a.cx - b.cx) <= geom.x(150.0)
    return overlap >= 0.12 or left_aligned or center_near


def _clusters(
    fragments: list[TextFragment], geom: FrameGeometry
) -> list[list[TextFragment]]:
    if not fragments:
        return []

    parent = list(range(len(fragments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ordered = sorted(range(len(fragments)), key=lambda i: fragments[i].cy)
    for pos, i in enumerate(ordered):
        a = fragments[i]
        for j in ordered[pos + 1 :]:
            b = fragments[j]
            if b.cy - a.cy > geom.y(30.0):
                break
            if _same_text_cluster(a, b, geom):
                union(i, j)

    groups: dict[int, list[TextFragment]] = {}
    for i, fragment in enumerate(fragments):
        groups.setdefault(find(i), []).append(fragment)
    return list(groups.values())


def _cluster_score(cluster: list[TextFragment], geom: FrameGeometry) -> float:
    """Prefer broad multi-line text blocks over narrow labels/stat columns."""
    if not cluster:
        return -1.0

    widths = np.array([item.width / geom.width for item in cluster], dtype=float)
    x1 = min(item.x1 for item in cluster)
    y1 = min(item.y1 for item in cluster)
    x2 = max(item.x2 for item in cluster)
    y2 = max(item.y2 for item in cluster)

    coverage = float(np.sum(np.power(widths, 1.5)))
    max_width = float(np.max(widths))
    broad_lines = int(np.sum(widths >= 0.24))
    bbox_width = (x2 - x1) / geom.width
    bbox_height = (y2 - y1) / geom.height

    return (
        30.0 * coverage
        + 60.0 * max_width
        + 4.0 * broad_lines
        + 1.0 * min(len(cluster), 12)
        + 10.0 * bbox_width
        + 3.0 * bbox_height
    )


def detect_text_region(frame: np.ndarray) -> Rect:
    """Discover the most likely narrative/menu text block.

    No absolute screen coordinates are used. If the cheap detector cannot find
    a credible text block, the entire native framebuffer is returned so OCR
    can still recover rather than silently looking in the wrong place.
    """
    geom = FrameGeometry.from_frame(frame)
    fragments = _line_fragments(frame, geom)
    groups = _clusters(fragments, geom)
    if not groups:
        return (0, 0, geom.width, geom.height)

    best = max(groups, key=lambda group: _cluster_score(group, geom))
    x1 = min(item.x1 for item in best)
    y1 = min(item.y1 for item in best)
    x2 = max(item.x2 for item in best)
    y2 = max(item.y2 for item in best)

    # Give OCR room for leading option dots, glyph edges, and lines that the
    # cheap pixel detector only partially connected.
    pad_left = geom.x_px(28, minimum=8)
    pad_right = geom.x_px(14, minimum=5)
    pad_y = geom.y_px(14, minimum=5)
    rect = clamp_rect(
        (x1 - pad_left, y1 - pad_y, x2 + pad_right, y2 + pad_y),
        geom.width,
        geom.height,
    )

    # A tiny isolated label is not a useful OCR crop; full-frame fallback is
    # safer and still contains no hard-coded Darklands screen location.
    rx1, ry1, rx2, ry2 = rect
    if (rx2 - rx1) < max(geom.x_px(72, minimum=24), int(geom.width * 0.12)):
        return (0, 0, geom.width, geom.height)
    if (ry2 - ry1) < geom.y_px(16, minimum=6):
        return (0, 0, geom.width, geom.height)
    return rect
