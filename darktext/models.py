"""Data models representing detected text and screen state."""

from dataclasses import dataclass


@dataclass
class Line:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    blue: bool
    option_start: bool = False


@dataclass
class ScreenState:
    narrative: str
    options: list[str]
    selected: int | None
    raw_lines: list[Line]
    frame_width: int = 640
    frame_height: int = 400

    def content_text(self) -> str:
        return "\n".join([self.narrative] + [f"{i+1}:{t}" for i, t in enumerate(self.options)])

    def speech_text(self) -> str:
        parts = []
        if self.narrative.strip():
            parts.append(self.narrative.strip())
        for text in self.options:
            parts.append(text.strip())
        return " ".join(p for p in parts if p).strip()
