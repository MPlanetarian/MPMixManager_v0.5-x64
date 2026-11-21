@echo off
setlocal enabledelayedexpansion

:: ==============================================================================
:: MP Liquid Glass Audio Player - Windows Launcher (Windows 10 & 11)
:: Launches the standalone Liquid Glass player with user-configured backend
:: (OpenGL [Default] or Vulkan).
:: ==============================================================================

cd /d "%~dp0\.."
set "BASE_DIR=%CD%"

:: Check for Python. windows_find_python.cmd skips the Microsoft Store alias.
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
:: pythonw for the host and the companion: python.exe would allocate a visible
:: console for each, which is the stray terminal window on Windows 10. The
:: player's own terminal UI is a separate CREATE_NEW_CONSOLE child that the
:: glass embeds, so the host itself must have no console of its own.
set "PYTHONW_BIN=%PYTHON_BIN%"
if /I "%PYTHON_BIN:~-10%"=="python.exe" (
    set "PYTHONW_TRY=!PYTHON_BIN:python.exe=pythonw.exe!"
    if exist "!PYTHONW_TRY!" set "PYTHONW_BIN=!PYTHONW_TRY!"
)

:: Check config.env for LIQUID_GLASS_BACKEND
set "BACKEND=opengl"
set "CONFIG_FILE=%USERPROFILE%\.config\mix-manager\config.env"
if not exist "%CONFIG_FILE%" set "CONFIG_FILE=%BASE_DIR%\config.env"

if exist "%CONFIG_FILE%" (
    for /f "tokens=1,2 delims==" %%A in ('type "%CONFIG_FILE%" ^| findstr /r "^LIQUID_GLASS_BACKEND="') do (
        set "VAL=%%B"
        set "VAL=!VAL:"=!"
        set "VAL=!VAL:'=!"
        set "VAL=!VAL: =!"
        if not "!VAL!"=="" set "BACKEND=!VAL!"
    )
)

:: The setup prompt and the Settings menu can turn off the background MP
:: Visualizer. Unset/unknown keeps the historical default (enabled).
set "VISUALIZER_COMPANION=1"
if exist "%CONFIG_FILE%" (
    for /f "tokens=1,2 delims==" %%A in ('type "%CONFIG_FILE%" ^| findstr /r "^MP_VISUALIZER_COMPANION_ENABLED="') do (
        set "VCOMP=%%B"
        set "VCOMP=!VCOMP:"=!"
        set "VCOMP=!VCOMP:'=!"
        set "VCOMP=!VCOMP: =!"
        if /i "!VCOMP!"=="0" set "VISUALIZER_COMPANION=0"
        if /i "!VCOMP!"=="no" set "VISUALIZER_COMPANION=0"
        if /i "!VCOMP!"=="false" set "VISUALIZER_COMPANION=0"
        if /i "!VCOMP!"=="off" set "VISUALIZER_COMPANION=0"
        if /i "!VCOMP!"=="disabled" set "VISUALIZER_COMPANION=0"
    )
)

:: Select target script (OpenGL default, or Vulkan if configured)
set "TARGET_PY=%BASE_DIR%\MP_Audio_Player_Liquid_Glass_OpenGL.py"
if /i "%BACKEND%"=="vulkan" (
    if exist "%BASE_DIR%\MP_Audio_Player_Liquid_Glass.py" (
        set "TARGET_PY=%BASE_DIR%\MP_Audio_Player_Liquid_Glass.py"
    )
)

if not exist "%TARGET_PY%" (
    if exist "%BASE_DIR%\MP_Audio_Player_Liquid_Glass_OpenGL.py" (
        set "TARGET_PY=%BASE_DIR%\MP_Audio_Player_Liquid_Glass_OpenGL.py"
    ) else if exist "%BASE_DIR%\MP_Audio_Player_Liquid_Glass.py" (
        set "TARGET_PY=%BASE_DIR%\MP_Audio_Player_Liquid_Glass.py"
    )
)

if not exist "%TARGET_PY%" (
    echo [ERROR] Liquid Glass player script could not be found in "%BASE_DIR%".
    pause
    exit /b 1
)

:: MP Liquid Player opens at most four windows. A fifth launch focuses one.
"%PYTHON_BIN%" "%BASE_DIR%\scripts\mp_instance_limit.py" check player >nul 2>&1
if errorlevel 3 (
    "%PYTHON_BIN%" "%BASE_DIR%\scripts\mp_instance_limit.py" focus player >nul 2>&1
    "%PYTHON_BIN%" "%BASE_DIR%\scripts\mp_instance_limit.py" notify player >nul 2>&1
    exit /b 0
)

:: Set environment and execute
set "MP_LIQUID_GLASS=1"
set "MP_LIQUID_GLASS_PLATFORM=win32"
set "PYTHONIOENCODING=utf-8"

:: The first MP Liquid Player window of a session plays Exit_MP_Video.mp4
:: before its glass appears. Run it here, before the companion visualizer, so
:: the movie is the only thing on screen. Windows two to four skip it, and the
:: host repeats the election for launch paths that skip this file. Helper
:: flags (--self-test, --embed-check, --tracklist, ...) draw no player window,
:: so they get no movie. The helper exits 0 only when it actually played.
set "MP_INTRO_WANTED=1"
for %%A in (%*) do (
    if /I "%%~A"=="--self-test" set "MP_INTRO_WANTED=0"
    if /I "%%~A"=="--embed-check" set "MP_INTRO_WANTED=0"
    if /I "%%~A"=="--list-fonts" set "MP_INTRO_WANTED=0"
    if /I "%%~A"=="--manager" set "MP_INTRO_WANTED=0"
    if /I "%%~A"=="--set-font" set "MP_INTRO_WANTED=0"
    if /I "%%~A"=="--tracklist" set "MP_INTRO_WANTED=0"
)
if "%MP_INTRO_WANTED%"=="1" (
    "%PYTHON_BIN%" -c "import sys; sys.path.insert(0, r'%BASE_DIR%\scripts'); import exit_video; raise SystemExit(0 if exit_video.play_player_intro('NORMAL') else 1)" >nul 2>&1
    if not errorlevel 1 set "MP_PLAYER_INTRO_DONE=1"
)

:: Automatically start MP Liquid Visualizer with a random preset if not already running
set "VIS_PY=%BASE_DIR%\MP_Liquid_Visualizer.py"
if not exist "%VIS_PY%" set "VIS_PY=%BASE_DIR%\scripts\mp_liquid_visualizer_vulkan.py"
if "!VISUALIZER_COMPANION!"=="1" if exist "%VIS_PY%" (
    set "VIS_RUNNING=0"
    for /f "tokens=*" %%P in ('powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*MP_Liquid_Visualizer*' -or $_.CommandLine -like '*mp_liquid_visualizer_vulkan*') } | Select-Object -ExpandProperty ProcessId" 2^>nul') do (
        if not "%%P"=="" set "VIS_RUNNING=1"
    )
    if "!VIS_RUNNING!"=="0" (
        set "VIS_BACKEND=opengl"
        if /i "!BACKEND!"=="vulkan" set "VIS_BACKEND=vulkan"
        set "MP_VISUALIZER_COMPANION=1"
        start "" "%PYTHONW_BIN%" "%VIS_PY%" --preset random --backend !VIS_BACKEND!
        set "MP_VISUALIZER_COMPANION="
    )
)

start "" "%PYTHONW_BIN%" "%TARGET_PY%" %*
exit /b 0
