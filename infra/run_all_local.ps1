# Start every backend service + the Next.js terminal on this laptop (docs/15 R1, single-laptop mode).
# Usage (repo root):  powershell -ExecutionPolicy Bypass -File infra\run_all_local.ps1 [-NoFrontend] [-Browser] [-Stop]
# Default opens the Electron desktop app (terminal window + pet); -Browser only serves http://127.0.0.1:3000/terminal
# Logs: data\logs\<service>.log   Stop everything: -Stop
param([switch]$NoFrontend, [switch]$Browser, [switch]$Stop)

$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Logs = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$PidFile = Join-Path $Logs "pids.txt"

if ($Stop) {
    if (Test-Path $PidFile) {
        Get-Content $PidFile | ForEach-Object { try { Stop-Process -Id ([int]$_) -Force -ErrorAction Stop } catch {} }
        Remove-Item $PidFile
    }
    Get-Process electron -ErrorAction SilentlyContinue | Stop-Process -Force
    # uvicorn/node children
    foreach ($port in 8000, 8101, 8102, 8103, 8104, 8201, 8202, 3000) {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction Stop } catch {} }
    }
    Write-Host "stopped"
    exit 0
}

# name, working dir (relative to repo root), uvicorn app, port
$Services = @(
    @("quant",        "services\quant",     "quant.main:app",          8101),
    @("sentiment",    "services\sentiment", "sentiment.main:app",      8102),
    @("agri",         "services\agri",      "agri.main:app",           8103),
    @("vectordb",     "services\vectordb",  "vectordb.main:app",       8104),
    @("ingestion",    ".",                  "services.ingestion.app:app", 8201),
    @("monitor",      "services\monitor",   "monitor.main:app",        8202),
    @("orchestrator", "services",           "orchestrator.app:app",    8000)
)

$env:PYTHONIOENCODING = "utf-8"

# Load this machine's Ollama models once and pin them (keep_alive=-1) so the first question is not a cold start.
Write-Host "warming up Ollama models on this machine (first load can take ~40 s)..."
& $Py (Join-Path $Root "infra\warmup.py") --local
if ($LASTEXITCODE -ne 0) { Write-Host "warning: no model loaded; questions will use keyword rules until Ollama is up" }
$env:COPILOT_ROOT = $Root
foreach ($s in $Services) {
    $name, $dir, $app, $port = $s
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host ("{0,-13} :{1} already listening, skipped" -f $name, $port)
        continue
    }
    $log = Join-Path $Logs "$name.log"
    $p = Start-Process -FilePath $Py -WorkingDirectory (Join-Path $Root $dir) -WindowStyle Hidden -PassThru `
        -ArgumentList "-m", "uvicorn", $app, "--host", "0.0.0.0", "--port", $port `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    Add-Content $PidFile $p.Id
    Write-Host ("{0,-13} :{1} pid {2}  log {3}" -f $name, $port, $p.Id, $log)
}

if (-not $NoFrontend) {
    $term = Join-Path $Root "apps\terminal"
    if (-not (Test-Path (Join-Path $term "node_modules"))) { Push-Location $term; npm install; Pop-Location }
    if (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "next.js       :3000 already listening, skipped"
    } else {
        $p = Start-Process -FilePath "cmd.exe" -WorkingDirectory $term -WindowStyle Hidden -PassThru `
            -ArgumentList "/c", "npm run next:dev" -RedirectStandardOutput (Join-Path $Logs "terminal.log") -RedirectStandardError (Join-Path $Logs "terminal.log.err")
        Add-Content $PidFile $p.Id
    }
    if (-not $Browser) {
        # Electron waits for Next.js on :3000, then opens the terminal window + desktop pet (main.js)
        $p = Start-Process -FilePath "cmd.exe" -WorkingDirectory $term -WindowStyle Hidden -PassThru `
            -ArgumentList "/c", "npm run electron:wait" -RedirectStandardOutput (Join-Path $Logs "electron.log") -RedirectStandardError (Join-Path $Logs "electron.log.err")
        Add-Content $PidFile $p.Id
        Write-Host "electron      desktop app (terminal window + pet) opens once :3000 is ready"
    }
    Write-Host "terminal      :3000  -> http://127.0.0.1:3000/terminal"
}
Write-Host "health: .venv\Scripts\python infra\check_health.py   stop: infra\run_all_local.ps1 -Stop"
