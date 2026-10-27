@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0baseline\go2_jazzy\deploy_sensors.py" %*
) else (
  python "%~dp0baseline\go2_jazzy\deploy_sensors.py" %*
)
pause
