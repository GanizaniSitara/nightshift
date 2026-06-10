@echo off
:: Nightshift Watchdog - stop and remove the Windows service (by name).
:: Location-independent: works run from the source repo or from C:\servers\nightshift.
:: Self-elevates via UAC. Removal is by service name, so it works regardless of
:: which path the service was installed from.

:: Self-elevate
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

setlocal
if "%PY%"=="" set "PY=C:\miniconda3\envs\python312\python.exe"
set "SVC=%~dp0src\nightshift\watchdog\service_watchdog.py"

echo.
echo Stopping existing service...
"%PY%" "%SVC%" stop

echo Removing existing service...
"%PY%" "%SVC%" remove

echo.
echo Service removed (PythonNightshiftWatchdog).
echo.
endlocal
pause
