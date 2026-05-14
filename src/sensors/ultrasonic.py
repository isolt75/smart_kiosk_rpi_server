"""HC-SR04 초음파 거리 측정 + 노이즈 필터링 + 차량 감지 이벤트 발행.

요구사항 매핑
- FR-1.1.1~4: ``gpiozero.DistanceSensor``로 측정, 50ms 주기, 0.02~4.0m
- FR-1.2.1~3: 이동평균(N=5) / outlier ±50cm / 히스테리시스 ±10cm
- FR-1.3.1~3: 1.5m 이하 연속 3회 → DETECTED, 10초 쿨다운, 2.0m 이상 → DEPARTED
- NFR-2.2.1: 측정 타임아웃(>1초) 시 GPIO 리셋 시도
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from src.config import settings
from src.core.event_bus import Event, EventBus, VEHICLE_DEPARTED, VEHICLE_DETECTED
from src.utils.logger import logger


class _NullSensor:
    """gpiozero를 import할 수 없는 환경(개발 PC)을 위한 폴백."""

    value = 1.0
    max_distance = 4.0
    distance = 4.0

    def close(self) -> None:  # noqa: D401
        return None


def _create_sensor():
    try:
        from gpiozero import DistanceSensor  # type: ignore

        return DistanceSensor(
            echo=settings.sensor_echo_pin,
            trigger=settings.sensor_trig_pin,
            max_distance=settings.sensor_max_distance_m,
            threshold_distance=settings.sensor_detect_threshold_m,
        )
    except Exception as exc:
        logger.error(f"gpiozero 초기화 실패 — null 센서 사용 ({exc})")
        return _NullSensor()


class UltrasonicMonitor:
    """50ms 주기로 측정하고 이벤트 버스에 publish하는 스레드."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._sensor = _create_sensor()
        self._window: deque[float] = deque(maxlen=settings.sensor_moving_avg_window)
        self._last_raw: Optional[float] = None
        self._consec_below = 0
        self._vehicle_present = False
        self._last_event_id: Optional[str] = None
        self._last_detect_ts: float = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="SensorThread", daemon=True
        )

    # --- lifecycle ---
    def start(self) -> None:
        self._thread.start()
        logger.info(
            f"SensorThread 시작 (TRIG={settings.sensor_trig_pin}, "
            f"ECHO={settings.sensor_echo_pin}, "
            f"poll={settings.sensor_poll_interval_ms}ms)"
        )

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)
        try:
            self._sensor.close()
        except Exception:
            pass
        logger.info("SensorThread 종료")

    # --- internal ---
    def _measure(self) -> Optional[float]:
        """단일 측정값(m)을 반환. 무효치면 None."""
        deadline = time.monotonic() + 1.0
        try:
            value = self._sensor.distance  # gpiozero: 0.0 ~ max_distance(m)
        except Exception as exc:
            logger.warning(f"센서 측정 예외: {exc}")
            return None
        if time.monotonic() > deadline:
            logger.warning("센서 측정 타임아웃(>1s) — GPIO 리셋 시도")
            self._reset_sensor()
            return None
        if value is None:
            return None
        # FR-1.1.3 범위 검증
        if value < 0.02 or value > settings.sensor_max_distance_m:
            return None
        return float(value)

    def _reset_sensor(self) -> None:
        try:
            self._sensor.close()
        except Exception:
            pass
        time.sleep(0.1)
        self._sensor = _create_sensor()

    def _filter(self, raw: float) -> Optional[float]:
        # FR-1.2.2 outlier 제거
        if self._last_raw is not None:
            if abs(raw - self._last_raw) > settings.sensor_outlier_delta_m:
                logger.debug(
                    f"outlier 제거: raw={raw:.3f} last={self._last_raw:.3f}"
                )
                return None
        self._last_raw = raw
        # FR-1.2.1 이동평균
        self._window.append(raw)
        if len(self._window) < self._window.maxlen:
            return None
        return sum(self._window) / len(self._window)

    def _evaluate(self, filtered: float) -> None:
        now = time.monotonic()
        detect = settings.sensor_detect_threshold_m
        depart = settings.sensor_depart_threshold_m
        hyst = settings.sensor_hysteresis_m

        # FR-1.2.3 히스테리시스: 진입 시 detect-hyst, 이탈 시 depart+hyst
        effective_detect = detect - hyst if self._vehicle_present else detect
        effective_depart = depart + hyst if self._vehicle_present else depart

        if filtered <= effective_detect:
            self._consec_below += 1
        else:
            self._consec_below = 0

        if (
            not self._vehicle_present
            and self._consec_below >= settings.sensor_detect_consecutive
            and (now - self._last_detect_ts) >= settings.sensor_cooldown_sec
        ):
            self._vehicle_present = True
            self._last_detect_ts = now
            evt = Event(
                type=VEHICLE_DETECTED,
                payload={"distance_m": filtered, "distance_cm": filtered * 100.0},
            )
            self._last_event_id = evt.event_id
            logger.info(f"VEHICLE_DETECTED: {filtered:.2f}m (event={evt.event_id})")
            self._bus.publish(evt)
        elif self._vehicle_present and filtered >= effective_depart:
            self._vehicle_present = False
            self._consec_below = 0
            evt = Event(
                type=VEHICLE_DEPARTED,
                event_id=self._last_event_id or "",
                payload={"distance_m": filtered},
            )
            logger.info(f"VEHICLE_DEPARTED: {filtered:.2f}m (event={evt.event_id})")
            self._bus.publish(evt)

    def _run(self) -> None:
        interval = settings.sensor_poll_interval_ms / 1000.0
        next_tick = time.monotonic()
        while not self._stop.is_set():
            next_tick += interval
            raw = self._measure()
            if raw is not None:
                filtered = self._filter(raw)
                if filtered is not None:
                    self._evaluate(filtered)
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                # 측정이 주기보다 오래 걸린 경우 — 일정 따라잡기
                next_tick = time.monotonic()
