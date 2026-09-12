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
python must_settings.py --port COM8 --baud 19200 --slave 4 --web --http-port 8080 --bms-host 192.168.1.50
```

Веб-інтерфейс: http://127.0.0.1:8080/

Доступ з телефону в локальній мережі:

```powershell
python must_settings.py --web --lan
```

## АКБ MUST LP16-24200 по Wi‑Fi

Батарея має BMS **PACEEX / PeiCheng**. Після підключення модуля до домашнього 2.4 ГГц Wi‑Fi (додаток **BMS-Tool** або **Paceex BMS**, пароль адміна часто `4321`) модуль слухає **TCP 8888** у локальній мережі. Програма читає SOC, струм, комірки локально — без хмари.

Спочатку роздай Wi‑Fi з телефону, потім закрий додаток (модуль тримає лише одне з'єднання).

```powershell
# Знайти модуль у мережі
python must_battery.py --scan

# Одноразовий звіт
python must_battery.py --host 192.168.1.50

# Автооновлення
python must_battery.py --host 192.168.1.50 --watch 10

# JSON
python must_battery.py --host 192.168.1.50 --json

# Веб лише з АКБ
python must_battery.py --host 192.168.1.50 --web --lan
```

Разом з інвертором:

```powershell
python must_settings.py --web --lan --bms-host 192.168.1.50
```

IP модуля дивись у DHCP роутера (Bluetooth-ім'я зазвичай `PC-XXXX`). Зарезервуй адресу в DHCP, щоб вона не змінювалась.

## Веб-дашборд

- **Огляд** — SOC, потоки енергії, метрики PV / навантаження / мережа / BMS
- **Графік** — історія SOC і потужностей (оновлення кожні 5 с)
- **АКБ / CAN** — дані BMS через інвертор (рег. 109–114)
- **Енергія** — PV, мережа, навантаження, live-стан
- **Налаштування** — запис LCD-програм у інвертор (вкладка за паролем)

Оновлення даних:
- немає з'єднання → повтор кожні **5 с**
- підключено → автооновлення кожні **5 хв** або кнопка **↻ Оновити**
- вкладка **Графік** → кожні **5 с**

Вкладка **Налаштування** відкривається лише після пароля. Задай його так:

```powershell
python must_settings.py --web --lan --settings-password "твій-пароль"
```

Або змінна `MUST_SETTINGS_PASSWORD`. Якщо пароль не вказати, сервер сам створить його і покаже в консолі (файл `.must-settings-password`).

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
python must_settings.py --web --lan --port /dev/ttyUSB0 --bms-host 192.168.1.50
```

4. Автозапуск через systemd (рекомендовано):

```bash
chmod +x deploy/raspberry-pi/install.sh
./deploy/raspberry-pi/install.sh
```

Після цього дашборд доступний з телефону вдома: `http://<IP-Pi>:8080/`

IP Pi: `hostname -I`

Файли: `deploy/raspberry-pi/must-web.service`, `deploy/raspberry-pi/install.sh`

### Tailscale (доступ не з дому)

На Pi той самий акаунт Tailscale, що на ноуті. Дашборд уже слухає `0.0.0.0:8080`, тож окремий порт-форвардинг не потрібен.

```bash
chmod +x deploy/raspberry-pi/install-tailscale.sh
./deploy/raspberry-pi/install-tailscale.sh
```

Або разом з установкою дашборда (так і є за замовчуванням):

```bash
./deploy/raspberry-pi/install.sh
```

Далі на ноуті ввімкни Tailscale і відкрий:

```text
http://<tailscale-ip-Pi>:8080/
```

IP Pi в Tailscale: на Pi виконай `tailscale ip -4`.

Інвертор і батарея лишаються в домашній Wi‑Fi. Pi читає їх локально, а ти дивишся сторінку через Tailscale.

## Структура проєкту

| Файл | Призначення |
|------|-------------|
| `must_settings.py` | Modbus RTU, CLI, читання/запис |
| `must_battery.py` | Wi‑Fi PACEEX BMS (LP16-24200) |
| `must_web.py` | Веб-сервер + API |
| `web/` | HTML/CSS/JS дашборд |
| `deploy/raspberry-pi/` | Автозапуск на Pi + Tailscale |
