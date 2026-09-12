# must_pv18-3224-settings

Читання телеметрії та зміна налаштувань інвертора **MUST PV18-3224 VPM II** через Modbus RTU (COM / USB‑Serial).

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

## Веб-дашборд

- **Огляд** — SOC, потоки енергії, метрики PV / навантаження / мережа / BMS
- **Графік** — історія SOC і потужностей (оновлення кожні 5 с)
- **АКБ / CAN** — дані BMS через інвертор (рег. 109–114)
- **Енергія** — PV, мережа, навантаження, live-стан
- **Налаштування** — запис LCD-програм у інвертор (з підтвердженням)

Оновлення даних:
- немає з'єднання → повтор кожні **5 с**
- підключено → автооновлення кожні **5 хв** або кнопка **↻ Оновити**
- вкладка **Графік** → кожні **5 с**

## Запис налаштувань (CLI)

```powershell
# Список програм, доступних для запису
python must_settings.py --list-settings

# Записати значення (номер LCD-програми)
python must_settings.py --set 17 28.4
python must_settings.py --set 14 USE
python must_settings.py --set 37 SOC
```

⚠️ Запис змінює регістри інвертора — перевір значення перед збереженням (особливо напруги АКБ).

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

IP Pi: `hostname -I`

Файли: `deploy/raspberry-pi/must-web.service`, `deploy/raspberry-pi/install.sh`

## Структура проєкту

| Файл | Призначення |
|------|-------------|
| `must_settings.py` | Modbus RTU, CLI, читання/запис |
| `must_web.py` | Веб-сервер + API |
| `web/` | HTML/CSS/JS дашборд |
| `must_gui.py` | Tkinter GUI (опційно) |
