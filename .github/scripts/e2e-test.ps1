# End-to-end lifecycle test for the baobao-apps bucket (runs on a clean Windows machine).
# - installs Scoop into a fresh root
# - hosts this repo checkout as a local bucket (directory drop; no git required)
# - installs / force-updates / uninstalls every package while verifying
#   data junctions, persist reuse, registry handling and the OpenBrowser patch.
# ASCII-only file (PowerShell 5.1-safe). Exit code 0 = all checks passed.
[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../')).Path,
    [string]$ScoopDir = 'C:\scoop-e2e'
)

$ErrorActionPreference = 'Stop'
$script:failures = 0
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Step([string]$msg) { Write-Host "`n== $msg" }
function Assert([bool]$cond, [string]$msg) {
    if ($cond) { Write-Host "  ok: $msg" }
    else { Write-Host "  FAIL: $msg"; $script:failures++ }
}
function Assert-Eq($actual, $expected, [string]$msg) {
    $a = if ($null -eq $actual) { '<null>' } else { "$actual" }
    $e = if ($null -eq $expected) { '<null>' } else { "$expected" }
    Assert ($a -eq $e) "$msg (expected='$e' actual='$a')"
}
function Norm([string]$p) {
    if ([string]::IsNullOrEmpty($p)) { return '' }
    return ($p -replace '^\\\\\?\\', '').TrimEnd('\').ToLowerInvariant()
}
function Get-ReparseTarget([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $it = Get-Item -LiteralPath $path -Force
    if (($null -ne $it.LinkType) -or (($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        $t = @($it.Target)[0]
        if ($t) { return $t }
    }
    return $null
}
function Test-Junction([string]$link, [string]$wantTarget, [string]$label) {
    $t = Get-ReparseTarget $link
    if ($null -eq $t) {
        Assert $false "$label : $link is not a junction"
        return
    }
    Assert-Eq (Norm $t) (Norm $wantTarget) "$label : junction target"
}
function Run-Scoop([string[]]$scoopArgs) {
    $cmd = Join-Path $ScoopDir 'shims\scoop.cmd'
    if (-not (Test-Path -LiteralPath $cmd)) { throw "scoop shim not found at $cmd" }
    Write-Host ("  > scoop " + ($scoopArgs -join ' '))
    # Capture the FULL child output: with $ErrorActionPreference=Stop a native
    # stderr record would otherwise abort the pipeline and swallow stdout.
    $logFile = Join-Path $env:TEMP ('scoop-e2e-{0}.log' -f ([guid]::NewGuid().ToString('N').Substring(0, 8)))
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $cmd @scoopArgs 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($out.Trim()) { [System.IO.File]::WriteAllText($logFile, [string]$out, $utf8) }
    if ($out.Trim()) { Write-Host $out.Trim() }
    if ($code -ne 0) {
        throw ("scoop {0} failed with exit code {1} (full output above; also kept at {2})" -f ($scoopArgs -join ' '), $code, $logFile)
    }
    return $out
}
function Get-RegDenyInfo([string]$SubKey) {
    # read the ACL through a handle that only needs WRITE_DAC + READ_CONTROL,
    # which works even while the key carries deny rules (plain Get-Acl does not)
    $key = $null
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    } catch {}
    if (-not $key) { return $null }
    $n = 0
    foreach ($r in @($key.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
        if ($r.AccessControlType -eq 'Deny') { $n++ }
    }
    $key.Close()
    return @{ DenyCount = $n }
}
function Read-HexJson([string]$path) {
    $hex = [System.IO.File]::ReadAllText($path, $utf8).Trim()
    $bytes = New-Object byte[] ($hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16)
    }
    return ([System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
}

# ---------------------------------------------------------------- 0. prep
$env:SCOOP = $ScoopDir
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

Step "Installing Scoop into $ScoopDir"
if (-not (Test-Path (Join-Path $ScoopDir 'shims\scoop.cmd'))) {
    Invoke-Expression "& {$(Invoke-RestMethod -Uri 'https://get.scoop.sh')} -RunAsAdmin"
} else {
    Write-Host '  (scoop already present, reusing)'
}
if (-not (Test-Path (Join-Path $ScoopDir 'shims\scoop.cmd'))) { throw 'Scoop bootstrap failed' }

Step 'Hosting the bucket from this repo checkout'
$bucketDir = Join-Path $ScoopDir 'buckets\baobao-test'
if (Test-Path $bucketDir) { Remove-Item $bucketDir -Recurse -Force }
New-Item -Path $bucketDir -ItemType Directory -Force | Out-Null
Copy-Item -Path (Join-Path $RepoRoot 'bucket') -Destination $bucketDir -Recurse -Force
Assert (Test-Path (Join-Path $bucketDir 'bucket\typora0.11.18.json')) 'bucket files copied into scoop buckets dir'

# ---------------------------------------------------------------- 1. typora
Step 'typora0.11.18: fresh install'
Run-Scoop @('install', 'baobao-test/typora0.11.18') | Out-Null
$typoraLink = Join-Path $env:APPDATA 'Typora'
$typoraTarget = Join-Path $ScoopDir 'persist\typora0.11.18\TyporaData'
Test-Junction $typoraLink $typoraTarget 'typora'
Assert (Test-Path (Join-Path $typoraLink 'profile.data')) 'profile.data seeded'
$pd = $null
try { $pd = Read-HexJson (Join-Path $typoraLink 'profile.data') } catch {}
Assert ($null -ne $pd) 'profile.data is valid hex-encoded JSON'
if ($pd) {
    Assert ($pd.enableAutoUpdate -eq $false) 'profile.data: enableAutoUpdate=false'
    Assert ($null -ne $pd.skipUpdate) 'profile.data: skipUpdate set'
}
$reg = Get-RegDenyInfo 'SOFTWARE\Typora'
Assert ($null -ne $reg) 'registry key exists (ACL readable through a ChangePermissions handle)'
if ($reg) {
    Assert ($reg.DenyCount -ge 4) "registry deny rules present (count=$($reg.DenyCount))"
}
$readDenied = $false
try { $null = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('SOFTWARE\Typora') } catch { $readDenied = $true }
Assert $readDenied 'plain read of the registry key is denied (popup fix active)'

Step 'typora0.11.18: force update keeps data'
Set-Content -Path (Join-Path $typoraLink 'marker.txt') -Value 'keep-me' -Encoding Ascii
Run-Scoop @('update', '-f', 'typora0.11.18') | Out-Null
Test-Junction $typoraLink $typoraTarget 'typora after force-update'
Assert (Test-Path (Join-Path $typoraLink 'marker.txt')) 'user data survived force update'
$reg2 = Get-RegDenyInfo 'SOFTWARE\Typora'
Assert ($null -ne $reg2) 'registry handling re-applied after force update'
if ($reg2) {
    Assert ($reg2.DenyCount -ge 4) "registry deny rules re-applied (count=$($reg2.DenyCount))"
}

Step 'typora0.11.18: normal uninstall keeps persist data, removes junction + registry key'
Run-Scoop @('uninstall', 'typora0.11.18') | Out-Null
Assert (-not (Test-Path -LiteralPath $typoraLink)) 'junction removed on uninstall'
Assert (Test-Path (Join-Path $typoraTarget 'marker.txt')) 'persist data kept on normal uninstall'
Assert ($null -eq (Get-RegDenyInfo 'SOFTWARE\Typora')) 'registry key removed on uninstall (was created by the package)'
Assert (-not (Test-Path (Join-Path $ScoopDir 'persist\typora0.11.18\.registry-state.json'))) 'registry state file cleared after a verified revert'

Step 'typora0.11.18: reinstall reuses persist data, then -p purges'
Run-Scoop @('install', 'baobao-test/typora0.11.18') | Out-Null
Assert (Test-Path (Join-Path $typoraLink 'marker.txt')) 'persist data reused on reinstall'
Run-Scoop @('uninstall', '-p', 'typora0.11.18') | Out-Null
Assert (-not (Test-Path -LiteralPath $typoraLink)) 'junction removed with -p'
Assert (-not (Test-Path (Join-Path $ScoopDir 'persist\typora0.11.18'))) 'persist data purged with -p'

# ---------------------------------------------------------------- 2. OpenBrowser
Step 'OpenBrowser: fresh install'
Run-Scoop @('install', 'baobao-test/OpenBrowser') | Out-Null
$obLink = Join-Path $env:APPDATA 'openbrowser'
$obTarget = Join-Path $ScoopDir 'persist\OpenBrowser\UserData'
Test-Junction $obLink $obTarget 'OpenBrowser'
$mainJs = Join-Path $ScoopDir 'apps\OpenBrowser\current\runtime\resources\app\main.js'
Assert (Test-Path $mainJs) 'main.js present'
if (Test-Path $mainJs) {
    $txt = [System.IO.File]::ReadAllText($mainJs, $utf8)
    Assert ($txt.Contains('normFileUrl')) 'IPC junction patch applied'
    Assert ($txt.Contains('const expected = normFileUrl(trustedAppIndexUrl());')) 'both-sides normalization applied'
}

Step 'OpenBrowser: force update keeps data and re-applies the patch'
Set-Content -Path (Join-Path $obLink 'marker.txt') -Value 'keep-me' -Encoding Ascii
Run-Scoop @('update', '-f', 'OpenBrowser') | Out-Null
Test-Junction $obLink $obTarget 'OpenBrowser after force-update'
Assert (Test-Path (Join-Path $obLink 'marker.txt')) 'user data survived force update'
if (Test-Path $mainJs) {
    $txt2 = [System.IO.File]::ReadAllText($mainJs, $utf8)
    Assert ($txt2.Contains('normFileUrl')) 'patch re-applied after force update'
}

Step 'OpenBrowser: normal uninstall keeps persist data'
Run-Scoop @('uninstall', 'OpenBrowser') | Out-Null
Assert (-not (Test-Path -LiteralPath $obLink)) 'junction removed on uninstall'
Assert (Test-Path (Join-Path $obTarget 'marker.txt')) 'persist data kept on normal uninstall'

Step 'OpenBrowser: uninstall -p purges persist data'
Run-Scoop @('install', 'baobao-test/OpenBrowser') | Out-Null
Test-Junction $obLink $obTarget 'OpenBrowser before -p'
Run-Scoop @('uninstall', '-p', 'OpenBrowser') | Out-Null
Assert (-not (Test-Path -LiteralPath $obLink)) 'junction removed with -p'
Assert (-not (Test-Path (Join-Path $ScoopDir 'persist\OpenBrowser'))) 'persist purged with -p'

# ---------------------------------------------------------------- 3. Twinkstar
Step 'Twinkstar: fresh install'
Run-Scoop @('install', 'baobao-test/Twinkstar') | Out-Null
$tsLink = Join-Path $env:LOCALAPPDATA 'Twinkstar'
$tsTarget = Join-Path $ScoopDir 'persist\Twinkstar\UserData'
Test-Junction $tsLink $tsTarget 'Twinkstar'

Step 'Twinkstar: force update keeps data'
Set-Content -Path (Join-Path $tsLink 'marker.txt') -Value 'keep-me' -Encoding Ascii
Run-Scoop @('update', '-f', 'Twinkstar') | Out-Null
Test-Junction $tsLink $tsTarget 'Twinkstar after force-update'
Assert (Test-Path (Join-Path $tsLink 'marker.txt')) 'user data survived force update'

Step 'Twinkstar: normal uninstall keeps persist data'
Run-Scoop @('uninstall', 'Twinkstar') | Out-Null
Assert (-not (Test-Path -LiteralPath $tsLink)) 'junction removed on uninstall'
Assert (Test-Path (Join-Path $tsTarget 'marker.txt')) 'persist data kept on normal uninstall'

Step 'Twinkstar: uninstall -p purges persist data'
Run-Scoop @('install', 'baobao-test/Twinkstar') | Out-Null
Test-Junction $tsLink $tsTarget 'Twinkstar before -p'
Run-Scoop @('uninstall', '-p', 'Twinkstar') | Out-Null
Assert (-not (Test-Path -LiteralPath $tsLink)) 'junction removed with -p'
Assert (-not (Test-Path (Join-Path $ScoopDir 'persist\Twinkstar'))) 'persist purged with -p'

# ---------------------------------------------------------------- 4. fingerprint-chromium
Step 'fingerprint-chromium: fresh install'
Run-Scoop @('install', 'baobao-test/fingerprint-chromium') | Out-Null
$fpcLink = Join-Path $env:LOCALAPPDATA 'Chromium\User Data'
$fpcTarget = Join-Path $ScoopDir 'persist\fingerprint-chromium\UserData'
Test-Junction $fpcLink $fpcTarget 'fingerprint-chromium'

Step 'fingerprint-chromium: force update keeps data'
Set-Content -Path (Join-Path $fpcLink 'marker.txt') -Value 'keep-me' -Encoding Ascii
Run-Scoop @('update', '-f', 'fingerprint-chromium') | Out-Null
Test-Junction $fpcLink $fpcTarget 'fingerprint-chromium after force-update'
Assert (Test-Path (Join-Path $fpcLink 'marker.txt')) 'user data survived force update'

Step 'fingerprint-chromium: normal uninstall keeps persist data'
Run-Scoop @('uninstall', 'fingerprint-chromium') | Out-Null
Assert (-not (Test-Path -LiteralPath $fpcLink)) 'junction removed on uninstall'
Assert (Test-Path (Join-Path $fpcTarget 'marker.txt')) 'persist data kept on normal uninstall'

Step 'fingerprint-chromium: uninstall -p purges persist data'
Run-Scoop @('install', 'baobao-test/fingerprint-chromium') | Out-Null
Test-Junction $fpcLink $fpcTarget 'fingerprint-chromium before -p'
Run-Scoop @('uninstall', '-p', 'fingerprint-chromium') | Out-Null
Assert (-not (Test-Path -LiteralPath $fpcLink)) 'junction removed with -p'
Assert (-not (Test-Path (Join-Path $ScoopDir 'persist\fingerprint-chromium'))) 'persist purged with -p'

# ---------------------------------------------------------------- summary
Step 'Summary'
if ($script:failures -gt 0) {
    Write-Host "E2E FAILED: $($script:failures) assertion(s)."
    exit 1
}
Write-Host 'All E2E checks passed.'
exit 0
