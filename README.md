# 스마트 드라이브스루 엣지 서버 소프트웨어 요구사항 명세서 (SRS)

**문서 버전:** 1.0
**작성일:** 2026-05-14
**대상 시스템:** Raspberry Pi 4 기반 Python Edge Server
**연동 시스템:** JSP/Vue.js 기반 Main Server

---

## 1. 시스템 개요

### 1.1 목적
본 명세서는 스마트 드라이브스루 시스템의 최전선에서 동작하는 **Raspberry Pi 4 엣지 서버**의 소프트웨어 요구사항을 정의한다. 엣지 서버는 물리적 센서 데이터를 수집하고, 차량 접근 이벤트를 감지하며, 멀티미디어 데이터(이미지·오디오)를 캡처하여 메인 서버로 전송하는 **데이터 수집 게이트웨이(Data Acquisition Gateway)** 역할을 수행한다.

### 1.2 엣지 서버의 역할
엣지 서버는 무거운 AI 추론(YOLO 객체 탐지, OCR 번호판 인식, STT 음성 인식)을 수행하지 않으며, 다음 책임에 한정된다.

- **실시간 이벤트 감지(Real-time Event Detection):** 초음파 센서 기반 차량 접근 트리거
- **멀티모달 데이터 캡처(Multimodal Capture):** 이미지 및 음성 동기 수집
- **데이터 전달(Data Forwarding):** REST API 기반 안정적 데이터 송신
- **장치 자가관리(Self-Management):** 네트워크 단절·하드웨어 오류에 대한 자율 복구

### 1.3 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Edge Server (Raspberry Pi 4)                 │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Sensor       │  │ Capture      │  │ Communication│           │
│  │ Module       │─▶│ Module       │─▶│ Module       │           │
│  │ (HC-SR04)    │  │ (Cam/Mic)    │  │ (REST Client)│           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│         │                  │                  │                 │
│  ┌──────▼──────────────────▼──────────────────▼──────┐          │
│  │       Event Bus / Thread-Safe Queue (queue.Queue) │          │
│  └────────────────────────────────────────────────────┘         │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ HTTPS (multipart/form-data)
                                  ▼
                  ┌───────────────────────────────┐
                  │     Main Server (JSP)         │
                  │  ┌─────────┐  ┌─────────┐     │
                  │  │ YOLO    │  │ OCR/STT │     │
                  │  │ Engine  │  │ Engine  │     │
                  │  └─────────┘  └─────────┘     │
                  │           ▲                   │
                  │           │ WebSocket/REST    │
                  │  ┌────────┴────────┐          │
                  │  │ Vue.js Frontend │          │
                  │  └─────────────────┘          │
                  └───────────────────────────────┘
```

### 1.4 시스템 워크플로우

1. **상시 감지 단계:** `SensorMonitor` 스레드가 HC-SR04로 50ms 주기 거리 측정
2. **이벤트 트리거:** 이동평균 필터링된 거리값이 1.5m 이하로 진입 시 `VEHICLE_DETECTED` 이벤트 발행
3. **동기 캡처:** 이벤트 수신 즉시 `CameraWorker`가 이미지 캡처, 동시에 `AudioWorker`가 마이크 녹음 개시
4. **데이터 송신:** `APIClient`가 이미지(즉시)와 오디오(녹음 종료 후)를 메인 서버로 비동기 POST
5. **응답 수신:** 메인 서버의 YOLO/OCR 결과는 Vue.js 프론트엔드로 직접 전달되며, 엣지 서버는 ACK만 확인

---

## 2. 기능적 요구사항 (Functional Requirements)

### 2.1 [모듈 1] 센서 제어 모듈 (Sensor Control Module)

#### FR-1.1 초음파 거리 측정
| ID | 요구사항 |
|---|---|
| FR-1.1.1 | HC-SR04 센서를 사용하여 `gpiozero.DistanceSensor` 기반 거리 측정을 수행한다 |
| FR-1.1.2 | 측정 주기는 **50ms**(20Hz)를 기본값으로 하며, 설정 파일을 통해 조정 가능해야 한다 |
| FR-1.1.3 | 측정 범위는 0.02m ~ 4.0m이며, 범위를 벗어난 값은 무효 처리한다 |
| FR-1.1.4 | TRIG 핀의 PWM 신호 발생 후 ECHO 핀의 응답 시간을 마이크로초 단위로 측정한다 |

#### FR-1.2 노이즈 필터링 알고리즘
| ID | 요구사항 |
|---|---|
| FR-1.2.1 | **이동 평균 필터(Moving Average Filter):** 최근 N개(기본 N=5) 측정값의 평균을 사용한다 |
| FR-1.2.2 | **이상치 제거(Outlier Rejection):** 직전 측정값 대비 ±50cm 이상 급변하는 값은 노이즈로 간주하여 제외한다 |
| FR-1.2.3 | **데드존(Dead Zone):** 임계치 ±10cm 구간에서는 히스테리시스를 적용하여 채터링(chattering)을 방지한다 |

#### FR-1.3 차량 감지 이벤트
| ID | 요구사항 |
|---|---|
| FR-1.3.1 | 필터링된 거리값이 **1.5m 이하로 연속 3회** 측정될 경우 `VEHICLE_DETECTED` 이벤트를 발행한다 |
| FR-1.3.2 | 이벤트 발행 후 최소 **10초간 쿨다운(cooldown)** 을 적용하여 중복 이벤트를 방지한다 |
| FR-1.3.3 | 거리값이 2.0m 이상으로 복귀 시 `VEHICLE_DEPARTED` 이벤트를 발행한다 |

---

### 2.2 [모듈 2] 이미지 캡처 모듈 (Image Capture Module)

#### FR-2.1 카메라 구동
| ID | 요구사항 |
|---|---|
| FR-2.1.1 | Raspberry Pi Camera Module v2/v3를 `picamera2` 라이브러리로 제어한다 |
| FR-2.1.2 | `VEHICLE_DETECTED` 이벤트 수신 후 **500ms 이내** 캡처를 완료해야 한다 |
| FR-2.1.3 | 해상도는 기본 **1920×1080(Full HD)**, JPEG 품질 90으로 설정한다 |
| FR-2.1.4 | 카메라는 항시 warm-up 상태를 유지하여 캡처 지연을 최소화한다 |

#### FR-2.2 이미지 저장 및 메모리 관리
| ID | 요구사항 |
|---|---|
| FR-2.2.1 | 캡처된 이미지는 `BytesIO` 객체로 메모리에 우선 적재하여 전송 지연을 최소화한다 |
| FR-2.2.2 | 동시에 로컬 캐시(`/var/lib/edge/cache/images/`)에 백업 저장한다 (전송 실패 시 재전송 용도) |
| FR-2.2.3 | 파일명 규칙: `{event_id}_{YYYYMMDD_HHMMSSfff}.jpg` |
| FR-2.2.4 | 캐시 디렉터리는 **최대 1GB** 또는 **24시간 이내** 파일만 유지하며, 초과 시 LRU 정책으로 삭제한다 |

---

### 2.3 [모듈 3] 오디오 캡처 모듈 (Audio Capture Module)

#### FR-3.1 음성 레코딩
| ID | 요구사항 |
|---|---|
| FR-3.1.1 | USB 마이크 또는 I2S 마이크를 `pyaudio` 또는 `sounddevice` 라이브러리로 제어한다 |
| FR-3.1.2 | 샘플링 레이트 **16kHz**, 16-bit PCM, 모노 채널을 기본 설정으로 한다 |
| FR-3.1.3 | 차량 감지 이벤트 발생 시 자동으로 녹음을 시작하며, 최대 **30초**까지 녹음한다 |
| FR-3.1.4 | VAD(Voice Activity Detection)를 적용하여 **2초간 무음 지속 시** 녹음을 자동 종료한다 (`webrtcvad` 활용) |

#### FR-3.2 버퍼링 및 저장
| ID | 요구사항 |
|---|---|
| FR-3.2.1 | 녹음 데이터는 1024 프레임 단위로 ring buffer에 저장된다 |
| FR-3.2.2 | 녹음 종료 시 WAV 포맷으로 인코딩하여 메모리(`BytesIO`)에 적재한다 |
| FR-3.2.3 | 로컬 캐시(`/var/lib/edge/cache/audio/`)에 동일 파일을 저장한다 |
| FR-3.2.4 | 파일명 규칙: `{event_id}_{YYYYMMDD_HHMMSSfff}.wav` |

---

### 2.4 [모듈 4] 데이터 통신 모듈 (Communication Module)

#### FR-4.1 REST API 클라이언트
| ID | 요구사항 |
|---|---|
| FR-4.1.1 | `requests` 라이브러리 기반 HTTP 클라이언트를 구현한다 |
| FR-4.1.2 | 모든 요청은 **HTTPS** 프로토콜을 사용하며, JWT 또는 API Key 기반 인증 헤더를 포함한다 |
| FR-4.1.3 | 이미지/오디오는 **multipart/form-data** 형식으로 전송한다 |
| FR-4.1.4 | 요청 타임아웃: connect 5초, read 30초 |

#### FR-4.2 전송 큐 관리
| ID | 요구사항 |
|---|---|
| FR-4.2.1 | 전송 대기 데이터는 thread-safe `queue.Queue`(maxsize=100)에 적재한다 |
| FR-4.2.2 | 별도의 `Sender Thread`가 큐를 폴링하여 순차 전송한다 |
| FR-4.2.3 | 큐 적재 실패(가득 참) 시 가장 오래된 항목을 제거하고 신규 항목을 추가한다 (드롭 정책) |

---

## 3. 비기능적 요구사항 (Non-Functional Requirements)

### 3.1 동시성 및 비동기 처리

#### NFR-1.1 스레드 아키텍처
엣지 서버는 다음 4개의 독립 스레드 또는 프로세스로 구성된다.

| 스레드/프로세스 | 역할 | 우선순위 |
|---|---|---|
| **MainThread** | 애플리케이션 부트스트랩, 이벤트 디스패치, 시그널 핸들링 | - |
| **SensorThread** | 초음파 센서 폴링 (50ms 주기) | High |
| **CaptureThread** | 이벤트 수신 시 이미지·오디오 동시 캡처 | High |
| **SenderThread** | 백그라운드 HTTP 전송 | Normal |

#### NFR-1.2 동시성 구현 가이드
| ID | 요구사항 |
|---|---|
| NFR-1.2.1 | 센서 모니터링은 I/O bound이므로 **`threading.Thread`** 를 사용한다 |
| NFR-1.2.2 | 이미지 인코딩 등 CPU bound 작업이 추가될 경우 **`multiprocessing.Process`** 로 분리한다 |
| NFR-1.2.3 | 스레드 간 통신은 `queue.Queue` 및 `threading.Event`를 사용하며, 전역 변수 공유는 금지한다 |
| NFR-1.2.4 | 공유 자원 접근 시 `threading.Lock`으로 보호한다 (특히 카메라 핸들) |
| NFR-1.2.5 | HTTP 전송은 `concurrent.futures.ThreadPoolExecutor(max_workers=4)`로 병렬화한다 |

### 3.2 안정성 및 예외 처리

#### NFR-2.1 네트워크 장애 대응
| ID | 요구사항 |
|---|---|
| NFR-2.1.1 | HTTP 요청 실패 시 **지수 백오프(Exponential Backoff)** 재시도를 적용한다 (1s → 2s → 4s → 8s → 16s, 최대 5회) |
| NFR-2.1.2 | 5회 재시도 실패 시 로컬 캐시에 보관하고, 네트워크 복구 감지 시 일괄 재전송한다 |
| NFR-2.1.3 | 60초 주기로 메인 서버 `/health` 엔드포인트에 heartbeat를 전송하여 연결 상태를 모니터링한다 |
| NFR-2.1.4 | 네트워크 단절 감지 시 LED 또는 로그로 운영자에게 통보한다 |

#### NFR-2.2 하드웨어 예외 처리
| ID | 요구사항 |
|---|---|
| NFR-2.2.1 | 센서 측정 타임아웃(>1초) 발생 시 GPIO 리셋을 시도한다 |
| NFR-2.2.2 | 카메라/마이크 디바이스 분리 감지 시 자동 재초기화를 시도하며, 3회 실패 시 시스템 알람을 발생시킨다 |
| NFR-2.2.3 | 모든 예외는 `logging` 모듈로 기록하며, ERROR 이상은 메인 서버 로그 수집 엔드포인트로 전송한다 |

### 3.3 성능 요구사항

| 항목 | 목표치 |
|---|---|
| 차량 감지 ~ 이미지 캡처 지연 | ≤ 500ms |
| 이미지 캡처 ~ 서버 전송 완료 | ≤ 2초 (정상 네트워크) |
| CPU 사용률 (Idle 상태) | ≤ 15% |
| 메모리 사용량 | ≤ 512MB |
| 24시간 무재시작 가동 | 100% |

### 3.4 보안 요구사항

| ID | 요구사항 |
|---|---|
| NFR-4.1 | API 통신은 TLS 1.2 이상을 사용하며, 자체 서명 인증서 사용 시 CA 번들을 명시한다 |
| NFR-4.2 | API 키/토큰은 환경변수(`os.environ`) 또는 `python-dotenv`로 관리하며, 소스 코드에 하드코딩 금지 |
| NFR-4.3 | 로컬 캐시 디렉터리는 권한 600으로 설정한다 |

---

## 4. 메인 서버(JSP)와의 API 통신 규격 (Interface Requirements)

### 4.1 공통 사항

- **Base URL:** `https://{main-server-host}/api/v1`
- **인증 방식:** `Authorization: Bearer {JWT_TOKEN}` 또는 `X-API-Key: {API_KEY}`
- **공통 헤더:**
  ```
  X-Device-ID: edge-001
  X-Timestamp: 2026-05-14T10:30:00.123Z
  Content-Type: multipart/form-data; boundary=----...
  ```

### 4.2 [API-1] 차량 진입 이미지 전송

#### Request
```http
POST /api/v1/drivethrough/vehicle-entry
Content-Type: multipart/form-data
Authorization: Bearer eyJhbGciOi...
X-Device-ID: edge-001
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `event_id` | string (form) | Y | UUID v4 형식 이벤트 식별자 |
| `device_id` | string (form) | Y | 엣지 디바이스 ID |
| `captured_at` | string (form) | Y | ISO 8601 형식 타임스탬프 |
| `distance_cm` | number (form) | Y | 감지 시점 거리 (cm) |
| `image` | file (binary) | Y | JPEG 이미지 파일 (multipart) |

#### Response (200 OK)
```json
{
  "success": true,
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "received_at": "2026-05-14T10:30:01.456Z",
  "processing_id": "PROC-20260514-001",
  "message": "Image received, queued for YOLO/OCR processing"
}
```

#### Response (4xx/5xx)
```json
{
  "success": false,
  "error_code": "INVALID_IMAGE_FORMAT",
  "message": "Image must be JPEG format",
  "retry_after": 0
}
```

| 에러 코드 | HTTP | 설명 |
|---|---|---|
| `UNAUTHORIZED` | 401 | 인증 실패 |
| `INVALID_IMAGE_FORMAT` | 400 | 이미지 포맷 오류 |
| `PAYLOAD_TOO_LARGE` | 413 | 파일 크기 초과 (>10MB) |
| `SERVER_BUSY` | 503 | 서버 과부하 (retry_after 적용) |

---

### 4.3 [API-2] 음성 주문 데이터 전송

#### Request
```http
POST /api/v1/drivethrough/voice-order
Content-Type: multipart/form-data
Authorization: Bearer eyJhbGciOi...
X-Device-ID: edge-001
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `event_id` | string (form) | Y | 차량 진입 시 발급된 event_id (연관) |
| `device_id` | string (form) | Y | 엣지 디바이스 ID |
| `recorded_at` | string (form) | Y | 녹음 시작 시각 (ISO 8601) |
| `duration_ms` | number (form) | Y | 녹음 길이 (밀리초) |
| `sample_rate` | number (form) | Y | 샘플링 레이트 (Hz) |
| `audio` | file (binary) | Y | WAV 파일 (16kHz, 16-bit, mono) |

#### Response (200 OK)
```json
{
  "success": true,
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "received_at": "2026-05-14T10:30:35.789Z",
  "stt_job_id": "STT-20260514-001",
  "message": "Audio received, queued for STT processing"
}
```

---

### 4.4 [API-3] Heartbeat (장치 상태 보고)

#### Request
```http
POST /api/v1/edge/heartbeat
Content-Type: application/json
```

```json
{
  "device_id": "edge-001",
  "timestamp": "2026-05-14T10:30:00.000Z",
  "status": "HEALTHY",
  "metrics": {
    "cpu_usage": 12.3,
    "memory_usage_mb": 287,
    "disk_free_gb": 18.4,
    "uptime_sec": 86400,
    "pending_queue_size": 0
  },
  "device_status": {
    "sensor": "OK",
    "camera": "OK",
    "microphone": "OK"
  }
}
```

#### Response (200 OK)
```json
{
  "success": true,
  "next_heartbeat_sec": 60,
  "config_updated": false
}
```

---

## 5. 권장 Python 패키지 및 하드웨어 핀맵

### 5.1 Python 패키지 (`requirements.txt`)

```python
# === Hardware Control ===
gpiozero==2.0.1              # GPIO 추상화 (RPi.GPIO 대비 안정성↑)
RPi.GPIO==0.7.1              # gpiozero 백엔드
picamera2==0.3.19            # Raspberry Pi Camera 제어 (libcamera 기반)

# === Audio Processing ===
sounddevice==0.4.7           # 크로스플랫폼 오디오 캡처
pyaudio==0.2.14              # 대체 오디오 라이브러리
webrtcvad==2.0.10            # 음성 활동 감지 (VAD)
numpy==1.26.4                # 오디오 버퍼 처리

# === Network Communication ===
requests==2.32.3             # HTTP 클라이언트
urllib3==2.2.2               # 재시도 로직 (Retry, HTTPAdapter)
requests-toolbelt==1.0.0     # MultipartEncoder (대용량 파일 스트리밍)

# === Concurrency & Utilities ===
python-dotenv==1.0.1         # 환경변수 관리
pydantic==2.7.4              # 설정/스키마 검증
APScheduler==3.10.4          # 주기적 작업 스케줄링 (heartbeat 등)
tenacity==8.4.2              # 재시도 데코레이터

# === Logging & Monitoring ===
loguru==0.7.2                # 향상된 로깅
psutil==5.9.8                # 시스템 메트릭 수집

# === Development ===
pytest==8.2.2
pytest-mock==3.14.0
black==24.4.2
```

### 5.2 Raspberry Pi 4 GPIO 핀맵

#### 5.2.1 초음파 센서 (HC-SR04)

| 센서 핀 | Pi 물리 핀 | BCM GPIO | 비고 |
|---|---|---|---|
| VCC | Pin 2 | 5V | 전원 |
| GND | Pin 6 | GND | 접지 |
| TRIG | Pin 16 | **GPIO 23** | 트리거 출력 |
| ECHO | Pin 18 | **GPIO 24** | 에코 입력 (3.3V 변환 회로 필요)¹ |

> ¹ HC-SR04는 5V 신호를 출력하므로, ECHO 핀과 Pi 사이에 **저항 분압 회로**(1kΩ + 2kΩ) 또는 레벨 시프터를 반드시 적용한다.

#### 5.2.2 카메라 모듈

| 장치 | 포트 | 비고 |
|---|---|---|
| Raspberry Pi Camera v2/v3 | **CSI-2 포트** | 15-pin FFC 케이블 |

#### 5.2.3 마이크

| 장치 | 포트 | 비고 |
|---|---|---|
| USB 마이크 | USB 3.0 (권장) | 플러그앤플레이 |
| I2S 마이크 (선택) | GPIO 18/19/20/21 | PCM_CLK, PCM_FS, PCM_DIN |

#### 5.2.4 상태 표시 LED (선택)

| 색상 | Pi 물리 핀 | BCM GPIO | 용도 |
|---|---|---|---|
| Green | Pin 11 | GPIO 17 | 시스템 정상 |
| Yellow | Pin 13 | GPIO 27 | 차량 감지 중 |
| Red | Pin 15 | GPIO 22 | 네트워크/하드웨어 오류 |

### 5.3 디렉터리 구조 (권장)

```
/opt/edge-server/
├── src/
│   ├── main.py                    # 엔트리포인트
│   ├── config.py                  # 설정 관리 (pydantic)
│   ├── sensors/
│   │   └── ultrasonic.py          # HC-SR04 제어 + 필터링
│   ├── capture/
│   │   ├── camera.py              # picamera2 wrapper
│   │   └── microphone.py          # sounddevice wrapper + VAD
│   ├── communication/
│   │   ├── api_client.py          # REST 클라이언트
│   │   └── retry.py               # 재시도 로직
│   ├── core/
│   │   ├── event_bus.py           # 이벤트 디스패처
│   │   └── queue_manager.py       # 전송 큐 관리
│   └── utils/
│       └── logger.py
├── tests/
├── /var/lib/edge/cache/           # 로컬 캐시
│   ├── images/
│   └── audio/
├── /var/log/edge-server/          # 로그
├── .env                           # 환경변수 (API_KEY 등)
└── requirements.txt
```

### 5.4 시스템 서비스 등록 (systemd)

```ini
# /etc/systemd/system/edge-server.service
[Unit]
Description=Smart Drive-through Edge Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/edge-server
ExecStart=/opt/edge-server/venv/bin/python src/main.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/edge-server/stdout.log
StandardError=append:/var/log/edge-server/stderr.log

[Install]
WantedBy=multi-user.target
```

---

## 부록 A. 용어 정의

| 용어 | 정의 |
|---|---|
| **Edge Server** | 데이터 발생 지점에 가까운 곳에서 1차 처리를 수행하는 컴퓨팅 노드 |
| **VAD** | Voice Activity Detection, 음성 구간 검출 |
| **Hysteresis** | 임계점 부근에서의 채터링 방지를 위한 이력 현상 적용 |
| **Exponential Backoff** | 재시도 간격을 지수적으로 증가시키는 알고리즘 |

## 부록 B. 변경 이력

| 버전 | 일자 | 작성자 | 변경 내용 |
|---|---|---|---|
| 1.0 | 2026-05-14 | IoT Architecture Team | 최초 작성 |

---
