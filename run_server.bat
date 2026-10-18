: << 'SH_COMMENT'
@echo off
REM ======================================================================
REM  OPPO Sales Activation Monitor - Windows Server Launcher
REM ======================================================================
cd /d "%~dp0"
title OPPO Sales Activation Monitor Server

echo ======================================================================
echo  OPPO Sales Activation Monitor Server (Windows Launcher)
echo ======================================================================

IF EXIST ".venv\Scripts\python.exe" (
    SET "PY_CMD=.venv\Scripts\python.exe"
) ELSE (
    SET "PY_CMD=python"
)

echo Starting server with %PY_CMD%...
echo.
echo Mobile access: Open your phone browser and go to your PC's IP port 8000
echo Local access:  http://localhost:8000
echo.

%PY_CMD% -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
exit /b %ERRORLEVEL%

SH_COMMENT

# Polyglot Linux Fallback
exec ./run_server.sh "$@"
