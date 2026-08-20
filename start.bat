@echo off
title PriceScout
cd /d "%~dp0"

echo.
echo  ==============================
echo   PriceScout - Starting...
echo  ==============================
echo.
echo  Opening browser at http://localhost:8000
echo  Press Ctrl+C to stop
echo.

:: Open browser after short delay
start "" "http://localhost:8000"

:: Try python3 first, then python
where python3 >nul 2>&1
if %errorlevel% equ 0 (
    python3 server.py
) else (
    python server.py
)

pause
