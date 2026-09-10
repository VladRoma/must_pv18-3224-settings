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

Лише **читання** регістрів — нічого не записується в інвертор.
