# Smart Drive-through Edge Server

Raspberry Pi 4 기반 **Python 엣지 서버**. HC-SR04 초음파 센서로 차량 접근을
감지하고, Pi Camera 이미지와 마이크 오디오를 동기 캡처해 메인 서버
(JSP/Vue.js)로 HTTPS multipart 업로드한다. AI 추론은 메인 서버 책임이며,
엣지는 **데이터 수집 게이트웨이** 역할만 수행한다.

전체 명세는 [`docs/SRS.md`](docs/SRS.md)를, 라즈베리파이 설치/배포는
[`docs/INSTALL.md`](docs/INSTALL.md)를 참고.

## 아키텍처 요약

```
HC-SR04 ──┐
          │   ┌──────────────┐  VEHICLE_DETECTED  ┌───────────────┐
          ├──▶│ SensorThread │ ─────────────────▶ │ CaptureThreads│
Pi Cam ───┤   │ (50ms poll)  │                    │ camera + mic  │
USB Mic ──┘   └──────────────┘                    └───────┬───────┘
                                                          │ TransmitJob
                                                          ▼
                                              ┌─────────────────────┐
                                              │ SenderThread        │ ──▶ Main Server (HTTPS)
                                              │ ThreadPoolExecutor  │
                                              └─────────────────────┘
```

- **SensorThread** — HC-SR04 50ms 폴링, MA(N=5)/outlier/히스테리시스, 1.5m 이하
  3회 연속 → `VEHICLE_DETECTED`, 10초 쿨다운
- **CaptureThreads** — picamera2 1920×1080 JPEG + sounddevice 16kHz WAV (VAD로
  무음 2초 시 종료)
- **SenderThread** — `queue.Queue(maxsize=100)` + `ThreadPoolExecutor(4)` +
  tenacity 지수 백오프(1→2→4→8→16, 5회)
- **MainThread** — APScheduler heartbeat 60s, LRU 캐시 정리

## 빠른 시작 (라즈베리파이)

```bash
git clone https://github.com/isolt75/smart_kiosk_rpi_server.git
cd smart_kiosk_rpi_server
sudo bash scripts/install.sh
sudo -u pi nano /opt/edge-server/.env   # API 키/베이스 URL 설정
sudo systemctl start edge-server
sudo journalctl -u edge-server -f
```

## 디렉터리 구조

```
.
├── src/
│   ├── main.py                # 엔트리포인트
│   ├── config.py              # pydantic-settings
│   ├── sensors/ultrasonic.py  # HC-SR04 + 필터링
│   ├── capture/
│   │   ├── camera.py          # picamera2 wrapper
│   │   └── microphone.py      # sounddevice + webrtcvad
│   ├── communication/
│   │   ├── api_client.py      # multipart 업로드 + heartbeat
│   │   └── retry.py           # tenacity 지수 백오프
│   ├── core/
│   │   ├── event_bus.py       # pub/sub
│   │   └── queue_manager.py   # drop-oldest 큐
│   └── utils/
│       ├── logger.py          # loguru
│       ├── cache.py           # LRU 캐시 정리
│       └── leds.py            # 상태 LED
├── systemd/edge-server.service
├── scripts/install.sh
├── docs/
│   ├── SRS.md                 # 전체 명세서
│   └── INSTALL.md             # 설치/운영 가이드
├── .env.example
└── requirements.txt
```

## 환경 의존성

| 패키지 | 용도 |
|---|---|
| `gpiozero`, `RPi.GPIO` | GPIO/HC-SR04 |
| `picamera2`, `Pillow` | 카메라, JPEG 인코딩 |
| `sounddevice`, `pyaudio`, `webrtcvad` | 오디오 + VAD |
| `requests`, `urllib3`, `requests-toolbelt` | HTTP/multipart |
| `tenacity`, `APScheduler` | 재시도, heartbeat |
| `pydantic`, `pydantic-settings`, `python-dotenv` | 설정 |
| `loguru`, `psutil` | 로깅, 시스템 메트릭 |

라즈베리파이 외 환경(개발 PC)에서는 `gpiozero`/`picamera2`/`sounddevice` 모듈이
없거나 디바이스가 없을 수 있다. 코드는 이 경우 자동으로 null 백엔드로
폴백하므로 import만 깨지지 않으면 동작은 한다. 실제 운용은 반드시
라즈베리파이에서.

## 라이선스

---
