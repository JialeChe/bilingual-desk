$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install requirements.txt and requirements-build.txt first.'
}
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'build\pyinstaller-cache'
& $pythonPath -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller 'packaging\翻译对照.spec'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'packaging\使用说明.txt') -Destination (Join-Path $PSScriptRoot 'dist\使用说明.txt') -Force
$archiveItems = @(
    (Join-Path $PSScriptRoot 'dist\翻译对照.exe'),
    (Join-Path $PSScriptRoot 'dist\使用说明.txt')
)
Compress-Archive -LiteralPath $archiveItems -DestinationPath (Join-Path $PSScriptRoot 'dist\翻译对照-Windows-x64.zip') -Force
Get-Item -LiteralPath $archiveItems[0], (Join-Path $PSScriptRoot 'dist\翻译对照-Windows-x64.zip') |
    Select-Object Name, Length, FullName
