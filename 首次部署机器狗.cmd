@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0baseline\desktop_install\bootstrap.py" install --start --wait %*
) else (
  python "%~dp0baseline\desktop_install\bootstrap.py" install --start --wait %*
)
if errorlevel 1 pause
