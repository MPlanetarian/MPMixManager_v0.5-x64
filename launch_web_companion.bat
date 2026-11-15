@echo off
rem ============================================================================
rem  launch_web_companion.bat - start the MP Web Companion on Windows
rem ---------------------------------------------------------------------------
rem  Windows has no systemd or launchd, so the companion there is an ordinary
rem  background process started by a shortcut in the user's Startup folder.
rem  The installer writes that shortcut (scripts/windows_user_integration.ps1),
rem  so the dashboard comes back after every login just like on Linux and macOS.
rem
rem  Usage:
rem    launch_web_companion.bat            start it in the background
rem    launch_web_companion.bat --fg       run in this console (diagnostics)
rem    launch_web_companion.bat --stop     stop the running companion
rem    launch_web_companion.bat --status   report whether it is running
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
rem A Windows console is cp1252 unless told otherwise, and the companion's
rem banner and log lines contain emoji. Without this the server can die on its
rem first print (UnicodeEncodeError) before it binds the port. The Python side
rem also reconfigures its streams; this covers child processes it spawns.
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"

set "SERVER=%BASE_DIR%\scripts\mix_web_companion.py"
if not exist "%SERVER%" (
    echo Error: scripts\mix_web_companion.py was not found.
    exit /b 1
)

set "ACTION=start"
if /I "%~1"=="--fg"   set "ACTION=fg"
if /I "%~1"=="--stop" set "ACTION=stop"
if /I "%~1"=="--status" set "ACTION=status"

rem --- locate a real interpreter (never the Store alias) ----------------------
set "PYTHON_BIN="
if exist "%BASE_DIR%\venv\Scripts\python.exe" (
    "%BASE_DIR%\venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_BIN=%BASE_DIR%\venv\Scripts\python.exe"
)
if not defined PYTHON_BIN (
    where py.exe >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py.exe -3 -c "import sys; print(sys.executable)" 2^>nul') do (
            if exist "%%~P" set "PYTHON_BIN=%%~P"
        )
    )
)
if not defined PYTHON_BIN (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
        if exist "%%~D\python.exe" set "PYTHON_BIN=%%~D\python.exe"
    )
)
if not defined PYTHON_BIN (
    for /f "delims=" %%P in ('where python.exe 2^>nul') do (
        echo %%P | findstr /I /C:"\WindowsApps\" >nul
        if errorlevel 1 if not defined PYTHON_BIN set "PYTHON_BIN=%%P"
    )
)
if not defined PYTHON_BIN (
    echo Error: no Python 3.8+ interpreter was found. Install Python and re-run the installer.
    exit /b 1
)

rem --- --stop / --status -----------------------------------------------------
if /I "%ACTION%"=="stop" (
    for /f "tokens=2 delims=," %%P in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO CSV /NH 2^>nul') do (
        wmic process where "ProcessId=%%~P" get CommandLine 2>nul | findstr /I /C:"mix_web_companion" >nul
        if not errorlevel 1 taskkill /F /PID %%~P >nul 2>&1
    )
    for /f "tokens=2 delims=," %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2^>nul') do (
        wmic process where "ProcessId=%%~P" get CommandLine 2>nul | findstr /I /C:"mix_web_companion" >nul
        if not errorlevel 1 taskkill /F /PID %%~P >nul 2>&1
    )
    echo MP Web Companion stopped.
    exit /b 0
)
if /I "%ACTION%"=="status" (
    set "FOUND="
    for /f "tokens=2 delims=," %%P in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO CSV /NH 2^>nul') do (
        wmic process where "ProcessId=%%~P" get CommandLine 2>nul | findstr /I /C:"mix_web_companion" >nul
        if not errorlevel 1 set "FOUND=%%~P"
    )
    if defined FOUND (echo MP Web Companion is RUNNING [pid !FOUND!]) else (echo MP Web Companion is STOPPED)
    exit /b 0
)

rem --- host / port ------------------------------------------------------------
rem Mirror scripts/mix_web_companion.sh: source config.env when present, then
rem default the host to 0.0.0.0 so the phone/tablet dashboard works on the LAN
rem exactly as it does on Linux and macOS. (The bare Python default is
rem loopback; the wrapper is what opts into LAN, and Windows has no wrapper.)
if exist "%BASE_DIR%\config.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%BASE_DIR%\config.env") do (
        set "_mpk=%%A"
        set "_mpk=!_mpk: =!"
        if /I "!_mpk!"=="WEB_COMPANION_HOST" (
            set "_mpv=%%B"
            set "_mpv=!_mpv:"=!"
            set "WEB_COMPANION_HOST=!_mpv: =!"
        )
        if /I "!_mpk!"=="WEB_COMPANION_PORT" (
            set "_mpv=%%B"
            set "_mpv=!_mpv:"=!"
            set "WEB_COMPANION_PORT=!_mpv: =!"
        )
    )
)
if not defined WEB_COMPANION_HOST set "WEB_COMPANION_HOST=0.0.0.0"
if not defined WEB_COMPANION_PORT set "WEB_COMPANION_PORT=8888"

set "COMPANION_ARGS=--host %WEB_COMPANION_HOST% --port %WEB_COMPANION_PORT%"

if /I "%ACTION%"=="fg" (
    "%PYTHON_BIN%" "%SERVER%" %COMPANION_ARGS%
    exit /b %ERRORLEVEL%
)

rem --- background start ------------------------------------------------------
rem Never stack a second server. The Startup shortcut runs at every login AND
rem the installer starts it now, so without this guard two processes end up
rem bound to the same port (Windows allows it with SO_REUSEADDR, and then the
rem two disagree about the stream queue and the phone gets a different answer
rem depending on which one wins accept()).
set "ALREADY="
for /f "tokens=2 delims=," %%P in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO CSV /NH 2^>nul') do (
    wmic process where "ProcessId=%%~P" get CommandLine 2>nul | findstr /I /C:"mix_web_companion" >nul
    if not errorlevel 1 set "ALREADY=%%~P"
)
if defined ALREADY (
    echo MP Web Companion is already running [pid !ALREADY!]
    exit /b 0
)

rem pythonw.exe keeps the console from flashing on every login. Fall back to
rem python.exe behind `start /B` if this interpreter has no pythonw.
set "PYTHONW=%PYTHON_BIN:python.exe=pythonw.exe%"
if exist "%PYTHONW%" (
    start "" /B "%PYTHONW%" "%SERVER%" %COMPANION_ARGS%
) else (
    start "" /B "%PYTHON_BIN%" "%SERVER%" %COMPANION_ARGS%
)
exit /b 0
