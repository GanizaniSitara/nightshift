@echo off
:: Nightshift Watchdog - deploy to C:\servers and (re)install the Windows service.
::
:: Run this from the SOURCE repo (e.g. C:\git\nightshift). It:
::   1. stops + removes any existing service BY NAME (must happen first - a running
::      service locks its own files, so you can't overwrite them until it's gone),
::   2. copies the runtime to C:\servers\nightshift,
::   3. installs + starts the service FROM the deployed copy.
::
:: Do NOT run the copy living under C:\servers\nightshift - run the source copy,
:: or step 2 would overwrite this script while it is executing.
::
:: Override the interpreter if needed:  set "PY=...python.exe" && refresh.cmd
:: NOTE: this does not touch the legacy PythonAgentRunnerWatchdog (different name);
::       remove that separately first to avoid two watchdogs running at once.

:: Self-elevate
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

setlocal
set "SRC=%~dp0"
set "DEST=C:\servers\nightshift"
if "%PY%"=="" set "PY=C:\miniconda3\envs\python312\python.exe"
set "SVC_SRC=%SRC%src\nightshift\watchdog\service_watchdog.py"
set "SVC_DEST=%DEST%\src\nightshift\watchdog\service_watchdog.py"

echo.
echo Stopping existing service (if installed)...
if exist "%SVC_DEST%" ( "%PY%" "%SVC_DEST%" stop ) else ( "%PY%" "%SVC_SRC%" stop )

echo Removing existing service (if installed)...
if exist "%SVC_DEST%" ( "%PY%" "%SVC_DEST%" remove ) else ( "%PY%" "%SVC_SRC%" remove )

:: Give SCM a moment to release file handles before overwriting the runtime.
timeout /t 2 /nobreak >nul

echo Deploying runtime to %DEST% ...
robocopy "%SRC%src" "%DEST%\src" /E /XD __pycache__ logs state /XF *.pyc /NFL /NDL /NJH /NJS /NP >nul
robocopy "%SRC%config" "%DEST%\config" /E /XF *.local.json /NFL /NDL /NJH /NJS /NP >nul
copy /Y "%SRC%remove.cmd" "%DEST%\remove.cmd" >nul

echo Installing service from %DEST% ...
"%PY%" "%SVC_DEST%" --startup auto install

echo Starting service...
"%PY%" "%SVC_DEST%" start

echo.
echo Service refreshed from %DEST% (PythonNightshiftWatchdog).
echo Config: %DEST%\config\nightshift.config.json  (create from the .example if absent)
echo.
endlocal
pause
