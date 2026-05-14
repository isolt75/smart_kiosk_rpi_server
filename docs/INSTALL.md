# 설치 및 운영 가이드

대상 OS: **Raspberry Pi OS (Bookworm, Debian 12 기반) / 64-bit 권장**

## 1. 하드웨어 연결

[SRS §5.2](SRS.md#52-raspberry-pi-4-gpio-핀맵) 핀맵 참고. 요점:

| 부품 | 연결 |
|---|---|
| HC-SR04 VCC/GND | Pin 2 (5V) / Pin 6 (GND) |
| HC-SR04 TRIG | Pin 16 (BCM 23) |
| HC-SR04 ECHO | Pin 18 (BCM 24) — **반드시 1kΩ+2kΩ 분압** |
| Pi Camera v2/v3 | CSI-2 포트 |
| USB 마이크 | USB 3.0 권장 |
| LED Green/Yellow/Red | BCM 17 / 27 / 22 (옵션) |

## 2. 자동 설치

```bash
git clone https://github.com/isolt75/smart_kiosk_rpi_server.git
cd smart_kiosk_rpi_server
sudo bash scripts/install.sh
```

스크립트가 처리하는 것:
- 시스템 패키지 (`python3-picamera2`, `portaudio19-dev`, `libcamera-apps` 등)
- `/opt/edge-server`에 소스 복사
- `venv` 생성 + `requirements.txt` 설치
- `/var/lib/edge/cache`, `/var/log/edge-server` 생성 (퍼미션 700)
- `/etc/systemd/system/edge-server.service` 등록 (자동 시작)

## 3. 환경변수 설정

`/opt/edge-server/.env` 파일을 편집:

```bash
sudo -u pi nano /opt/edge-server/.env
```

필수 값:
- `EDGE_API_BASE_URL` — 메인 서버 base URL (예: `https://main.example.com/api/v1`)
- `EDGE_API_KEY` 또는 `EDGE_API_JWT` — 둘 중 하나
- `EDGE_DEVICE_ID` — 기본 `edge-001`

자체 서명 인증서를 쓰는 경우 `EDGE_CA_BUNDLE`에 CA 번들 경로 지정.

## 4. 서비스 운영

```bash
sudo systemctl start edge-server
sudo systemctl status edge-server
sudo journalctl -u edge-server -f          # 실시간 로그
tail -f /var/log/edge-server/edge-server.log
```

## 5. 수동 실행 (디버깅)

```bash
cd /opt/edge-server
source venv/bin/activate
python -m src.main
```

## 6. 카메라/마이크 확인

```bash
# 카메라
libcamera-hello -t 2000

# 마이크 목록
arecord -l
# 5초 녹음 테스트
arecord -D plughw:1,0 -f S16_LE -c 1 -r 16000 -d 5 /tmp/test.wav
```

## 7. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| `gpiozero` import 실패 | `sudo apt install python3-gpiozero` 또는 venv 생성 시 `--system-site-packages` |
| `picamera2` import 실패 | `python3-picamera2` 미설치. `apt install python3-picamera2` 후 venv 재생성 |
| HC-SR04 측정값 0 또는 max | 분압 회로 확인, ECHO 라인 잡음, 1초 타임아웃 시 로그 확인 |
| HTTP 401 | `.env`의 `EDGE_API_KEY`/`EDGE_API_JWT` 확인 |
| Disk full | `EDGE_CACHE_MAX_BYTES`/`EDGE_CACHE_MAX_AGE_HOURS` 줄이기 |
