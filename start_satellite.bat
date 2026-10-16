@echo off
cd /d "%~dp0"
echo Starting the satellite placefile server (600 x 600 mile box).
echo Leave this window open while you use GR.
echo.
py --version >nul 2>nul
if %errorlevel%==0 (
  py sat_placefile.py --half-deg 4.35
  goto end
)
python --version >nul 2>nul
if %errorlevel%==0 (
  python sat_placefile.py --half-deg 4.35
  goto end
)
echo Python was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo Then double-click this file again.
:end
echo.
pause
