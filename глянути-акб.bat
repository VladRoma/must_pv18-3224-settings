@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MUST LP16-24200
echo.
echo  Зараз відкриється меню. Натисни 1 і Enter.
echo  Браузер відкриється сам, якщо батарея вже в домашньому Wi-Fi.
echo.
python must_battery.py
echo.
pause
