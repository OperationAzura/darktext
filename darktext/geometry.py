"""Geometry helpers for working directly in DOSBox framebuffer pixels."""

from dataclasses import dataclass


# These fractions preserve the story-pane calibration that was originally
# measured against DarkText's old 640x480 stretched working image. They are
# converted once at the capture boundary into the actual framebuffer's pixel
# coordinates; all downstream coordinates stay native.
_REFERENCE_WIDTH = 640.0
_REFERENCE_HEIGHT = 480.0
_REFERENCE_TEXT_RECT = (135.0, 45.0, 625.0, 455.0)


@dataclass(frozen=True)
class FrameGeometry:
    """Native framebuffer dimensions plus calibrated scaling helpers."""

    width: int
    height: int

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Invalid framebuffer size: {self.width}x{self.height}")

    @classmethod
    def from_frame(cls, frame) -> "FrameGeometry":
        """Build geometry from a numpy/OpenCV-style HxWxC image."""
        if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
            raise ValueError("Expected an image with height and width")
        height, width = int(frame.shape[0]), int(frame.shape[1])
        return cls(width=width, height=height)

    @property
    def scale_x(self) -> float:
        """Scale old calibrated horizontal distances into native pixels."""
        return self.width / _REFERENCE_WIDTH

    @property
    def scale_y(self) -> float:
        """Scale old calibrated vertical distances into native pixels."""
        return self.height / _REFERENCE_HEIGHT

    def x(self, reference_pixels: float) -> float:
        return float(reference_pixels) * self.scale_x

    def y(self, reference_pixels: float) -> float:
        return float(reference_pixels) * self.scale_y

    def x_px(self, reference_pixels: float, minimum: int = 0) -> int:
        return max(minimum, int(round(self.x(reference_pixels))))

    def y_px(self, reference_pixels: float, minimum: int = 0) -> int:
        return max(minimum, int(round(self.y(reference_pixels))))

    def area_px(self, reference_pixels: float, minimum: int = 1) -> int:
        scaled = float(reference_pixels) * self.scale_x * self.scale_y
        return max(minimum, int(round(scaled)))

    @property
    def text_rect(self) -> tuple[int, int, int, int]:
        """Story/dialog crop in this framebuffer's native pixel coordinates."""
        rx1, ry1, rx2, ry2 = _REFERENCE_TEXT_RECT
        x1 = min(self.width - 1, max(0, self.x_px(rx1)))
        y1 = min(self.height - 1, max(0, self.y_px(ry1)))
        x2 = min(self.width, max(x1 + 1, self.x_px(rx2)))
        y2 = min(self.height, max(y1 + 1, self.y_px(ry2)))
        return x1, y1, x2, y2
