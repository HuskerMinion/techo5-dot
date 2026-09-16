<#
.SYNOPSIS
  Turn a Wi-Fi name and passphrase into the lines the Dot's wifi.conf holds (the name as hex, WPA's
  256-bit key as hex), asking for the passphrase without echoing it.

.DESCRIPTION
  The key is PBKDF2-HMAC-SHA1 over the passphrase with the network name as salt, 4096 rounds, 32 bytes:
  exactly what wpa_passphrase makes. Only the key leaves the PC, never the passphrase, and hex means no
  character in either can break the configuration file on the unit. Used by install-dot.ps1 and
  set-wifi.ps1.
#>
param(
    [Parameter(Mandatory)][string]$Ssid,
    # For tests only; otherwise asked for.
    [Security.SecureString]$Passphrase
)
$ErrorActionPreference = 'Stop'
$ssidBytes = [Text.Encoding]::UTF8.GetBytes($Ssid)
if ($ssidBytes.Length -lt 1 -or $ssidBytes.Length -gt 32) { throw "a Wi-Fi network name is 1 to 32 bytes" }
if (-not $Passphrase) { $Passphrase = Read-Host "   Passphrase for '$Ssid'" -AsSecureString }
$pass = [Net.NetworkCredential]::new('', $Passphrase).Password
if ($pass.Length -lt 8 -or $pass.Length -gt 63) { throw "a WPA passphrase is 8 to 63 characters" }
$kdf = [Security.Cryptography.Rfc2898DeriveBytes]::new([Text.Encoding]::UTF8.GetBytes($pass), $ssidBytes, 4096,
    [Security.Cryptography.HashAlgorithmName]::SHA1)
$psk = -join ($kdf.GetBytes(32) | ForEach-Object { $_.ToString('x2') })
$kdf.Dispose()
$pass = $null
"ssid=$(-join ($ssidBytes | ForEach-Object { $_.ToString('x2') }))`npsk=$psk`n"
