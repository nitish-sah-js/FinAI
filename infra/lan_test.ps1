# LAN reachability test (02 §7 step 6). Run on any laptop:
#   powershell -ExecutionPolicy Bypass -File infra\lan_test.ps1 -L1 192.168.43.101 -L2 192.168.43.102 -L3 192.168.43.103
#   powershell -ExecutionPolicy Bypass -File infra\lan_test.ps1 -FromEnv        # read the IPs from .env
# Uses a 1-second TCP connect per port (Test-NetConnection can hang ~20 s on filtered ports).
param([string]$L1, [string]$L2, [string]$L3, [switch]$FromEnv, [int]$TimeoutMs = 1000)

if ($FromEnv) {
    $envFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
    if (-not (Test-Path $envFile)) { Write-Host "no .env found at $envFile" -ForegroundColor Red; exit 1 }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^(L[123])_HOST=([^\s#]+)') { Set-Variable -Name $Matches[1] -Value $Matches[2] }
    }
}
if (-not ($L1 -and $L2 -and $L3)) { Write-Host "give -L1 -L2 -L3 IPs, or -FromEnv" -ForegroundColor Red; exit 1 }

# ports to probe per laptop (01 §1); 11434 = Ollama everywhere
$plan = [ordered]@{
    L1 = @{ ip = $L1; ports = [ordered]@{ "8000" = "orchestrator"; "11434" = "ollama" } }
    L2 = @{ ip = $L2; ports = [ordered]@{ "8101" = "quant"; "8102" = "sentiment"; "8103" = "agri"; "8104" = "vectordb"; "8080" = "weaviate"; "11434" = "ollama" } }
    L3 = @{ ip = $L3; ports = [ordered]@{ "8201" = "ingestion"; "8202" = "monitor"; "3000" = "terminal"; "11434" = "ollama" } }
}

function Test-Port([string]$ip, [int]$port, [int]$ms) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $t = $c.ConnectAsync($ip, $port); return ($t.Wait($ms) -and $c.Connected) } catch { return $false } finally { $c.Close() }
}

$fails = 0
foreach ($name in $plan.Keys) {
    $ip = $plan[$name].ip
    if ($ip -like "127.*" -or $ip -eq "localhost") {
        # some security suites block loopback ICMP; ping is meaningless for single-laptop mode anyway
        Write-Host ("{0} {1,-16} ping n/a (loopback)" -f $name, $ip) -ForegroundColor Gray
    } else {
        $ping = Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue
        Write-Host ("{0} {1,-16} ping {2}" -f $name, $ip, $(if ($ping) { "ok" } else { "FAIL" })) -ForegroundColor $(if ($ping) { "Green" } else { "Yellow" })
    }
    foreach ($port in $plan[$name].ports.Keys) {
        $ok = Test-Port $ip ([int]$port) $TimeoutMs
        if (-not $ok) { $fails++ }
        Write-Host ("    {0,-6} {1,-13} {2}" -f $port, $plan[$name].ports[$port], $(if ($ok) { "open" } else { "CLOSED" })) `
            -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
    }
}

if ($fails -gt 0) {
    Write-Host "`n$fails port(s) unreachable. Most likely causes, in order:" -ForegroundColor Yellow
    Write-Host "  1. The target laptop's network profile is Public  -> set it to Private (infra\firewall.ps1 -Check shows it)"
    Write-Host "  2. Firewall rule missing                          -> run infra\firewall.ps1 as Administrator on that laptop"
    Write-Host "  3. Service bound to 127.0.0.1 or not started      -> uvicorn ... --host 0.0.0.0 ; OLLAMA_HOST=0.0.0.0:11434"
    Write-Host "  (ping FAIL + all closed = wrong IP or client isolation on the Wi-Fi: use a phone hotspot or Tailscale)"
    exit 1
}
Write-Host "`nall ports reachable" -ForegroundColor Green
