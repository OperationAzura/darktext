"""Audio format information shipped beside Piper voice models."""

import json
from pathlib import Path

from .config import SAMPLE_RATE


def voice_sample_rate(model: Path) -> int:
    """Read Piper's model metadata; retain the legacy rate if it is absent."""
    try:
        data = json.loads(Path(str(model) + ".json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SAMPLE_RATE
    rate = data["audio"]["sample_rate"]
    if isinstance(rate, bool) or not isinstance(rate, int) or not 8000 <= rate <= 192000:
        raise ValueError(f"Invalid sample rate in {model}.json: {rate!r}")
    return rate
