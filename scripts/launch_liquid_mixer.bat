@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: ==============================================================================
:: MP Liquid Mixer 0.0.1 - Windows 10 & 11 launcher
:: Opens the native Tkinter four-channel mixer, the Kontrol S4/S3 MIDI profiles,
:: the MP Liquid Visualizer companion behind it, and the shared theme file.
:: ==============================================================================

cd /d "%~dp0\.."
set "BASE_DIR=%CD%"

if not exist "%BASE_DIR%\MP_Liquid_Mixer_Windows_10.py" (
    echo [ERROR] MP_Liquid_Mixer_Windows_10.py was not found in "%BASE_DIR%".
    pause
    exit /b 1
)

set "PYTHON_BIN="
for /f "delims=" %%P in ('call "%~dp0windows_find_python.cmd"') do (
    if not defined PYTHON_BIN set "PYTHON_BIN=%%P"
)
if not defined PYTHON_BIN (
    echo [ERROR] Python was not found in PATH or standard installation folders.
    echo Please install Python 3.10+ from python.org or the Microsoft Store.
    pause
    exit /b 1
)

set "PYTHONW_BIN=%PYTHON_BIN%"
if /I "%PYTHON_BIN:~-10%"=="python.exe" (
    set "PYTHONW_TRY=!PYTHON_BIN:python.exe=pythonw.exe!"
    if exist "!PYTHONW_TRY!" set "PYTHONW_BIN=!PYTHONW_TRY!"
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "MP_LIQUID_MIXER=1"

start "" "%PYTHONW_BIN%" "%BASE_DIR%\MP_Liquid_Mixer_Windows_10.py" %*
exit /b 0
