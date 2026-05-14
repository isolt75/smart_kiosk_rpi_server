"""상태 LED 제어 (5.2.4, 옵션).

green=정상 / yellow=차량 감지 / red=오류.
gpiozero가 없거나 핀이 -1이면 NOP.
"""

from __future__ import annotations

from typing import Optional

from src.config import settings
from src.utils.logger import logger


class _NullLED:
    def on(self) -> None: ...
    def off(self) -> None: ...
    def close(self) -> None: ...


def _make_led(pin: int):
    if pin is None or pin < 0:
        return _NullLED()
    try:
        from gpiozero import LED  # type: ignore

        return LED(pin)
    except Exception as exc:
        logger.debug(f"LED(pin={pin}) 초기화 실패 — null 사용: {exc}")
        return _NullLED()


class StatusLeds:
    def __init__(self) -> None:
        self.green = _make_led(settings.led_green_pin)
        self.yellow = _make_led(settings.led_yellow_pin)
        self.red = _make_led(settings.led_red_pin)

    def mark_idle(self) -> None:
        self.green.on()
        self.yellow.off()
        self.red.off()

    def mark_busy(self) -> None:
        self.yellow.on()

    def clear_busy(self) -> None:
        self.yellow.off()

    def mark_error(self) -> None:
        self.red.on()

    def clear_error(self) -> None:
        self.red.off()

    def close(self) -> None:
        for led in (self.green, self.yellow, self.red):
            try:
                led.close()
            except Exception:
                pass
