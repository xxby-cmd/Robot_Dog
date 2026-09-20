@echo off
setlocal EnableExtensions
title VirtualBox VT-x Repair v2

set "LOG=%USERPROFILE%\Desktop\VirtualBox_VTx_Repair.log"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting administrator privileges...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList 'elevated'"
    if errorlevel 1 (
        echo.
        echo Failed to request administrator privileges.
        echo Right-click this file and choose Run as administrator.
        pause
    )
    exit /b
)

echo VirtualBox VT-x repair started. > "%LOG%"
echo Date: %DATE% %TIME% >> "%LOG%"
echo. >> "%LOG%"

echo [1/8] Disabling Windows hypervisor startup...
bcdedit /set hypervisorlaunchtype off >> "%LOG%" 2>&1

echo [2/8] Disabling Hyper-V...
dism.exe /Online /Disable-Feature /FeatureName:Microsoft-Hyper-V-All /NoRestart >> "%LOG%" 2>&1

echo [3/8] Disabling Virtual Machine Platform...
dism.exe /Online /Disable-Feature /FeatureName:VirtualMachinePlatform /NoRestart >> "%LOG%" 2>&1

echo [4/8] Disabling Windows Hypervisor Platform...
dism.exe /Online /Disable-Feature /FeatureName:HypervisorPlatform /NoRestart >> "%LOG%" 2>&1

echo [5/8] Disabling Windows Sandbox...
dism.exe /Online /Disable-Feature /FeatureName:Containers-DisposableClientVM /NoRestart >> "%LOG%" 2>&1

echo [6/8] Disabling VBS policy...
reg.exe add "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard" /v EnableVirtualizationBasedSecurity /t REG_DWORD /d 0 /f >> "%LOG%" 2>&1

echo [7/8] Disabling Memory Integrity policy...
reg.exe add "HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity" /v Enabled /t REG_DWORD /d 0 /f >> "%LOG%" 2>&1

echo [8/8] Disabling Credential Guard policy...
reg.exe add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v LsaCfgFlags /t REG_DWORD /d 0 /f >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo Repair commands completed. >> "%LOG%"

echo.
echo ============================================================
echo Repair commands completed.
echo Log saved to: %LOG%
echo No VirtualBox VM or disk file was changed.
echo Windows must restart before testing Robot_Dog.
echo ============================================================
echo.
choice /C YN /N /M "Restart Windows now? [Y/N]: "
if errorlevel 2 goto finish
shutdown.exe /r /t 10 /c "VirtualBox VT-x repair completed."

:finish
echo.
echo Press any key to close this window.
pause >nul
endlocal
