@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3 "%~dp0baseline\desktop_install\launcher.py" %*
) else (
  pythonw "%~dp0baseline\desktop_install\launcher.py" %*
)
exit /b
