from __future__ import annotations

import base64
import hashlib
import io
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


@dataclass
class Observation:
    image: Image.Image
    origin: tuple[int, int]
    logical_size: tuple[int, int]
    cursor: tuple[float, float] | None
    context: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    captured_at: float = field(default_factory=time.monotonic)
    changed: float = 1.0

    def jpeg(self) -> bytes:
        b = io.BytesIO()
        self.image.convert("RGB").save(b, "JPEG", quality=78)
        return b.getvalue()

    def metadata(self) -> dict:
        return {"id": self.id, "width": self.image.width, "height": self.image.height,
                "origin": self.origin, "logical_size": self.logical_size,
                "cursor": self.cursor, "changed": round(self.changed, 5),
                "context": self.context, "sha256": hashlib.sha256(self.jpeg()).hexdigest()}

    def payload(self) -> dict:
        return {**self.metadata(), "image_base64": base64.b64encode(self.jpeg()).decode(),
                "mime_type": "image/jpeg"}

    def target(self, x: int, y: int) -> tuple[float, float]:
        if not (0 <= x < self.image.width and 0 <= y < self.image.height):
            raise ValueError("move_to outside the observed image")
        return (self.origin[0] + x * self.logical_size[0] / self.image.width,
                self.origin[1] + y * self.logical_size[1] / self.image.height)


def cursor_position() -> tuple[float, float] | None:
    """Read only. Never inject OS input."""
    if sys.platform == "darwin":
        import Quartz
        p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return p.x, p.y
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        p = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
            return float(p.x), float(p.y)
        return None
    try:
        from Xlib.display import Display
        d = Display()
        try:
            p = d.screen().root.query_pointer()
            return float(p.root_x), float(p.root_y)
        finally:
            d.close()
    except Exception:
        return None


class DesktopObserver:
    def __init__(self, monitor=1, region=None, max_width=1280, context_file=None):
        if not 64 <= max_width <= 4096:
            raise ValueError("max_width must be 64..4096")
        self.monitor, self.region, self.max_width = monitor, region, max_width
        self.context_file = Path(context_file) if context_file else None
        self.previous = None

    def cursor(self):
        return cursor_position()

    def capture(self) -> Observation:
        if sys.platform == "darwin":
            import Quartz
            if not Quartz.CGPreflightScreenCaptureAccess():
                raise PermissionError("macOS Screen Recording permission is missing; enable it for the launching terminal/app in System Settings > Privacy & Security > Screen Recording, then restart that app")
        import mss
        # A fresh context allows capture from the server worker thread on all platforms.
        with mss.mss() as sct:
            if not 1 <= self.monitor < len(sct.monitors):
                raise ValueError("monitor not available (indices start at 1)")
            mon = sct.monitors[self.monitor]
            area = dict(self.region or mon)
            if area["width"] <= 0 or area["height"] <= 0:
                raise ValueError("region must have positive size")
            if (area["left"] < mon["left"] or area["top"] < mon["top"] or
                area["left"]+area["width"] > mon["left"]+mon["width"] or
                area["top"]+area["height"] > mon["top"]+mon["height"]):
                raise ValueError("region must be within the selected monitor")
            shot = sct.grab(area)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
        if image.width > self.max_width:
            image = image.resize((self.max_width, max(1, round(image.height*self.max_width/image.width))))
        changed = 1.0
        if self.previous is not None and self.previous.size == image.size:
            changed = sum(ImageStat.Stat(ImageChops.difference(image, self.previous)).mean)/(3*255)
        self.previous = image.copy()
        context = self.context_file.read_text(encoding="utf-8")[:16000] if self.context_file else ""
        return Observation(image, (area["left"], area["top"]), (area["width"], area["height"]),
                           self.cursor(), context, changed=changed)
