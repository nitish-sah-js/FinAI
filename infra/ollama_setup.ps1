# Per-laptop Ollama setup (02 §4): persistent env vars, restart, model pulls, tokens/sec benchmark.
#   powershell -ExecutionPolicy Bypass -File infra\ollama_setup.ps1 -Laptop L1
#   ... -Laptop L2 -Backup        # also pull qwen3:4b-instruct so this laptop can take over any role
#   ... -Laptop L3 -SkipPull      # only env vars + restart + benchmark
#   ... -Laptop L1 -DryRun        # print what would happen, change nothing
param(
    [Parameter(Mandatory = $true)][ValidateSet("L1", "L2", "L3")][string]$Laptop,
    [switch]$Backup, [switch]$SkipPull, [switch]$SkipBench, [switch]$DryRun
)
$ErrorActionPreference = "Stop"

$vars = [ordered]@{
    OLLAMA_HOST              = "0.0.0.0:11434"   # listen on the LAN, not only 127.0.0.1
    OLLAMA_KEEP_ALIVE        = "30m"             # keep models loaded (no cold start in the demo)
    OLLAMA_CONTEXT_LENGTH    = "8192"            # the OpenAI endpoint can't set num_ctx per request
    OLLAMA_NUM_PARALLEL      = "2"               # 2 concurrent requests per loaded model
    OLLAMA_MAX_LOADED_MODELS = "2"               # L1/L3 hold 2 models
    OLLAMA_FLASH_ATTENTION   = "1"               # less VRAM for the KV cache
    OLLAMA_KV_CACHE_TYPE     = "q8_0"            # halves KV-cache memory, small quality cost
}
# NOTE: use qwen3:4b-instruct, not qwen3:4b (that tag is the thinking-only build and cannot turn thinking off)
$models = @{ L1 = @("qwen3:4b-instruct"); L2 = @("gemma3:4b"); L3 = @("qwen3:1.7b", "phi4-mini") }[$Laptop]
if ($Backup -and $models -notcontains "qwen3:4b-instruct") { $models += "qwen3:4b-instruct" }

$dir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$exe = Join-Path $dir "ollama.exe"
$app = Join-Path $dir "ollama app.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Ollama is not installed. Install it with:  winget install --id Ollama.Ollama -e" -ForegroundColor Red
    exit 1
}

Write-Host "== $Laptop : environment variables (user scope) ==" -ForegroundColor Cyan
foreach ($k in $vars.Keys) {
    Write-Host ("  {0,-26} = {1}" -f $k, $vars[$k])
    if (-not $DryRun) { [Environment]::SetEnvironmentVariable($k, $vars[$k], "User") }
    Set-Item -Path "Env:$k" -Value $vars[$k]          # this process too, so the restarted server inherits them
}

function Test-Api { try { Invoke-RestMethod "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null; $true } catch { $false } }

Write-Host "== restart Ollama so it picks up the variables ==" -ForegroundColor Cyan
if ($DryRun) { Write-Host "  (dry run) would stop ollama*, start the tray app, fall back to 'ollama serve'" }
else {
    Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    if (Test-Path $app) { Start-Process -FilePath $app -WindowStyle Hidden }
    $up = $false
    for ($i = 0; $i -lt 20 -and -not $up; $i++) { Start-Sleep -Milliseconds 750; $up = Test-Api }
    if (-not $up) {
        # the tray app can fail to spawn the server from some shells; start the server directly (hidden, detached)
        Write-Host "  tray app did not start the server; starting 'ollama serve' directly" -ForegroundColor Yellow
        # Start-Process (ShellExecute) does NOT inherit this script's stdout handle, so a caller that pipes our output
        # is not left waiting forever for the long-running server to close it. The env vars were set on this process above.
        $log = Join-Path $env:LOCALAPPDATA "Ollama\serve-manual.log"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"`"$exe`" serve > `"$log`" 2>&1`"" -WindowStyle Hidden
        for ($i = 0; $i -lt 30 -and -not $up; $i++) { Start-Sleep -Milliseconds 750; $up = Test-Api }
    }
    if (-not $up) { Write-Host "  Ollama server did not come up. See %LOCALAPPDATA%\Ollama\server.log" -ForegroundColor Red; exit 1 }
    Write-Host "  server up: $((Invoke-RestMethod http://127.0.0.1:11434/api/version).version)" -ForegroundColor Green
}

if (-not $SkipPull) {
    Write-Host "== pull models for $Laptop : $($models -join ', ') ==" -ForegroundColor Cyan
    foreach ($m in $models) {
        if ($DryRun) { Write-Host "  (dry run) ollama pull $m"; continue }
        & $exe pull $m
        if ($LASTEXITCODE -ne 0) { Write-Host "  pull failed: $m" -ForegroundColor Red; exit 1 }
    }
}
if (-not $DryRun) { & $exe list }

if (-not $SkipBench -and -not $DryRun) {
    Write-Host "== tokens/sec (record these in infra/README.md) ==" -ForegroundColor Cyan
    foreach ($m in $models) {
        $body = @{ model = $m; prompt = "Explain in three sentences why a cyclone can hurt Indian port stocks.";
                   stream = $false; options = @{ num_predict = 128 } } | ConvertTo-Json
        try {
            Invoke-RestMethod http://127.0.0.1:11434/api/generate -Method Post -Body $body -ContentType "application/json" -TimeoutSec 300 | Out-Null  # warm-up / load
            $r = Invoke-RestMethod http://127.0.0.1:11434/api/generate -Method Post -Body $body -ContentType "application/json" -TimeoutSec 300
            $tps = [math]::Round($r.eval_count / ($r.eval_duration / 1e9), 1)
            $ptps = if ($r.prompt_eval_duration -gt 0) { [math]::Round($r.prompt_eval_count / ($r.prompt_eval_duration / 1e9), 1) } else { "n/a" }
            Write-Host ("  {0,-20} generate {1,6} tok/s   prompt {2,6} tok/s" -f $m, $tps, $ptps) -ForegroundColor Green
        } catch { Write-Host "  $m benchmark failed: $_" -ForegroundColor Red }
    }
    try {
        (Invoke-RestMethod http://127.0.0.1:11434/api/ps).models | ForEach-Object {
            Write-Host ("  loaded {0,-20} vram {1:N1} GB / total {2:N1} GB, ctx {3}" -f $_.name, ($_.size_vram / 1GB), ($_.size / 1GB), $_.context_length)
        }
    } catch {}
}
Write-Host "done. From another laptop:  curl http://<this-ip>:11434/api/tags" -ForegroundColor Cyan
