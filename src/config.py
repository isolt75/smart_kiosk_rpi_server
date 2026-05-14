"""중앙 설정 (.env 로드 + pydantic 검증).

SRS의 모든 튜닝 파라미터는 여기 모인다. 모듈은 ``settings``를
import해서 사용한다 — 환경변수 직접 참조는 금지(NFR-4.2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Device ---
    device_id: str = Field(default="edge-001")

    # --- Main Server / API ---
    api_base_url: str = Field(default="https://main-server.example.com/api/v1")
    api_key: str = Field(default="")
    api_jwt: str = Field(default="")
    ca_bundle: str = Field(default="")

    # --- Sensor (HC-SR04) ---
    sensor_trig_pin: int = 23
    sensor_echo_pin: int = 24
    sensor_poll_interval_ms: int = 50
    sensor_max_distance_m: float = 4.0
    sensor_moving_avg_window: int = 5
    sensor_outlier_delta_m: float = 0.5
    sensor_detect_threshold_m: float = 1.5
    sensor_depart_threshold_m: float = 2.0
    sensor_detect_consecutive: int = 3
    sensor_hysteresis_m: float = 0.1
    sensor_cooldown_sec: float = 10.0

    # --- Camera ---
    camera_width: int = 1920
    camera_height: int = 1080
    camera_jpeg_quality: int = 90

    # --- Microphone ---
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_frame_duration_ms: int = 30  # webrtcvad 허용값: 10/20/30
    audio_max_duration_sec: float = 30.0
    audio_vad_silence_sec: float = 2.0
    audio_vad_aggressiveness: int = 2  # 0~3

    # --- Plate Detection (YOLOv8, 옵션) ---
    plate_model_path: str = "/opt/edge-server/models/license_plate.pt"
    plate_conf_threshold: float = 0.5
    plate_iou_threshold: float = 0.5
    plate_img_size: int = 640
    plate_max_detections: int = 5
    plate_crop_jpeg_quality: int = 92

    # --- Cache ---
    cache_base_dir: Path = Path("/var/lib/edge/cache")
    cache_max_bytes: int = 1024 * 1024 * 1024  # 1GB
    cache_max_age_hours: int = 24

    # --- HTTP / Queue / Retry ---
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    queue_max_size: int = 100
    http_max_workers: int = 4
    retry_max_attempts: int = 5
    retry_initial_delay: float = 1.0
    retry_max_delay: float = 16.0

    # --- Heartbeat ---
    heartbeat_interval_sec: int = 60

    # --- Logging ---
    log_dir: Path = Path("/var/log/edge-server")
    log_level: str = "INFO"

    # --- LEDs (-1: 미사용) ---
    led_green_pin: int = 17
    led_yellow_pin: int = 27
    led_red_pin: int = 22

    @property
    def image_cache_dir(self) -> Path:
        return self.cache_base_dir / "images"

    @property
    def audio_cache_dir(self) -> Path:
        return self.cache_base_dir / "audio"

    @property
    def auth_header(self) -> dict[str, str]:
        if self.api_jwt:
            return {"Authorization": f"Bearer {self.api_jwt}"}
        if self.api_key:
            return {"X-API-Key": self.api_key}
        return {}

    def verify_option(self) -> Optional[str] | bool:
        # requests의 verify 인자에 그대로 전달
        return self.ca_bundle if self.ca_bundle else True


settings = Settings()
