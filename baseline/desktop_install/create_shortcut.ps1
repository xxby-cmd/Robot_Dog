param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Launcher,
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$Name
)
$ErrorActionPreference = 'Stop'
$desktopFolder = [Environment]::GetFolderPath('Desktop')
if ([IO.Path]::GetFileName($Name) -ne $Name) { throw 'Shortcut name must be a file name.' }
$shortcutFile = Join-Path $desktopFolder $Name
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutFile)
$pythonwPath = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) { $pythonwPath = $Python }
$expectedArguments = '"' + $Launcher + '" --config "' + $Config + '"'
if ((Test-Path -LiteralPath $shortcutFile) -and ($shortcut.Arguments -ne $expectedArguments)) {
    throw 'Existing shortcut points elsewhere; it was preserved.'
}
$shortcut.TargetPath = $pythonwPath
$shortcut.Arguments = $expectedArguments
$shortcut.WorkingDirectory = Split-Path -Parent $Launcher
$shortcut.Save()
