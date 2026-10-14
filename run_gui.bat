@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
title OPPO Device Verification Portal

cd /d "%~dp0"

:: 1. Detect Python executable (checks 'python' and 'py' launcher)
set "PY_CMD="
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY_CMD=py"
    )
)

if "%PY_CMD%"=="" (
    echo ======================================================================
    echo  [ERROR] Python is not installed or not found in system PATH.
    echo.
    echo  Please install Python 3.10 or newer from:
    echo  https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: During installation, make sure to check:
    echo  [X] "Add python.exe to PATH"
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: 2. Check if all required libraries are installed
%PY_CMD% -c "import playwright, openpyxl, cv2, pandas, tqdm, requests, numpy" >nul 2>&1
if %errorlevel% neq 0 (
    echo ======================================================================
    echo  First-time launch detected!
    echo  Automatically installing required Python libraries...
    echo ======================================================================
    echo.
    %PY_CMD% -m pip install --upgrade pip
    %PY_CMD% -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo  [ERROR] Failed to install libraries. Please check internet connection.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo ======================================================================
    echo  Installing Playwright Chromium browser engine...
    echo ======================================================================
    echo.
    %PY_CMD% -m playwright install chromium
    if %errorlevel% neq 0 (
        echo  [Notice] Browser setup can also be run inside the app via 'Setup Browser'.
    )
    echo.
    echo ======================================================================
    echo  All libraries and dependencies installed successfully!
    echo ======================================================================
    echo.
    timeout /t 2 >nul
)

:: 3. Launch GUI application cleanly (without lingering black console)
start "" %PY_CMD%w gui_app.py 2>nul
if %errorlevel% neq 0 (
    %PY_CMD% gui_app.py
)

exit /b 0
