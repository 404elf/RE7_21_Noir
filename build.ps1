param([string]$OutputRoot = 'dist/v1.4.2')
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
$destination = Join-Path $packageRoot 'config.json'
if (Test-Path -LiteralPath $destination) {
    $backup = "$destination.$([DateTime]::Now.ToString('yyyyMMddHHmmssfff')).bak"
    Copy-Item -LiteralPath $destination -Destination $backup
    if ((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $backup).Hash) { throw 'Backup verification failed' }
}
Copy-Item -LiteralPath config.json -Destination $destination
Copy-Item -LiteralPath README.md -Destination (Join-Path $packageRoot 'README.md')
Copy-Item -LiteralPath audio.json -Destination (Join-Path $packageRoot 'audio.json')
Copy-Item -LiteralPath sounds -Destination (Join-Path $packageRoot 'sounds') -Recurse
Copy-Item -LiteralPath presets -Destination (Join-Path $packageRoot 'presets') -Recurse
foreach ($name in @('timer.json', 'updates.json', 'version.json')) {
    Copy-Item -LiteralPath $name -Destination (Join-Path $packageRoot $name)
}
foreach ($name in @('network.json','room-server.json','start-room-server.cmd','NETWORKING.md','SECURITY.md')) {
    Copy-Item -LiteralPath $name -Destination (Join-Path $packageRoot $name)
}
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'docs') | Out-Null
foreach ($name in @('AI-v1.4.1.md','ai-v141-calibrated-benchmark.jsonl','AI-v1.4.2.md','ai-v142-release-benchmark.jsonl')) {
    Copy-Item -LiteralPath (Join-Path 'docs' $name) -Destination (Join-Path $packageRoot "docs/$name")
}
