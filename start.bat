@echo off
title PriceScout
echo.
echo  ==============================
echo   PriceScout — Starting...
echo  ==============================
echo.
echo  Opening browser at http://localhost:8000
echo  Press Ctrl+C to stop
echo.

:: Try python3 first, then python
where python3 >nul 2>&1
if %errorlevel% equ 0 (
    start "" http://localhost:8000
    python3 server.py
) else (
    start "" http://localhost:8000
    python server.py
)

pause
