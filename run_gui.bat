@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
title OPPO Device Verification Portal

cd /d "%~dp0"

:: 1. Verify Python availability
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ======================================================================
    echo  [ERROR] Python is not detected in your system PATH!
    echo.
    echo  Please install Python 3.10 or newer from:
    echo  https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: During installation, be sure to check the box:
    echo  [X] "Add python.exe to PATH"
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: 2. Check dependencies (install automatically if missing)
python -c "import playwright, openpyxl, cv2, pandas" >nul 2>&1
if %errorlevel% neq 0 (
    echo ======================================================================
    echo  First-time launch setup detected.
    echo  Installing required Python libraries and Playwright browser...
    echo ======================================================================
    echo.
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m playwright install chromium
    echo.
    echo ======================================================================
    echo  Setup completed! Launching application...
    echo ======================================================================
    echo.
)

:: 3. Launch GUI application cleanly (without background black terminal window)
start "" pythonw gui_app.py
if %errorlevel% neq 0 (
    python gui_app.py
)

exit /b 0
