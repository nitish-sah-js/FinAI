# Copilot hackathon: open the inbound ports on the PRIVATE profile (02 §3). Run in an ADMIN PowerShell on every laptop:
#   powershell -ExecutionPolicy Bypass -File infra\firewall.ps1           # add / refresh the rules
#   powershell -ExecutionPolicy Bypass -File infra\firewall.ps1 -Remove   # delete them after the hackathon
#   powershell -ExecutionPolicy Bypass -File infra\firewall.ps1 -Check    # read-only: show profile + rules (no admin needed)
param([switch]$Remove, [switch]$Check)

$ports = "8000,8101-8104,8201-8202,3000,8080,50051,11434"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

function Show-Profile {
    $profiles = Get-NetConnectionProfile
    foreach ($p in $profiles) {
        $color = if ($p.NetworkCategory -eq "Public") { "Yellow" } else { "Green" }
        Write-Host ("network '{0}' on {1}: {2}" -f $p.Name, $p.InterfaceAlias, $p.NetworkCategory) -ForegroundColor $color
    }
    if ($profiles | Where-Object { $_.NetworkCategory -eq "Public" }) {
        Write-Host "  A Public profile blocks inbound LAN connections even with these rules. Change it to Private:" -ForegroundColor Yellow
        Write-Host "    Settings > Network & internet > Wi-Fi (or Ethernet) > <network> > Network profile type: Private"
        Write-Host "    or (Admin): Set-NetConnectionProfile -InterfaceAlias '<alias>' -NetworkCategory Private"
        Write-Host "  (Domain-managed lab networks may not allow this; use a phone hotspot or Tailscale instead.)"
    }
    return $profiles
}

function Show-Rules {
    $rules = Get-NetFirewallRule -DisplayName "Copilot-*" -ErrorAction SilentlyContinue
    if (-not $rules) { Write-Host "no Copilot-* firewall rules installed" -ForegroundColor Yellow; return }
    $rules | ForEach-Object {
        $pf = $_ | Get-NetFirewallPortFilter
        [pscustomobject]@{ DisplayName = $_.DisplayName; Enabled = $_.Enabled; Profile = $_.Profile; Ports = ($pf.LocalPort -join ",") }
    } | Format-Table -AutoSize | Out-String | Write-Host
}

if ($Check) { Show-Profile | Out-Null; Show-Rules; exit 0 }

if (-not $isAdmin) {
    Write-Host "This script changes Windows Firewall rules and must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click PowerShell > 'Run as administrator', then run it again. (Use -Check for a read-only view.)"
    exit 1
}

if ($Remove) {
    Remove-NetFirewallRule -DisplayName "Copilot-*" -ErrorAction SilentlyContinue
    Write-Host "removed Copilot-* rules" -ForegroundColor Green
    exit 0
}

Show-Profile | Out-Null
# idempotent: drop old copies first so re-running never duplicates rules
Remove-NetFirewallRule -DisplayName "Copilot-*" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "Copilot-Hackathon" -Direction Inbound `
    -Protocol TCP -LocalPort ($ports -split ",") -Action Allow -Profile Private | Out-Null
# allow ping for diagnostics
New-NetFirewallRule -DisplayName "Copilot-ICMP" -Protocol ICMPv4 -IcmpType 8 `
    -Direction Inbound -Action Allow -Profile Private | Out-Null
Write-Host "inbound TCP $ports + ping allowed on the Private profile" -ForegroundColor Green
Show-Rules
