# Copy infra/env/<Laptop>.env to the repo-root .env (backs up an existing .env first).
#   powershell -ExecutionPolicy Bypass -File infra/use_env.ps1 -Laptop L2
param([Parameter(Mandatory = $true)][ValidateSet("L1", "L2", "L3", "single")][string]$Laptop)

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $PSScriptRoot "env\$Laptop.env"
$dst = Join-Path $root ".env"
if (-not (Test-Path $src)) { Write-Host "missing $src (run: python infra/make_env.py)" -ForegroundColor Red; exit 1 }
if (Test-Path $dst) {
    $bak = "$dst.bak"
    Copy-Item $dst $bak -Force
    Write-Host "backed up existing .env -> .env.bak"
}
Copy-Item $src $dst -Force
Write-Host "active: $Laptop.env -> .env" -ForegroundColor Green
Get-Content $dst | Select-String -Pattern '^(THIS_LAPTOP|L[123]_HOST|MOCK|LLM_MODE)=' | ForEach-Object { "  $($_.Line)" }
