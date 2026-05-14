#!/usr/bin/env bash
# Raspberry Pi OS (Debian) 기반 엣지 서버 설치 스크립트.
# 사용:
#   sudo bash scripts/install.sh
#   sudo SERVICE_USER=yejin bash scripts/install.sh   # 사용자 명시
set -euo pipefail

APP_DIR="/opt/edge-server"
CACHE_DIR="/var/lib/edge/cache"
LOG_DIR="/var/log/edge-server"

# sudo로 호출한 원래 사용자(SUDO_USER) → 환경변수 SERVICE_USER → 'pi' 순서
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-pi}}"

if [[ $EUID -ne 0 ]]; then
  echo "이 스크립트는 sudo로 실행해야 합니다." >&2
  exit 1
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "사용자 '${SERVICE_USER}' 가 존재하지 않습니다." >&2
  echo "  → SERVICE_USER=<실제유저명> 으로 다시 실행하세요." >&2
  echo "  예) sudo SERVICE_USER=$(logname 2>/dev/null || echo myuser) bash $0" >&2
  exit 1
fi

SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
echo "→ 서비스 사용자: ${SERVICE_USER} (group=${SERVICE_GROUP})"

echo "[1/6] 시스템 패키지 설치"
apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  libatlas-base-dev libportaudio2 portaudio19-dev \
  libcamera-apps python3-libcamera python3-picamera2 \
  libgl1 libglib2.0-0 \
  ffmpeg

echo "[2/6] 앱 디렉터리 준비: ${APP_DIR}"
mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.venv' --exclude 'venv' \
  "$(dirname "$(readlink -f "$0")")/.." "${APP_DIR}/"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}"

echo "[3/6] 가상환경 생성 및 의존성 설치"
sudo -u "${SERVICE_USER}" python3 -m venv "${APP_DIR}/venv"
sudo -u "${SERVICE_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade pip
# picamera2/RPi.GPIO 등은 시스템 site-packages를 같이 보게 함
sudo -u "${SERVICE_USER}" "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[4/6] 캐시/로그/모델 디렉터리 권한 설정"
mkdir -p "${CACHE_DIR}/images" "${CACHE_DIR}/audio" "${LOG_DIR}" "${APP_DIR}/models"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${CACHE_DIR}" "${LOG_DIR}" "${APP_DIR}/models"
chmod 700 "${CACHE_DIR}"

echo "[5/6] .env 템플릿 복사"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
  echo "  → ${APP_DIR}/.env 를 편집하여 API_KEY/BASE_URL을 설정하세요."
fi

echo "[6/6] systemd 서비스 등록 (User/Group=${SERVICE_USER})"
sed -e "s|^User=.*|User=${SERVICE_USER}|" \
    -e "s|^Group=.*|Group=${SERVICE_GROUP}|" \
    "${APP_DIR}/systemd/edge-server.service" \
    > /etc/systemd/system/edge-server.service
chmod 644 /etc/systemd/system/edge-server.service
systemctl daemon-reload
systemctl enable edge-server.service

cat <<EOF

설치 완료. 다음 단계:

1) (선택) 번호판 검출용 YOLOv8 모델을 배치하세요:
     ${APP_DIR}/models/license_plate.pt   (또는 .onnx)
   모델이 없으면 검출 단계를 건너뛰고 원본 이미지만 메인 서버로 전송합니다.

2) ${APP_DIR}/.env 를 편집해 API_KEY / API_BASE_URL 을 설정합니다.

3) 서비스 시작:
     sudo systemctl start edge-server
     sudo systemctl status edge-server
     sudo journalctl -u edge-server -f

로그: ${LOG_DIR}/edge-server.log
캐시: ${CACHE_DIR}
모델 디렉터리: ${APP_DIR}/models
EOF
