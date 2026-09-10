"""Geometry helpers for working directly in DOSBox framebuffer pixels."""

from dataclasses import dataclass


# Reference dimensions are used only to scale size/tolerance constants. They do
# not define where text is expected to appear on the screen.
_REFERENCE_WIDTH = 640.0
_REFERENCE_HEIGHT = 480.0


@dataclass(frozen=True)
class FrameGeometry:
    """Native framebuffer dimensions plus resolution-aware scaling helpers."""

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
        """Scale horizontal size/tolerance values into native pixels."""
        return self.width / _REFERENCE_WIDTH

    @property
    def scale_y(self) -> float:
        """Scale vertical size/tolerance values into native pixels."""
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
