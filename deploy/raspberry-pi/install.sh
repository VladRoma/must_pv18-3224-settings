#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-https://github.com/VladRoma/must_pv18-3224-settings.git}"
TARGET="${TARGET:-$HOME/must_pv18-3224-settings}"
SERIAL_PORT="${SERIAL_PORT:-/dev/ttyUSB0}"
HTTP_PORT="${HTTP_PORT:-8080}"

echo "==> MUST PV18 · Raspberry Pi install"
echo "    repo:   $REPO"
echo "    target: $TARGET"
echo "    serial: $SERIAL_PORT"

sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip

if [[ ! -d "$TARGET/.git" ]]; then
  git clone "$REPO" "$TARGET"
else
  git -C "$TARGET" pull --ff-only
fi

python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install -U pip
"$TARGET/.venv/bin/pip" install -r "$TARGET/requirements.txt"

sudo usermod -aG dialout "$USER"

SERVICE="/etc/systemd/system/must-web.service"
sudo cp "$TARGET/deploy/raspberry-pi/must-web.service" "$SERVICE"
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$TARGET|" "$SERVICE"
sudo sed -i "s|ExecStart=.*|ExecStart=$TARGET/.venv/bin/python must_settings.py --web --lan --port $SERIAL_PORT --baud 19200 --slave 4 --http-port $HTTP_PORT|" "$SERVICE"
sudo sed -i "s|User=.*|User=$USER|" "$SERVICE"

sudo systemctl daemon-reload
sudo systemctl enable must-web.service
sudo systemctl restart must-web.service

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Готово."
echo "  Локально на Pi:  http://127.0.0.1:$HTTP_PORT/"
echo "  З телефону:      http://${IP:-<IP-Pi>}:$HTTP_PORT/"
echo
echo "Статус:  systemctl status must-web"
echo "Логи:    journalctl -u must-web -f"
echo
echo "Якщо порт інший — перевір: ls -l /dev/ttyUSB* /dev/ttyACM*"
