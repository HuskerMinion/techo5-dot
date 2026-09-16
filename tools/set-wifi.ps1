<#
.SYNOPSIS
  Change the Wi-Fi network a Dot running TECHO5 Linux joins, over its USB serial console.

.DESCRIPTION
  The console needs no network, so this works exactly when it is needed: a new router, a changed
  password, a unit moved to another house. The passphrase is asked for without echoing and turned into
  WPA's key on the PC (tools/wifi-key.ps1); the unit receives only the name and key as hex, runs
  wifi-set, and rejoins at once. -Forget goes back to the network Fire OS saved.

.EXAMPLE
  .\tools\set-wifi.ps1 -Serial <serial> -Ssid "MyNetwork"
  .\tools\set-wifi.ps1 -Serial <serial> -Forget
#>
param(
    [Parameter(Mandatory)][string]$Serial,
    [string]$Ssid,
    [switch]$Forget
)
$ErrorActionPreference = 'Stop'
if (-not $Forget -and -not $Ssid) { throw "give -Ssid, or -Forget" }

$dev = Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -like "USB\VID_18D1&PID_4EE7\$Serial" -and $_.Name -match '\((COM\d+)\)' } | Select-Object -First 1
if (-not $dev) { throw "no TECHO5 Linux console for $Serial on USB (is it plugged in and booted into Linux?)" }
$port = [regex]::Match($dev.Name, 'COM\d+').Value

if ($Forget) {
    $cmd = 'wifi-set --forget'
} else {
    $conf = & (Join-Path $PSScriptRoot 'wifi-key.ps1') -Ssid $Ssid
    $hex = @{}
    foreach ($line in ($conf -split "`n")) { if ($line -match '^(ssid|psk)=([0-9a-f]+)$') { $hex[$Matches[1]] = $Matches[2] } }
    $cmd = "wifi-set --hex $($hex.ssid) $($hex.psk)"
}

$sp = New-Object IO.Ports.SerialPort $port, 115200
$sp.Open()
try {
    $sp.Write("`n"); Start-Sleep -Milliseconds 300; $sp.DiscardInBuffer()
    # Joining takes up to a minute and a quarter (association, then a lease); the marker ends the wait.
    $sp.Write("$cmd; printf 'T5%s\n' END`n")
    $out = ''
    $until = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $until -and $out -notmatch 'T5END') { Start-Sleep -Milliseconds 200; $out += $sp.ReadExisting() }
} finally { $sp.Close() }

$lines = $out -split "\r?\n" | Where-Object { $_ -match '^(wifi-set|techo5-net):|^\d+\.\d+\.\d+\.\d+$' }
$lines | ForEach-Object { Write-Host "   $_" }
$ip = $lines | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -Last 1
if ($ip) { Write-Host "Joined: $Serial is at $ip" } else { Write-Host "The unit did not report an address; the console ($port) shows why."; exit 1 }
