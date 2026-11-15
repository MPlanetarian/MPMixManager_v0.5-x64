@echo off
setlocal enabledelayedexpansion

:: ==============================================================================
:: MP Gui - Windows Launcher (Windows 10 & 11)
:: Opens Mix Archive Manager in the Liquid Glass window.
:: Uses windows_find_python.cmd so the Microsoft Store python3 alias is skipped.
:: ==============================================================================

cd /d "%~dp0\.."
set "BASE_DIR=%CD%"

set "PYTHON_BIN="
for /f "delims=" %%P in ('call "%~dp0windows_find_python.cmd"') do (
    if not defined PYTHON_BIN set "PYTHON_BIN=%%P"
)

if not defined PYTHON_BIN (
    echo [ERROR] Python was not found in PATH or standard installation folders.
    echo The project venv, Python 3.10+, or Anaconda is required.
    pause
    exit /b 1
)

set "KEEP_THEME=0"
set "NO_VISUALIZER=0"
set "EXTRA="
for %%A in (%*) do (
    if /I "%%~A"=="--keep-theme" (
        set "KEEP_THEME=1"
    ) else if /I "%%~A"=="--no-visualizer" (
        set "NO_VISUALIZER=1"
    ) else (
        set "EXTRA=!EXTRA! %%~A"
    )
)

set "CONFIG_DIR=%USERPROFILE%\.config\mix-manager"
if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%" >nul 2>&1

:: MP Gui opens at most four windows. A fifth launch focuses one instead.
set "MP_INSTANCE_STATUS=0"
"%PYTHON_BIN%" "%BASE_DIR%\scripts\mp_instance_limit.py" check gui >nul 2>&1
set "MP_INSTANCE_STATUS=%ERRORLEVEL%"
if "%MP_INSTANCE_STATUS%"=="3" (
    "%PYTHON_BIN%" "%BASE_DIR%\scripts\mp_instance_limit.py" focus gui >nul 2>&1
    "%PYTHON_BIN%" "%BASE_DIR%\scripts\mp_instance_limit.py" notify gui >nul 2>&1
    exit /b 0
)

:: Keep the theme the user picked. A fresh install starts on the default.
if "!KEEP_THEME!"=="0" (
    if not exist "%CONFIG_DIR%\theme" (
        echo dreamworlds_ultra> "%CONFIG_DIR%\theme"
    )
)

start "" /B "%PYTHON_BIN%" -c "import sys; sys.path.insert(0, r'%BASE_DIR%\scripts'); from desktop_launch_screen import reposition_off_hisense; reposition_off_hisense()" >nul 2>&1

:: The first MP Gui window plays Exit_MP_Video.mp4 once, before the companion
:: visualizer, so the movie is the only thing on screen. It closes when the
:: clip ends. A second, third, or fourth MP Gui never shows it. The helper
:: exits 0 only when it actually played; the host then skips a second play.
"%PYTHON_BIN%" -c "import sys; sys.path.insert(0, r'%BASE_DIR%\scripts'); import exit_video; raise SystemExit(0 if exit_video.play_player_intro('MANAGER') else 1)" >nul 2>&1
if not errorlevel 1 set "MP_GUI_INTRO_DONE=1"

set "BACKEND=opengl"
set "CONFIG_FILE=%CONFIG_DIR%\config.env"
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
    echo [ERROR] MP Gui could not find the Liquid Glass host in "%BASE_DIR%".
    pause
    exit /b 1
)

set "MP_LIQUID_GLASS=1"
set "MP_LIQUID_GLASS_PLATFORM=win32"
set "PYTHONIOENCODING=utf-8"
set "PYTHONW_BIN=%PYTHON_BIN%"
if /I "%PYTHON_BIN:~-10%"=="python.exe" (
    set "PYTHONW_TRY=!PYTHON_BIN:python.exe=pythonw.exe!"
    if exist "!PYTHONW_TRY!" set "PYTHONW_BIN=!PYTHONW_TRY!"
)

if "!NO_VISUALIZER!"=="0" (
    set "VIS_PY=%BASE_DIR%\MP_Liquid_Visualizer.py"
    if not exist "!VIS_PY!" set "VIS_PY=%BASE_DIR%\scripts\mp_liquid_visualizer_vulkan.py"
    if exist "!VIS_PY!" (
        set "VIS_RUNNING=0"
        for /f "tokens=*" %%P in ('powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*MP_Liquid_Visualizer*' -or $_.CommandLine -like '*mp_liquid_visualizer_vulkan*' } | Select-Object -ExpandProperty ProcessId" 2^>nul') do (
            if not "%%P"=="" set "VIS_RUNNING=1"
        )
        if "!VIS_RUNNING!"=="0" (
            set "VIS_BACKEND=opengl"
            if /i "!BACKEND!"=="vulkan" set "VIS_BACKEND=vulkan"
            set "MP_VISUALIZER_COMPANION=1"
            set "MP_VISUALIZER_MATCH_GUI=1"
            start "" "%PYTHON_BIN%" "!VIS_PY!" --preset random --backend !VIS_BACKEND!
            set "MP_VISUALIZER_COMPANION="
            set "MP_VISUALIZER_MATCH_GUI="
        )
    )
)

start "" /MIN powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File "%BASE_DIR%\scripts\windows_tray.ps1" -Root "%BASE_DIR%"
start "" "%PYTHONW_BIN%" "%TARGET_PY%" --manager !EXTRA!
exit /b 0
