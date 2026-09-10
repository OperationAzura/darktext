"""Game screen capture routines for DOSBox framebuffer API and X11 fallback."""

import os
import re
import shutil
import subprocess
import urllib.request
import numpy as np


def shutil_which(name: str) -> str | None:
    return shutil.which(name)


def capture_logical_game() -> np.ndarray:
    """Capture the DOSBox framebuffer at its native emulated resolution."""
    api = os.environ.get("DOSBOX_API_URL", "http://127.0.0.1:8086").rstrip("/")
    req = urllib.request.Request(
        api + "/api/v1/video/frame",
        headers={"Accept": "image/x-portable-pixmap", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=2.5) as response:
        data = response.read()

    # Our DOSBox endpoint emits simple PPM: P6\nWIDTH HEIGHT\n255\n followed by packed RGB.
    match = re.match(br"P6\n([0-9]+) ([0-9]+)\n255\n", data)
    if not match:
        raise RuntimeError("DOSBox /api/v1/video/frame returned an invalid PPM frame")

    width = int(match.group(1))
    height = int(match.group(2))
    body = data[match.end():]
    expected = width * height * 3
    if len(body) != expected:
        raise RuntimeError(
            f"DOSBox frame length mismatch: got {len(body)} RGB bytes; expected {expected}"
        )

    rgb = np.frombuffer(body, dtype=np.uint8).reshape((height, width, 3))
    # Keep the framebuffer exactly as DOSBox rendered it. DarkText now performs
    # all UI geometry in these native pixels; OCR enlargement happens only on
    # the cropped story pane.
    return rgb[:, :, ::-1].copy()


def find_darklands_window() -> tuple[str, int, int, int, int]:
    """Fallback window detection using xdotool if desktop window capture is needed."""
    cmd = ["xdotool", "search", "--onlyvisible", "--name", "DARKLAND.EXE"]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    ids = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    if not ids:
        p = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", "Darklands"],
            capture_output=True,
            text=True,
            check=False,
        )
        ids = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    if not ids:
        raise RuntimeError("Could not find a visible Darklands/DARKLAND.EXE window with xdotool")

    wid = ids[-1]
    p = subprocess.run(
        ["xdotool", "getwindowgeometry", "--shell", wid],
        capture_output=True,
        text=True,
        check=True,
    )
    vals = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k] = int(v)
    return wid, vals["X"], vals["Y"], vals["WIDTH"], vals["HEIGHT"]
