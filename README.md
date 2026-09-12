# must_pv18-3224-settings

Читання налаштувань та телеметрії інвертора **MUST PV18-3224 VPM II** через Modbus RTU (COM-порт).

## Встановлення

```powershell
pip install -r requirements.txt
```

## Запуск

```powershell
# CLI — одноразовий звіт
python must_settings.py

# CLI — автооновлення
python must_settings.py --watch 5

# Веб-дашборд (рекомендовано)
python must_settings.py --web

# Віконний GUI (tkinter)
python must_settings.py --gui
```

За замовчуванням: **COM8**, **19200 8N1**, **slave 4**.

## Параметри

```powershell
python must_settings.py --port COM8 --baud 19200 --slave 4 --web --http-port 8080
```

Веб-інтерфейс: http://127.0.0.1:8080/

Доступ з телефону в локальній мережі:

```powershell
python must_settings.py --web --lan
```

Лише **читання** регістрів — нічого не записується в інвертор.

## Raspberry Pi 5 (сервер 24/7)

Схема: **інвертор → USB‑RS485/CH340 → Pi → Wi‑Fi → телефон/ПК**.

1. Клонуй репозиторій на Pi і встанови залежності:

```bash
git clone https://github.com/VladRoma/must_pv18-3224-settings.git
cd must_pv18-3224-settings
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo usermod -aG dialout $USER   # потім перелогінитись
```

2. Знайди serial-порт (зазвичай `/dev/ttyUSB0`):

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
```

3. Запуск вручну:

```bash
python must_settings.py --web --lan --port /dev/ttyUSB0
```

4. Автозапуск через systemd (рекомендовано):

```bash
chmod +x deploy/raspberry-pi/install.sh
./deploy/raspberry-pi/install.sh
```

Після цього дашборд доступний з телефону: `http://<IP-Pi>:8080/`

IP Pi дізнаєшся командою `hostname -I`.

Файли: `deploy/raspberry-pi/must-web.service`, `deploy/raspberry-pi/install.sh`
