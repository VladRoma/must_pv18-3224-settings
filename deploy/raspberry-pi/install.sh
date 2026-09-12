#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-https://github.com/VladRoma/must_pv18-3224-settings.git}"
TARGET="${TARGET:-$HOME/must_pv18-3224-settings}"
SERIAL_PORT="${SERIAL_PORT:-/dev/ttyUSB0}"
HTTP_PORT="${HTTP_PORT:-8080}"
BMS_HOST="${BMS_HOST:-}"
BMS_PORT="${BMS_PORT:-8888}"
INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-1}"
SETTINGS_PASSWORD="${SETTINGS_PASSWORD:-}"

echo "==> MUST PV18 · Raspberry Pi install"
echo "    repo:   $REPO"
echo "    target: $TARGET"
echo "    serial: $SERIAL_PORT"
if [[ -n "$BMS_HOST" ]]; then
  echo "    АКБ:    $BMS_HOST:$BMS_PORT"
fi

sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip curl

if [[ ! -d "$TARGET/.git" ]]; then
  git clone "$REPO" "$TARGET"
else
  git -C "$TARGET" pull --ff-only
fi

python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install -U pip
"$TARGET/.venv/bin/pip" install -r "$TARGET/requirements.txt"

sudo usermod -aG dialout "$USER"

EXEC_START="$TARGET/.venv/bin/python must_settings.py --web --lan --port $SERIAL_PORT --baud 19200 --slave 4 --http-port $HTTP_PORT"
if [[ -n "$BMS_HOST" ]]; then
  EXEC_START+=" --bms-host $BMS_HOST --bms-port $BMS_PORT"
fi

if [[ -n "$SETTINGS_PASSWORD" ]]; then
  printf 'MUST_SETTINGS_PASSWORD=%s\n' "$SETTINGS_PASSWORD" > "$TARGET/.must-web.env"
  chmod 600 "$TARGET/.must-web.env"
fi

SERVICE="/etc/systemd/system/must-web.service"
sudo cp "$TARGET/deploy/raspberry-pi/must-web.service" "$SERVICE"
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$TARGET|" "$SERVICE"
sudo sed -i "s|ExecStart=.*|ExecStart=$EXEC_START|" "$SERVICE"
sudo sed -i "s|User=.*|User=$USER|" "$SERVICE"
sudo sed -i "s|EnvironmentFile=.*|EnvironmentFile=-$TARGET/.must-web.env|" "$SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable must-web.service
sudo systemctl restart must-web.service

if [[ "$INSTALL_TAILSCALE" == "1" ]]; then
  chmod +x "$TARGET/deploy/raspberry-pi/install-tailscale.sh"
  "$TARGET/deploy/raspberry-pi/install-tailscale.sh"
fi

LAN_IP="$(hostname -I | awk '{print $1}')"
TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"

echo
echo "Готово."
echo "  На Pi:              http://127.0.0.1:$HTTP_PORT/"
echo "  У домашньому Wi‑Fi: http://${LAN_IP:-<IP-Pi>}:$HTTP_PORT/"
if [[ -n "$TS_IP" ]]; then
  echo "  Через Tailscale:    http://$TS_IP:$HTTP_PORT/"
else
  echo "  Tailscale:          sudo tailscale up   потім: tailscale ip -4"
fi
echo
echo "Статус:  systemctl status must-web"
echo "Логи:    journalctl -u must-web -f"
echo
echo "Якщо порт інший — перевір: ls -l /dev/ttyUSB* /dev/ttyACM*"
echo "АКБ Wi‑Fi: BMS_HOST=192.168.0.50 ./deploy/raspberry-pi/install.sh"
echo "Пароль налаштувань: SETTINGS_PASSWORD='твій-пароль' ./deploy/raspberry-pi/install.sh"
