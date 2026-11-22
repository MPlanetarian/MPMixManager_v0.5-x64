@echo off
rem MP Liquid Mixer 0.0.1 - Windows launch from the mixer directory.
call "%~dp0..\launch_liquid_mixer.bat" %*
exit /b %ERRORLEVEL%
