@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

set "IP="
set "PORT="
set "CLEAN_LINE="
set "PYTHONIOENCODING=utf-8"
set "SCAN_LOG=%TEMP%\must_scan.txt"

echo Пошук батареї в мережі...
python "C:\Users\armyv\Documents\must\must_battery.py" --scan > "%SCAN_LOG%" 2>&1
type "%SCAN_LOG%"
echo.

:: Рядок виглядає так: "Знайдено: 192.168.0.2:8888"
:: Шукаємо IPv4:порт (findstr погано розуміє український текст)
for /f "usebackq tokens=2 delims= " %%i in (`findstr /R "[0-9][0-9]*[.][0-9][0-9]*[.][0-9][0-9]*[.][0-9][0-9]*:[0-9]" "%SCAN_LOG%"`) do (
    if not defined CLEAN_LINE set "CLEAN_LINE=%%i"
)

if defined CLEAN_LINE (
    for /f "tokens=1,2 delims=:" %%a in ("!CLEAN_LINE!") do (
        set "IP=%%a"
        set "PORT=%%b"
    )
)

if not defined IP (
    echo [ПОМИЛКА] Пристрій не знайдено у мережі.
    goto :end
)

echo Дані успішно зчитано:
echo IP-адреса: !IP!
echo Порт:      !PORT!

echo.
echo Запуск наступної команди з параметрами...
python "C:\Users\armyv\Documents\must\must_settings.py" --web --lan --bms-host !IP! --bms-port !PORT! --http-port 8000 --settings-password "2104220"

:end
endlocal
pause
