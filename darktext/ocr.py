"""OCR processing and text extraction for Darklands game dialogs."""

from difflib import SequenceMatcher
import re
import cv2
import numpy as np
from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

from .config import CONTENT_SIMILARITY, DEBUG_DIR, OCR_SCALE
from .geometry import FrameGeometry
from .models import Line, ScreenState
from .text_region import detect_text_region


def build_engine() -> RapidOCR:
    """Instantiate and configure RapidOCR for English small model."""
    return RapidOCR(
        params={
            "Global.use_cls": False,
            "Global.text_score": 0.35,
            "Global.log_level": "warning",
            "Det.lang_type": LangDet.EN,
            "Det.model_type": ModelType.SMALL,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Rec.lang_type": LangRec.EN,
            "Rec.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
        }
    )


def clean_text(text: str) -> str:
    """Normalize common OCR artifacts in Darklands gothic serif font."""
    text = text.replace("…", "...")
    text = text.replace("|", "I")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def box_bounds(box) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _crop_local(line: Line, origin_x: int, origin_y: int) -> Line:
    return Line(
        line.text,
        line.x1 - origin_x,
        line.y1 - origin_y,
        line.x2 - origin_x,
        line.y2 - origin_y,
        line.score,
        line.blue,
        line.option_start,
    )


def line_is_blue(crop_bgr: np.ndarray, line: Line, geom: FrameGeometry) -> bool:
    """Detect whether a crop-local OCR line is highlighted in blue."""
    x_pad = geom.x_px(2, minimum=1)
    y_pad = geom.y_px(2, minimum=1)
    x1 = max(0, int(line.x1) - x_pad)
    y1 = max(0, int(line.y1) - y_pad)
    x2 = min(crop_bgr.shape[1], int(line.x2) + x_pad)
    y2 = min(crop_bgr.shape[0], int(line.y2) + y_pad)
    reg = crop_bgr[y1:y2, x1:x2]
    if reg.size == 0:
        return False
    b = reg[:, :, 0].astype(np.int16)
    g = reg[:, :, 1].astype(np.int16)
    r = reg[:, :, 2].astype(np.int16)
    blue = (b >= 105) & (b >= r * 1.55) & (b >= g * 1.35) & ((b - r) >= 45)
    bright = (r >= 165) & (g >= 165) & (b >= 165)
    blue_n = int(blue.sum())
    bright_n = int(bright.sum())
    min_blue = geom.area_px(8, minimum=2)
    return blue_n >= min_blue and blue_n >= max(
        min_blue, int(0.12 * (blue_n + bright_n + 1))
    )


def visual_dot_triplet(
    crop_bgr: np.ndarray, line_x1: float, y1: float, y2: float, geom: FrameGeometry
) -> bool:
    """Detect the three tiny leading dots immediately left of an OCR text line."""
    y_pad = geom.y_px(3, minimum=1)
    ya = max(0, int(y1) - y_pad)
    yb = min(crop_bgr.shape[0], int(y2) + y_pad)
    left_pad = geom.x_px(42, minimum=12)
    xa = max(0, int(line_x1) - left_pad)
    xb = min(crop_bgr.shape[1], int(line_x1) + geom.x_px(8, minimum=3))
    strip = crop_bgr[ya:yb, xa:xb]
    if strip.size == 0:
        return False
    b, g, r = cv2.split(strip)
    white = (r > 165) & (g > 165) & (b > 165)
    blue = (
        (b > 90)
        & (b.astype(np.int16) > r.astype(np.int16) * 1.35)
        & (b.astype(np.int16) > g.astype(np.int16) * 1.20)
    )
    mask = ((white | blue).astype(np.uint8) * 255)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    dots = []
    max_area = geom.area_px(30, minimum=4)
    max_w = geom.x_px(8, minimum=2)
    max_h = geom.y_px(8, minimum=2)
    for i in range(1, n):
        _x, _y, w, h, area = stats[i]
        if 1 <= area <= max_area and 1 <= w <= max_w and 1 <= h <= max_h:
            dots.append((float(cents[i][0]), float(cents[i][1]), w, h, area))
    dots.sort()
    y_tol = max(1.5, geom.y(3.5))
    min_gap = max(1.0, geom.x(2))
    max_gap = max(min_gap + 1.0, geom.x(14))
    for i in range(len(dots) - 2):
        a, c, d = dots[i : i + 3]
        if abs(a[1] - c[1]) <= y_tol and abs(c[1] - d[1]) <= y_tol:
            if min_gap <= c[0] - a[0] <= max_gap and min_gap <= d[0] - c[0] <= max_gap:
                return True
    return False


def extract_state(
    engine: RapidOCR, native_game: np.ndarray, save_debug: bool = False
) -> ScreenState:
    """Discover and OCR the active text block in native framebuffer pixels."""
    geom = FrameGeometry.from_frame(native_game)
    region = detect_text_region(native_game)
    x1, y1, x2, y2 = region
    crop = native_game[y1:y2, x1:x2].copy()

    # Only the dynamically discovered text crop is enlarged for recognition.
    enlarged = cv2.resize(
        crop, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_NEAREST
    )
    result = engine(enlarged, use_cls=False)
    lines: list[Line] = []

    if getattr(result, "boxes", None) is not None and getattr(result, "txts", None) is not None:
        for box, txt, score in zip(result.boxes, result.txts, result.scores):
            tx = clean_text(str(txt))
            if not tx:
                continue
            bx1, by1, bx2, by2 = box_bounds(box)
            bx1 /= OCR_SCALE
            by1 /= OCR_SCALE
            bx2 /= OCR_SCALE
            by2 /= OCR_SCALE

            if (by2 - by1) < max(2.0, geom.y(5)) or float(score) < 0.32:
                continue

            # ScreenState coordinates are always absolute native framebuffer
            # coordinates even though OCR operated on a moving crop.
            line = Line(
                tx,
                bx1 + x1,
                by1 + y1,
                bx2 + x1,
                by2 + y1,
                float(score),
                False,
            )
            local = _crop_local(line, x1, y1)
            line.blue = line_is_blue(crop, local, geom)
            regex_marker = bool(re.match(r"^\s*(?:\.{1,4}|…|[·•\-])", tx))
            has_visual_dots = visual_dot_triplet(
                crop, local.x1, local.y1, local.y2, geom
            )
            line.option_start = regex_marker or has_visual_dots
            lines.append(line)

    y_sort = max(1.0, geom.y(3.0))
    lines.sort(key=lambda l: (round(l.y1 / y_sort), l.x1))

    narrative_lines: list[str] = []
    options: list[list[str]] = []
    option_line_groups: list[list[Line]] = []
    current: list[str] | None = None
    current_group: list[Line] | None = None

    for line in lines:
        text = line.text
        is_new_option = False
        if line.option_start:
            is_new_option = True
        elif options:
            prev_ended = bool(current and current[-1].endswith((".", "!", "?", ":")))
            too_many_lines = bool(current_group and len(current_group) >= 2)
            vertical_gap = bool(
                current_group and (line.y1 - current_group[-1].y1) > geom.y(22.0)
            )
            if prev_ended or too_many_lines or vertical_gap:
                is_new_option = True
                line.option_start = True

        if is_new_option:
            text = re.sub(r"^\s*(?:\.{1,}|…|[·•\-]{1,})\s*", "", text).strip()
            current = [text] if text else []
            current_group = [line]
            options.append(current)
            option_line_groups.append(current_group)
        elif options and current is not None and current_group is not None:
            current.append(text)
            current_group.append(line)
        else:
            narrative_lines.append(text)

    valid_options: list[str] = []
    valid_groups: list[list[Line]] = []
    for parts, group in zip(options, option_line_groups):
        opt_str = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if opt_str and len(opt_str.split()) <= 20 and len(opt_str) <= 130:
            valid_options.append(opt_str)
            valid_groups.append(group)
        elif opt_str:
            narrative_lines.append(opt_str)

    narrative = re.sub(r"\s+", " ", " ".join(narrative_lines)).strip()
    selected: int | None = None
    for idx, group in enumerate(valid_groups, 1):
        if any(l.blue for l in group):
            selected = idx
            break

    if save_debug:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(DEBUG_DIR / "game-native.png"), native_game)
        cv2.imwrite(str(DEBUG_DIR / "text-region-native.png"), crop)
        vis = native_game.copy()
        cv2.rectangle(vis, (x1, y1), (x2 - 1, y2 - 1), (255, 255, 255), 1)
        for l in lines:
            color = (255, 0, 0) if l.blue else (
                (0, 255, 255) if l.option_start else (0, 255, 0)
            )
            cv2.rectangle(
                vis,
                (int(l.x1), int(l.y1)),
                (int(l.x2), int(l.y2)),
                color,
                1,
            )
        cv2.imwrite(str(DEBUG_DIR / "text-region-boxes-native.png"), vis)

    return ScreenState(
        narrative,
        valid_options,
        selected,
        lines,
        frame_width=geom.width,
        frame_height=geom.height,
        text_region=region,
    )


def equivalent(a: ScreenState, b: ScreenState) -> bool:
    """Check if two ScreenState snapshots represent the same menu/dialog."""
    if len(a.options) != len(b.options):
        return False
    aa = re.sub(r"\W+", " ", a.content_text().lower()).strip()
    bb = re.sub(r"\W+", " ", b.content_text().lower()).strip()
    if not aa and not bb:
        return True
    return SequenceMatcher(None, aa, bb).ratio() >= CONTENT_SIMILARITY


def print_state(state: ScreenState) -> None:
    """Display extracted screen state in user-friendly format."""
    print("Narrative:")
    print(state.narrative or "(none)")
    print("Options:")
    if not state.options:
        print("(none)")
    for i, text in enumerate(state.options, 1):
        flag = "  <-- SELECTED" if state.selected == i else ""
        print(f"- {text}{flag}")
