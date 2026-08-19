@echo off
REM One-click launcher: installs what's needed, then opens the app in your browser.
title Credit Report Extractor
cd /d "%~dp0"

echo.
echo   Credit Report Extractor - starting up...
echo.

REM Find a working Python (the "py" launcher first, then plain "python").
set PY=
where py >nul 2>&1 && set PY=py
if "%PY%"=="" ( where python >nul 2>&1 && set PY=python )

if "%PY%"=="" (
  echo   Python is not installed.
  echo.
  echo   Install it from https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add Python to PATH" during setup, then run this file again.
  echo.
  pause
  exit /b 1
)

echo   Checking dependencies ^(first run takes a minute^)...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo   Could not install dependencies. Check your internet connection and try again.
  echo.
  pause
  exit /b 1
)

%PY% app.py

echo.
pause
