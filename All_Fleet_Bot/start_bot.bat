@echo off
title NowTrading 2.1 - Quant Trading Bot
color 0A

echo =======================================================
echo          NOWTRADING 2.1 - INITIALIZING BOT...
echo =======================================================
echo.

:: Ensure we are in the correct directory
cd /d "%~dp0"

:: Set utf-8 encoding for proper emoji display
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

:: Check if MetaTrader 5 Terminal is running; if not, launch GUI
set "MT5_DIR=C:\Program Files\MetaTrader 5 EXNESS"
set "MT5_EXE=%MT5_DIR%\terminal64.exe"
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I /N "terminal64.exe">NUL
if "%ERRORLEVEL%"=="1" (
    echo [INFO] Launching MetaTrader 5 GUI Terminal...
    if exist "%MT5_EXE%" (
        start "" /d "%MT5_DIR%" "%MT5_EXE%"
        echo [INFO] Waiting for MetaTrader 5 to load...
        ping 127.0.0.1 -n 7 > nul
    ) else (
        echo [WARNING] MT5 executable not found at "%MT5_EXE%"
    )
) else (
    echo [INFO] MetaTrader 5 Terminal is already running.
)

:: Auto-restart loop (max 50 restarts to prevent infinite crash loops)
set /a RESTART_COUNT=0
set MAX_RESTARTS=50
set RESTART_DELAY=30

:start_loop
set /a RESTART_COUNT+=1

if %RESTART_COUNT% GTR %MAX_RESTARTS% (
    echo.
    echo =======================================================
    echo  ERROR: Max restarts reached [%MAX_RESTARTS%]. Bot will NOT restart.
    echo  Please check logs and fix the issue manually.
    echo =======================================================
    pause > nul
    exit /b 1
)

echo.
echo [%date% %time%] Starting bot (attempt %RESTART_COUNT%/%MAX_RESTARTS%)...
echo.

python run_unified_bot.py

:: Check exit code
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 (
    echo.
    echo =======================================================
    echo  Bot stopped gracefully [exit code 0]. Not restarting.
    echo =======================================================
    pause > nul
    exit /b 0
)

echo.
echo =======================================================
echo  Bot crashed with exit code %EXIT_CODE%.
echo  Restarting in %RESTART_DELAY% seconds...
echo  (Press Ctrl+C to cancel restart)
echo =======================================================

:: Wait before restart (allows user to cancel)
timeout /t %RESTART_DELAY% /nobreak > nul 2>&1 || ping 127.0.0.1 -n 31 > nul

goto start_loop
