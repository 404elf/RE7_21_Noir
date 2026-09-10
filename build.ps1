param([string]$OutputRoot = 'dist/v1.5.1')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$packageRoot = Join-Path $OutputRoot 'RE7_21_Noir'
if (Test-Path -LiteralPath $packageRoot) { throw 'A build already exists. Use -OutputRoot with a new directory to preserve it.' }
$env:PYTHONUSERBASE = Join-Path $PSScriptRoot '.venv/build-userbase'
$buildWork = Join-Path 'build' ([DateTime]::Now.ToString('yyyyMMddHHmmssfff'))
& .venv/Scripts/python.exe -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .venv/Scripts/python.exe -m PyInstaller --windowed --onedir --distpath $OutputRoot --workpath $buildWork --specpath $buildWork --name RE7_21_Noir (Join-Path $PSScriptRoot 'main.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
# The player root contains only folders, the executable and a short guide.
foreach ($folder in @('custom/game','custom/timer','custom/audio','custom/network','custom/presets','docs','userdata')) {
    New-Item -ItemType Directory -Path (Join-Path $packageRoot $folder) | Out-Null
}
$files = @{
    'config.json'='custom/game/config.json'; 'timer.json'='custom/timer/timer.json';
    'audio.json'='custom/audio/audio.json'; 'network.json'='custom/network/network.json';
    'updates.json'='custom/network/updates.json'; 'room-server.json'='custom/network/room-server.json';
    'version.json'='_internal/version.json'; 'docs/开始游戏.txt'='开始游戏.txt';
    'docs/AI-CONFIG-GUIDE.md'='docs/AI-CONFIG-GUIDE.md'; 'NETWORKING.md'='docs/NETWORKING.md'
}
foreach ($source in $files.Keys) {
    Copy-Item -LiteralPath $source -Destination (Join-Path $packageRoot $files[$source])
}
Copy-Item -LiteralPath sounds -Destination (Join-Path $packageRoot 'custom/audio/sounds') -Recurse
Get-ChildItem -LiteralPath presets -Filter '*.json' | Copy-Item -Destination (Join-Path $packageRoot 'custom/presets')
$serverScript = @('@echo off', 'cd /d "%~dp0..\.."', '"RE7_21_Noir.exe" --room-server "custom\network\room-server.json"', 'pause')
$serverScript | Set-Content -LiteralPath (Join-Path $packageRoot 'custom/network/start-room-server.cmd') -Encoding ascii
