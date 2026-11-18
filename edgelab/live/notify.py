"""Desktop notifications (Windows toast / macOS / Linux) via plyer, with a safe fallback."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def desktop_notify(title: str, message: str, timeout: int = 20) -> bool:
    """Show an operating-system notification. Returns False (never raises) if it isn't possible."""
    try:
        from plyer import notification

        notification.notify(title=title[:63], message=message[:250], app_name="EdgeLab", timeout=timeout)
        return True
    except Exception as e:  # noqa: BLE001 - notifications must never break scanning
        log.info("Desktop notification unavailable: %s", e)
        return False


def beep_wav(freq: float = 880, ms: int = 220, volume: float = 0.4) -> bytes:
    """A short beep as WAV bytes (played in the browser when a new alert arrives)."""
    import io
    import math
    import struct
    import wave

    rate = 22050
    n = int(rate * ms / 1000)
    frames = b"".join(struct.pack("<h", int(volume * 32767 * math.sin(2 * math.pi * freq * k / rate) *
                                            min(1, k / 200, (n - k) / 200))) for k in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()
