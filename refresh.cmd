@echo off
:: Nightshift Watchdog - install/refresh the OPTIONAL Windows service.
:: The watchdog also runs as a plain module (python -m nightshift.watchdog);
:: use this only if you want it always-on as a service. Self-elevates via UAC.
::
:: Override the interpreter if 'python' on PATH lacks pywin32, e.g.:
::   set "PY=C:\path\to\env\python.exe" && refresh.cmd

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

echo Installing service...
"%PY%" "%SVC%" --startup auto install

echo Starting service...
"%PY%" "%SVC%" start

echo.
echo Service refreshed (PythonNightshiftWatchdog).
echo.

pause
