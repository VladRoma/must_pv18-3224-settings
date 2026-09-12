#!/usr/bin/env bash
set -euo pipefail

# Tailscale на Raspberry Pi. Той самий акаунт, що на ноуті.
# Після цього дашборд відкривається звідусіль: http://<tailscale-ip>:8080/

if ! command -v tailscale >/dev/null 2>&1; then
  echo "==> Встановлюю Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sudo sh
else
  echo "==> Tailscale уже встановлено"
fi

sudo systemctl enable --now tailscaled

if tailscale status >/dev/null 2>&1; then
  echo "Tailscale уже підключено."
else
  echo
  echo "Зараз відкриється логін. На ноуті/телефоні підтверди цей Pi в тому ж акаунті Tailscale."
  sudo tailscale up
fi

echo
echo "Адреса Pi в Tailscale:"
tailscale ip -4
echo
echo "З ноута (Tailscale увімкнений) відкрий:"
echo "  http://$(tailscale ip -4 | head -n1):8080/"
echo
echo "Ім'я в MagicDNS:  tailscale status"
