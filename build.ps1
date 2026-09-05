$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& .venv/Scripts/python.exe -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .venv/Scripts/python.exe -m PyInstaller --noconfirm --windowed --onedir --name RE7_21_Noir main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$destination = Join-Path $PSScriptRoot 'dist/RE7_21_Noir/config.json'
if (Test-Path -LiteralPath $destination) {
    $backup = "$destination.$([DateTime]::Now.ToString('yyyyMMddHHmmssfff')).bak"
    Copy-Item -LiteralPath $destination -Destination $backup
    if ((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $backup).Hash) { throw 'Backup verification failed' }
}
Copy-Item -LiteralPath config.json -Destination $destination
Copy-Item -LiteralPath README.md -Destination dist/RE7_21_Noir/README.md
