@echo off
chcp 65001 > nul
title Building Standalone OPPO_Checker.exe...

cd /d "%~dp0"
python build_exe.py

echo.
pause
