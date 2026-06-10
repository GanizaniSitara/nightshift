@echo off
:: Nightshift Watchdog - stop and remove the optional Windows service.
:: Self-elevates via UAC.

:: Self-elevate
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
if "%PY%"=="" set "PY=python"
set "SVC=src\nightshift\watchdog\service_watchdog.py"

echo.
echo Stopping existing service...
"%PY%" "%SVC%" stop

echo Removing existing service...
"%PY%" "%SVC%" remove

echo.
echo Service removed (PythonNightshiftWatchdog).
echo.

pause
