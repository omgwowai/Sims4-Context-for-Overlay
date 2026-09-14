@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set "install_result=%errorlevel%"
echo.
pause
exit /b %install_result%
