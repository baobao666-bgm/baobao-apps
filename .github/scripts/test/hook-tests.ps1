# Fixture-based behaviour tests for the manifest hook scripts.
# Sandboxes %APPDATA%/%LOCALAPPDATA% and the Typora registry key (SOFTWARE\BaobaoPkgTest\Typora)
# so no real user data is ever touched. Windows only (junctions). ASCII-only file.
[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../')).Path
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$script:passed = 0
$script:failed = 0

function Case([string]$name) { Write-Host "`nCASE: $name" }
function Check([bool]$cond, [string]$msg) {
    if ($cond) { Write-Host "  ok: $msg"; $script:passed++ }
    else { Write-Host "  FAIL: $msg"; $script:failed++ }
}
function Check-Eq($a, $b, [string]$msg) {
    $sa = if ($null -eq $a) { '<null>' } else { [string]$a }
    $sb = if ($null -eq $b) { '<null>' } else { [string]$b }
    Check ($sa -eq $sb) ("$msg (expected='$sb' actual='$sa')")
}

$sandboxRoot = Join-Path $env:TEMP ('baobao-hook-tests-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$caseNo = 0
$testRegRoot = 'SOFTWARE\BaobaoPkgTest\Typora'

function New-CaseDir {
    $script:caseNo++
    $d = Join-Path $sandboxRoot ('case-{0:d2}' -f $script:caseNo)
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    return $d
}
function New-TestFile([string]$path, [string]$content) {
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [System.IO.File]::WriteAllText($path, $content, $utf8)
}
function Get-LinkTargetString([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $it = Get-Item -LiteralPath $path -Force
    if (($null -eq $it.LinkType) -and ((($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) -eq 0)) { return $null }
    return @($it.Target)[0]
}
function Norm([string]$p) {
    if ([string]::IsNullOrEmpty($p)) { return '' }
    $t = $p -replace '^\\\\\?\\', ''
    # canonicalize 8.3 short names (e.g. RUNNER~1 on CI) before comparing
    try { $t = [System.IO.Path]::GetFullPath($t) } catch {}
    if (Test-Path -LiteralPath $t) {
        try {
            $full = (Get-Item -LiteralPath $t -Force).FullName
            if ($full) { $t = $full }
        } catch {}
    }
    return ($t -replace '^\\\\\?\\', '').TrimEnd('\').ToLowerInvariant()
}
function Invoke-Hook {
    param([string]$Text, [string]$Dir, [string]$OriginalDir, [string]$PersistDir)
    $dir = $Dir
    $original_dir = $OriginalDir
    $persist_dir = $PersistDir
    $sb = [scriptblock]::Create($Text)
    $threw = $false
    $err = ''
    try { & $sb | Out-Null } catch { $threw = $true; $err = [string]$_.Exception.Message }
    return @{ Threw = $threw; Error = $err }
}
function Get-ManifestField([string]$manifestFile, [string]$field) {
    $path = Join-Path $RepoRoot ('bucket\' + $manifestFile)
    $json = [System.IO.File]::ReadAllText($path, $utf8) | ConvertFrom-Json
    return [string]$json.$field
}
function Get-TyporaHook([string]$field) {
    $t = Get-ManifestField 'typora0.11.18.json' $field
    # isolate the registry and cut the network fetch (fast-fail to the built-in fallback)
    $t = $t.Replace('SOFTWARE\Typora', 'SOFTWARE\BaobaoPkgTest\Typora')
    $t = $t.Replace('https://typora.io/releases/stable', 'http://127.0.0.1:9/stable')
    $t = $t.Replace('https://typoraio.cn/releases/stable.html', 'http://127.0.0.1:9/stable.html')
    return $t
}
function Get-TestRegInfo {
    $k = $null
    try {
        $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($testRegRoot, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    } catch {}
    if (-not $k) { return $null }
    $denyCount = 0
    foreach ($r in @($k.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
        if ($r.AccessControlType -eq 'Deny') { $denyCount++ }
    }
    $k.Close()
    return @{ DenyCount = $denyCount }
}
function Clear-TestRegKey {
    try {
        $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($testRegRoot, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
        if ($k) {
            $sd = $k.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
            foreach ($r in @($sd.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
                if ($r.AccessControlType -eq 'Deny') { [void]$sd.RemoveAccessRuleSpecific($r) }
            }
            $k.SetAccessControl($sd)
            $k.Close()
        }
    } catch {}
    try { [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree('SOFTWARE\BaobaoPkgTest', $false) } catch {}
}
function Get-TestDenyRights {
    return ([System.Security.AccessControl.RegistryRights]::ReadKey -bor [System.Security.AccessControl.RegistryRights]::QueryValues -bor [System.Security.AccessControl.RegistryRights]::EnumerateSubKeys -bor [System.Security.AccessControl.RegistryRights]::Notify -bor [System.Security.AccessControl.RegistryRights]::SetValue -bor [System.Security.AccessControl.RegistryRights]::CreateSubKey -bor [System.Security.AccessControl.RegistryRights]::Delete -bor [System.Security.AccessControl.RegistryRights]::WriteKey)
}
function Add-TestDenyRule([string]$sidStr) {
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($testRegRoot, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    if (-not $k) { throw 'test key not available' }
    $sd = $k.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
    $sid = New-Object System.Security.Principal.SecurityIdentifier($sidStr)
    $rule = New-Object System.Security.AccessControl.RegistryAccessRule($sid, (Get-TestDenyRights), 'ContainerInherit', 'None', 'Deny')
    $sd.AddAccessRule($rule)
    $k.SetAccessControl($sd)
    $k.Close()
}
function Get-TestDenyCountForSid([string]$sidStr) {
    $k = $null
    try {
        $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($testRegRoot, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    } catch {}
    if (-not $k) { return -1 }
    $n = 0
    foreach ($r in @($k.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
        if (($r.AccessControlType -eq 'Deny') -and (-not $r.IsInherited) -and ($r.IdentityReference.Value -eq $sidStr)) { $n++ }
    }
    $k.Close()
    return $n
}
function Get-HookState([string]$persistDir) {
    $p = Join-Path $persistDir '.registry-state.json'
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    try { return ([System.IO.File]::ReadAllText($p, $utf8) | ConvertFrom-Json) } catch { return $null }
}
function Remove-SandboxTree([string]$root) {
    if (-not (Test-Path -LiteralPath $root)) { return }
    $links = @(Get-ChildItem -LiteralPath $root -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    foreach ($l in $links) {
        attrib -R /L $l.FullName 2>$null | Out-Null
        try { [System.IO.Directory]::Delete($l.FullName, $false) } catch {}
    }
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
function Remove-JunctionAt([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    $it = Get-Item -LiteralPath $path -Force
    if (($null -ne $it.LinkType) -or ((($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) -ne 0)) {
        attrib -R /L $path 2>$null | Out-Null
        try { [System.IO.Directory]::Delete($path, $false) } catch {}
        if (Test-Path -LiteralPath $path) { cmd /c rd "$path" 2>$null | Out-Null }
    } else {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}
function Reset-Path([string]$path) {
    # make sure the path is completely absent before a case sets up its own state
    if (Test-Path -LiteralPath $path) { Remove-JunctionAt $path }
}

$oldAppData = $env:APPDATA
$oldLocalAppData = $env:LOCALAPPDATA
try {
    $env:APPDATA = Join-Path $sandboxRoot 'Roaming'
    $env:LOCALAPPDATA = Join-Path $sandboxRoot 'Local'
    New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA -Force | Out-Null
    Clear-TestRegKey

    $obPost = Get-ManifestField 'OpenBrowser.json' 'post_install'

    # ---------------------------------------------------------------- C1
    Case 'OpenBrowser post_install: fresh install creates the junction'
    Reset-Path (Join-Path $env:APPDATA 'openbrowser')
    $c1 = New-CaseDir
    $dir1 = Join-Path $c1 'apps\OpenBrowser\current'
    $orig1 = Join-Path $c1 'apps\OpenBrowser\1.0.24'
    $pers1 = Join-Path $c1 'persist\OpenBrowser'
    New-Item -ItemType Directory -Path $dir1, $orig1, $pers1 -Force | Out-Null
    $r = Invoke-Hook -Text $obPost -Dir $dir1 -OriginalDir $orig1 -PersistDir $pers1
    Check (-not $r.Threw) "no exception ($($r.Error))"
    $jl = Join-Path $env:APPDATA 'openbrowser'
    Check-Eq (Norm (Get-LinkTargetString $jl)) (Norm (Join-Path $pers1 'UserData')) 'junction target is the persist folder'

    # ---------------------------------------------------------------- C2
    Case 'OpenBrowser post_install: migrates real AppData folder after verified copy'
    Reset-Path (Join-Path $env:APPDATA 'openbrowser')
    $c2 = New-CaseDir
    $dir2 = Join-Path $c2 'apps\OpenBrowser\current'
    $orig2 = Join-Path $c2 'apps\OpenBrowser\1.0.24'
    $pers2 = Join-Path $c2 'persist\OpenBrowser'
    New-Item -ItemType Directory -Path $dir2, $orig2, $pers2 -Force | Out-Null
    $jl2 = Join-Path $env:APPDATA 'openbrowser'
    New-TestFile (Join-Path $jl2 'a.txt') 'hello migration'
    New-TestFile (Join-Path $jl2 'sub\b.bin') ('B' * 2048)
    $r = Invoke-Hook -Text $obPost -Dir $dir2 -OriginalDir $orig2 -PersistDir $pers2
    Check (-not $r.Threw) "no exception ($($r.Error))"
    Check-Eq (Norm (Get-LinkTargetString $jl2)) (Norm (Join-Path $pers2 'UserData')) 'junction created'
    Check (Test-Path (Join-Path $pers2 'UserData\a.txt')) 'migrated a.txt'
    Check (Test-Path (Join-Path $pers2 'UserData\sub\b.bin')) 'migrated sub\b.bin'
    $a = [System.IO.File]::ReadAllText((Join-Path $jl2 'a.txt'), $utf8)
    Check-Eq $a 'hello migration' 'content readable through the junction'
    Check (Test-Path (Join-Path $jl2 'sub\b.bin')) 'nested file readable through the junction'

    # ---------------------------------------------------------------- C2b
    Case 'OpenBrowser post_install: idempotent second run'
    $r = Invoke-Hook -Text $obPost -Dir $dir2 -OriginalDir $orig2 -PersistDir $pers2
    Check (-not $r.Threw) 'no exception on re-run'
    Check-Eq (Norm (Get-LinkTargetString $jl2)) (Norm (Join-Path $pers2 'UserData')) 'junction still correct'
    Check (Test-Path (Join-Path $jl2 'a.txt')) 'data still readable'

    # ---------------------------------------------------------------- C3
    Case 'OpenBrowser post_install: both locations non-empty -> refuses to merge'
    $c3 = New-CaseDir
    $dir3 = Join-Path $c3 'apps\OpenBrowser\current'
    $orig3 = Join-Path $c3 'apps\OpenBrowser\1.0.24'
    $pers3 = Join-Path $c3 'persist\OpenBrowser'
    New-Item -ItemType Directory -Path $dir3, $orig3, (Join-Path $pers3 'UserData') -Force | Out-Null
    $jl3 = Join-Path $env:APPDATA 'openbrowser'
    Reset-Path $jl3
    New-TestFile (Join-Path $jl3 'from-appdata.txt') 'A'
    New-TestFile (Join-Path $pers3 'UserData\from-persist.txt') 'P'
    $r = Invoke-Hook -Text $obPost -Dir $dir3 -OriginalDir $orig3 -PersistDir $pers3
    Check $r.Threw 'threw as expected'
    Check ($r.Error -match 'BOTH locations') "error mentions both locations ($($r.Error))"
    Check (Test-Path (Join-Path $jl3 'from-appdata.txt')) 'AppData data still intact'
    Check (Test-Path (Join-Path $pers3 'UserData\from-persist.txt')) 'persist data still intact'
    Check ((Get-LinkTargetString $jl3) -eq $null) 'no junction was created'
    Reset-Path $jl3

    # ---------------------------------------------------------------- C4
    Case 'OpenBrowser post_install: foreign junction is left untouched'
    $c4 = New-CaseDir
    $dir4 = Join-Path $c4 'apps\OpenBrowser\current'
    $orig4 = Join-Path $c4 'apps\OpenBrowser\1.0.24'
    $pers4 = Join-Path $c4 'persist\OpenBrowser'
    $other4 = Join-Path $c4 'somewhere-else'
    New-Item -ItemType Directory -Path $dir4, $orig4, $pers4, $other4 -Force | Out-Null
    $jl4 = Join-Path $env:APPDATA 'openbrowser'
    Reset-Path $jl4
    New-Item -Path $jl4 -ItemType Junction -Value $other4 | Out-Null
    $r = Invoke-Hook -Text $obPost -Dir $dir4 -OriginalDir $orig4 -PersistDir $pers4
    Check (-not $r.Threw) 'no exception'
    Check-Eq (Norm (Get-LinkTargetString $jl4)) (Norm $other4) 'foreign junction untouched'
    Check (-not (Test-Path (Join-Path $pers4 'UserData'))) 'persist target not created'
    Remove-JunctionAt $jl4

    # ---------------------------------------------------------------- C6
    Case 'fingerprint-chromium post_install: fresh install (path with space)'
    $fpcPost = Get-ManifestField 'fingerprint-chromium.json' 'post_install'
    $c6 = New-CaseDir
    $dir6 = Join-Path $c6 'apps\fingerprint-chromium\current'
    $orig6 = Join-Path $c6 'apps\fingerprint-chromium\150.0.7871.186'
    $pers6 = Join-Path $c6 'persist\fingerprint-chromium'
    New-Item -ItemType Directory -Path $dir6, $orig6, $pers6 -Force | Out-Null
    $jl6 = Join-Path $env:LOCALAPPDATA 'Chromium\User Data'
    Reset-Path $jl6
    $r = Invoke-Hook -Text $fpcPost -Dir $dir6 -OriginalDir $orig6 -PersistDir $pers6
    Check (-not $r.Threw) "no exception ($($r.Error))"
    Check-Eq (Norm (Get-LinkTargetString $jl6)) (Norm (Join-Path $pers6 'UserData')) 'junction target (space path)'

    # ---------------------------------------------------------------- C7
    Case 'Twinkstar pre_uninstall: removes our junction, keeps appdata junction rules intact'
    $tsPre = Get-ManifestField 'Twinkstar.json' 'pre_uninstall'
    $c7 = New-CaseDir
    $dir7 = Join-Path $c7 'apps\Twinkstar\current'
    $pers7 = Join-Path $c7 'persist\Twinkstar'
    New-Item -ItemType Directory -Path $dir7, (Join-Path $pers7 'UserData') -Force | Out-Null
    New-TestFile (Join-Path $pers7 'UserData\marker.txt') 'keep me'
    $jl7 = Join-Path $env:LOCALAPPDATA 'Twinkstar'
    Reset-Path $jl7
    New-Item -Path $jl7 -ItemType Junction -Value (Join-Path $pers7 'UserData') | Out-Null
    $r = Invoke-Hook -Text $tsPre -Dir $dir7 -OriginalDir $dir7 -PersistDir $pers7
    Check (-not $r.Threw) "no exception ($($r.Error))"
    Check (-not (Test-Path -LiteralPath $jl7)) 'junction removed'
    Check (Test-Path (Join-Path $pers7 'UserData\marker.txt')) 'persist data kept'

    # ---------------------------------------------------------------- C8
    Case 'Twinkstar pre_uninstall: foreign junction is left untouched'
    $c8 = New-CaseDir
    $dir8 = Join-Path $c8 'apps\Twinkstar\current'
    $pers8 = Join-Path $c8 'persist\Twinkstar'
    $other8 = Join-Path $c8 'elsewhere'
    New-Item -ItemType Directory -Path $dir8, $pers8, $other8 -Force | Out-Null
    $jl8 = Join-Path $env:LOCALAPPDATA 'Twinkstar'
    Reset-Path $jl8
    New-Item -Path $jl8 -ItemType Junction -Value $other8 | Out-Null
    $r = Invoke-Hook -Text $tsPre -Dir $dir8 -OriginalDir $dir8 -PersistDir $pers8
    Check (-not $r.Threw) 'no exception'
    Check-Eq (Norm (Get-LinkTargetString $jl8)) (Norm $other8) 'foreign junction untouched'
    Remove-JunctionAt $jl8

    # ---------------------------------------------------------------- C9
    Case 'typora: fresh install (junction + registry + profile.data), then uninstall reverts'
    $tyPost = Get-TyporaHook 'post_install'
    $tyPre = Get-TyporaHook 'pre_uninstall'
    $c9 = New-CaseDir
    $dir9 = Join-Path $c9 'apps\typora0.11.18\current'
    $orig9 = Join-Path $c9 'apps\typora0.11.18\0.11.18'
    $pers9 = Join-Path $c9 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir9, $orig9, $pers9 -Force | Out-Null
    $r = Invoke-Hook -Text $tyPost -Dir $dir9 -OriginalDir $orig9 -PersistDir $pers9
    Check (-not $r.Threw) "post_install no exception ($($r.Error))"
    $jl9 = Join-Path $env:APPDATA 'Typora'
    Check-Eq (Norm (Get-LinkTargetString $jl9)) (Norm (Join-Path $pers9 'TyporaData')) 'junction target'
    $pdPath = Join-Path $jl9 'profile.data'
    Check (Test-Path $pdPath) 'profile.data created'
    if (Test-Path $pdPath) {
        $hex = [System.IO.File]::ReadAllText($pdPath, $utf8).Trim()
        $bytes = New-Object byte[] ($hex.Length / 2)
        for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16) }
        $obj = [System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
        Check ($obj.enableAutoUpdate -eq $false) 'enableAutoUpdate=false'
        Check-Eq ([string]$obj.skipUpdate) '1.14.10' 'skipUpdate fallback value'
    }
    $reg = Get-TestRegInfo
    Check ($null -ne $reg) 'test registry key exists'
    Check ($reg.DenyCount -ge 4) "deny rules applied (count=$($reg.DenyCount))"
    $readDenied = $false
    try { $null = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($testRegRoot) } catch { $readDenied = $true }
    Check $readDenied 'plain read of the key is denied'
    New-TestFile (Join-Path $jl9 'keep.txt') 'typora data'
    $r = Invoke-Hook -Text $tyPre -Dir $dir9 -OriginalDir $orig9 -PersistDir $pers9
    Check (-not $r.Threw) "pre_uninstall no exception ($($r.Error))"
    Check (-not (Test-Path -LiteralPath $jl9)) 'junction removed'
    Check (Test-Path (Join-Path $pers9 'TyporaData\keep.txt')) 'persist data kept'
    Check ($null -eq (Get-TestRegInfo)) 'registry key removed (it was created by the package)'

    # ---------------------------------------------------------------- C10
    Case 'typora: legacy junction into the version dir is migrated to persist'
    $c10 = New-CaseDir
    $dir10 = Join-Path $c10 'apps\typora0.11.18\current'
    $orig10 = Join-Path $c10 'apps\typora0.11.18\0.11.18'
    $pers10 = Join-Path $c10 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir10, $orig10, $pers10 -Force | Out-Null
    $legacyData = Join-Path $orig10 'TyporaData'
    New-TestFile (Join-Path $legacyData 'old-pref.txt') 'legacy content'
    $jl10 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl10
    New-Item -Path $jl10 -ItemType Junction -Value $legacyData | Out-Null
    $r = Invoke-Hook -Text $tyPost -Dir $dir10 -OriginalDir $orig10 -PersistDir $pers10
    Check (-not $r.Threw) "no exception ($($r.Error))"
    Check-Eq (Norm (Get-LinkTargetString $jl10)) (Norm (Join-Path $pers10 'TyporaData')) 'junction re-pointed to persist'
    Check (Test-Path (Join-Path $pers10 'TyporaData\old-pref.txt')) 'legacy data migrated'
    Check (-not (Test-Path -LiteralPath $legacyData)) 'legacy source folder removed after verification'

    # ---------------------------------------------------------------- C11
    Case 'typora: data left behind by a force-update move (_*.old) is recovered'
    $c11 = New-CaseDir
    $dir11 = Join-Path $c11 'apps\typora0.11.18\current'
    $orig11 = Join-Path $c11 'apps\typora0.11.18\0.11.18'
    $pers11 = Join-Path $c11 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir11, $orig11, $pers11 -Force | Out-Null
    $oldData = Join-Path $c11 'apps\typora0.11.18\_0.11.18.old\TyporaData'
    New-TestFile (Join-Path $oldData 'rescued.txt') 'from _old'
    $jl11 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl11
    $r = Invoke-Hook -Text $tyPost -Dir $dir11 -OriginalDir $orig11 -PersistDir $pers11
    Check (-not $r.Threw) "no exception ($($r.Error))"
    Check-Eq (Norm (Get-LinkTargetString $jl11)) (Norm (Join-Path $pers11 'TyporaData')) 'junction created'
    Check (Test-Path (Join-Path $pers11 'TyporaData\rescued.txt')) 'data rescued from _*.old'
    Check (-not (Test-Path -LiteralPath $oldData)) '_old source folder cleared after verification'

    # ---------------------------------------------------------------- C12
    Case 'typora: legacy data AND persist data both present -> refuses to merge'
    $c12 = New-CaseDir
    $dir12 = Join-Path $c12 'apps\typora0.11.18\current'
    $orig12 = Join-Path $c12 'apps\typora0.11.18\0.11.18'
    $pers12 = Join-Path $c12 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir12, $orig12, (Join-Path $pers12 'TyporaData') -Force | Out-Null
    $legacy12 = Join-Path $orig12 'TyporaData'
    New-TestFile (Join-Path $legacy12 'old.txt') 'old'
    New-TestFile (Join-Path $pers12 'TyporaData\new.txt') 'new'
    $jl12 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl12
    New-Item -Path $jl12 -ItemType Junction -Value $legacy12 | Out-Null
    $r = Invoke-Hook -Text $tyPost -Dir $dir12 -OriginalDir $orig12 -PersistDir $pers12
    Check $r.Threw 'threw as expected'
    Check ($r.Error -match 'refusing to merge') "error mentions refusal ($($r.Error))"
    Check (Test-Path (Join-Path $legacy12 'old.txt')) 'legacy data intact'
    Check (Test-Path (Join-Path $pers12 'TyporaData\new.txt')) 'persist data intact'

    # ---------------------------------------------------------------- C13
    Case 'typora: pre-existing registry key/values are restored on uninstall'
    Clear-TestRegKey
    $null = New-Item -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Force
    Set-ItemProperty -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Name 'IDate' -Value '01/01/2020'
    Set-ItemProperty -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Name 'Other' -Value 42
    $c13 = New-CaseDir
    $dir13 = Join-Path $c13 'apps\typora0.11.18\current'
    $orig13 = Join-Path $c13 'apps\typora0.11.18\0.11.18'
    $pers13 = Join-Path $c13 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir13, $orig13, $pers13 -Force | Out-Null
    $jl13 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl13
    $r = Invoke-Hook -Text $tyPost -Dir $dir13 -OriginalDir $orig13 -PersistDir $pers13
    Check (-not $r.Threw) "post no exception ($($r.Error))"
    $reg = Get-TestRegInfo
    Check ($reg.DenyCount -ge 4) "denies applied (count=$($reg.DenyCount))"
    $r = Invoke-Hook -Text $tyPre -Dir $dir13 -OriginalDir $orig13 -PersistDir $pers13
    Check (-not $r.Threw) "pre no exception ($($r.Error))"
    $reg = Get-TestRegInfo
    Check ($null -ne $reg) 'key kept (pre-existed)'
    if ($reg) { Check-Eq $reg.DenyCount 0 'denies removed' }
    $idate = (Get-ItemProperty -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Name 'IDate' -ErrorAction SilentlyContinue).IDate
    Check-Eq $idate '01/01/2020' 'IDate restored to the pre-install value'
    $other = (Get-ItemProperty -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Name 'Other' -ErrorAction SilentlyContinue).Other
    Check-Eq $other 42 'unrelated value untouched'

    # ---------------------------------------------------------------- C14
    Case 'typora: unparseable profile.data is left untouched'
    $c14 = New-CaseDir
    $dir14 = Join-Path $c14 'apps\typora0.11.18\current'
    $orig14 = Join-Path $c14 'apps\typora0.11.18\0.11.18'
    $pers14 = Join-Path $c14 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir14, $orig14, $pers14 -Force | Out-Null
    $jl14 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl14
    New-Item -ItemType Directory -Path $jl14 -Force | Out-Null
    New-TestFile (Join-Path $jl14 'profile.data') 'zzzz-not-hex'
    $r = Invoke-Hook -Text $tyPost -Dir $dir14 -OriginalDir $orig14 -PersistDir $pers14
    Check (-not $r.Threw) "no exception ($($r.Error))"
    $content = [System.IO.File]::ReadAllText((Join-Path $jl14 'profile.data'), $utf8)
    Check-Eq $content 'zzzz-not-hex' 'profile.data unchanged'
    Check ((Get-LinkTargetString $jl14) -ne $null) 'junction still created'
    Reset-Path $jl14

    # ---------------------------------------------------------------- C18
    Case 'typora: a deny rule that already existed is never removed by uninstall'
    Clear-TestRegKey
    $null = New-Item -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Force
    Set-ItemProperty -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Name 'IDate' -Value '01/01/2020'
    Add-TestDenyRule 'S-1-5-32-545'
    $c18 = New-CaseDir
    $dir18 = Join-Path $c18 'apps\typora0.11.18\current'
    $orig18 = Join-Path $c18 'apps\typora0.11.18\0.11.18'
    $pers18 = Join-Path $c18 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir18, $orig18, $pers18 -Force | Out-Null
    $jl18 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl18
    $r = Invoke-Hook -Text $tyPost -Dir $dir18 -OriginalDir $orig18 -PersistDir $pers18
    Check (-not $r.Threw) "post no exception ($($r.Error))"
    $st18 = Get-HookState $pers18
    Check ($null -ne $st18) 'state file written'
    if ($st18) {
        Check-Eq ([string]$st18.schemaVersion) '2' 'state schema version'
        Check-Eq (@($st18.addedRules).Count) 3 'only the three genuinely new rules are recorded as ours'
        Check (@($st18.preExistingSids) -contains 'S-1-5-32-545') 'pre-existing rule recorded separately'
    }
    $r = Invoke-Hook -Text $tyPre -Dir $dir18 -OriginalDir $orig18 -PersistDir $pers18
    Check (-not $r.Threw) "pre no exception ($($r.Error))"
    Check-Eq (Get-TestDenyCountForSid 'S-1-5-32-545') 1 'pre-existing deny rule survives the uninstall'
    Check-Eq (Get-TestDenyCountForSid 'S-1-5-11') 0 'rule added by the package was removed'
    Clear-TestRegKey

    # ---------------------------------------------------------------- C19
    Case 'typora: IDate stored as REG_DWORD keeps its value and type after uninstall'
    Clear-TestRegKey
    $null = New-Item -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Force
    $null = New-ItemProperty -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Name 'IDate' -Value 12345 -PropertyType DWord -Force
    $c19 = New-CaseDir
    $dir19 = Join-Path $c19 'apps\typora0.11.18\current'
    $orig19 = Join-Path $c19 'apps\typora0.11.18\0.11.18'
    $pers19 = Join-Path $c19 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir19, $orig19, $pers19 -Force | Out-Null
    $jl19 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl19
    $r = Invoke-Hook -Text $tyPost -Dir $dir19 -OriginalDir $orig19 -PersistDir $pers19
    Check (-not $r.Threw) "post no exception ($($r.Error))"
    $st19 = Get-HookState $pers19
    if ($st19) { Check-Eq ([string]$st19.idateKind) 'DWord' 'value kind recorded as DWord' }
    $r = Invoke-Hook -Text $tyPre -Dir $dir19 -OriginalDir $orig19 -PersistDir $pers19
    Check (-not $r.Threw) "pre no exception ($($r.Error))"
    $k19 = $null
    try { $k19 = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($testRegRoot) } catch {}
    Check ($null -ne $k19) 'key readable again after the deny rules were removed'
    if ($k19) {
        Check-Eq ([string]$k19.GetValueKind('IDate')) 'DWord' 'IDate restored as REG_DWORD'
        Check-Eq ([int]$k19.GetValue('IDate')) 12345 'IDate value restored'
        $k19.Close()
    }
    Clear-TestRegKey

    # ---------------------------------------------------------------- C20
    Case 'typora: corrupt or missing state never triggers a guessed ACL cleanup'
    Clear-TestRegKey
    $null = New-Item -Path 'HKCU:\SOFTWARE\BaobaoPkgTest\Typora' -Force
    $c20 = New-CaseDir
    $dir20 = Join-Path $c20 'apps\typora0.11.18\current'
    $orig20 = Join-Path $c20 'apps\typora0.11.18\0.11.18'
    $pers20 = Join-Path $c20 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir20, $orig20, $pers20 -Force | Out-Null
    $jl20 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl20
    $r = Invoke-Hook -Text $tyPost -Dir $dir20 -OriginalDir $orig20 -PersistDir $pers20
    Check (-not $r.Threw) "post no exception ($($r.Error))"
    $statePath20 = Join-Path $pers20 '.registry-state.json'
    Check (Test-Path -LiteralPath $statePath20) 'state file exists before the damage'
    [System.IO.File]::WriteAllText($statePath20, '{not json', $utf8)
    $r = Invoke-Hook -Text $tyPre -Dir $dir20 -OriginalDir $orig20 -PersistDir $pers20
    Check (-not $r.Threw) "pre no exception on corrupt state ($($r.Error))"
    $reg = Get-TestRegInfo
    Check ($reg.DenyCount -ge 4) "deny rules left in place on corrupt state (count=$($reg.DenyCount))"
    Check (Test-Path -LiteralPath $statePath20) 'corrupt state file kept for inspection'
    Check-Eq ([System.IO.File]::ReadAllText($statePath20, $utf8)) '{not json' 'corrupt state file not overwritten'
    Remove-Item -LiteralPath $statePath20 -Force
    $r = Invoke-Hook -Text $tyPre -Dir $dir20 -OriginalDir $orig20 -PersistDir $pers20
    Check (-not $r.Threw) "pre no exception on missing state ($($r.Error))"
    $reg = Get-TestRegInfo
    Check ($reg.DenyCount -ge 4) "deny rules left in place on missing state (count=$($reg.DenyCount))"
    Check (-not (Test-Path -LiteralPath $statePath20)) 'missing state file is not re-created'
    Clear-TestRegKey

    # ---------------------------------------------------------------- C21
    Case 'OpenBrowser post_install: a nested link in the data folder aborts the migration'
    $c21 = New-CaseDir
    $dir21 = Join-Path $c21 'apps\OpenBrowser\current'
    $orig21 = Join-Path $c21 'apps\OpenBrowser\1.0.24'
    $pers21 = Join-Path $c21 'persist\OpenBrowser'
    $outside21 = Join-Path $c21 'outside'
    New-Item -ItemType Directory -Path $dir21, $orig21, $pers21, $outside21 -Force | Out-Null
    $jl21 = Join-Path $env:APPDATA 'openbrowser'
    Reset-Path $jl21
    New-TestFile (Join-Path $jl21 'a.txt') 'keep me'
    New-TestFile (Join-Path $outside21 'precious.txt') 'MUST-SURVIVE'
    New-Item -Path (Join-Path $jl21 'nested') -ItemType Junction -Value $outside21 | Out-Null
    $r = Invoke-Hook -Text $obPost -Dir $dir21 -OriginalDir $orig21 -PersistDir $pers21
    Check $r.Threw 'threw as expected'
    Check ($r.Error -match 'nested link') "error names the problem ($($r.Error))"
    Check ($r.Error -match 'nothing was moved') 'error states that nothing was moved'
    Check (Test-Path (Join-Path $jl21 'a.txt')) 'source data still intact'
    Check ((Get-LinkTargetString $jl21) -eq $null) 'source is still a real folder, no junction created'
    Check (-not (Test-Path (Join-Path $pers21 'UserData\a.txt'))) 'nothing was copied to persist'
    Check (Test-Path (Join-Path $outside21 'precious.txt')) 'link target untouched'
    Reset-Path $jl21

    # ---------------------------------------------------------------- C22
    Case 'typora: a nested link in a legacy data folder aborts the migration'
    $c22 = New-CaseDir
    $dir22 = Join-Path $c22 'apps\typora0.11.18\current'
    $orig22 = Join-Path $c22 'apps\typora0.11.18\0.11.18'
    $pers22 = Join-Path $c22 'persist\typora0.11.18'
    New-Item -ItemType Directory -Path $dir22, $orig22, $pers22 -Force | Out-Null
    $legacy22 = Join-Path $orig22 'TyporaData'
    $outside22 = Join-Path $c22 'outside'
    New-Item -ItemType Directory -Path $outside22 -Force | Out-Null
    New-TestFile (Join-Path $legacy22 'old-pref.txt') 'legacy content'
    New-TestFile (Join-Path $outside22 'precious.txt') 'MUST-SURVIVE'
    New-Item -Path (Join-Path $legacy22 'nested') -ItemType Junction -Value $outside22 | Out-Null
    $jl22 = Join-Path $env:APPDATA 'Typora'
    Reset-Path $jl22
    New-Item -Path $jl22 -ItemType Junction -Value $legacy22 | Out-Null
    $r = Invoke-Hook -Text $tyPost -Dir $dir22 -OriginalDir $orig22 -PersistDir $pers22
    Check $r.Threw 'threw as expected'
    Check ($r.Error -match 'nested link') "error names the problem ($($r.Error))"
    Check (Test-Path (Join-Path $legacy22 'old-pref.txt')) 'legacy data intact'
    Check (Test-Path (Join-Path $outside22 'precious.txt')) 'link target untouched'
    Check-Eq (Norm (Get-LinkTargetString $jl22)) (Norm $legacy22) 'junction was not repointed'
    Reset-Path $jl22

    # ---------------------------------------------------------------- C23
    Case 'process matching: package paths are matched with a separator boundary'
    $preText = Get-ManifestField 'OpenBrowser.json' 'pre_install'
    $fnMatch = [regex]::Match($preText, '(?s)function Get-BaobaoProcessPrefix\s*\{.*?\n\}')
    Check $fnMatch.Success 'prefix helper present in the generated script'
    if ($fnMatch.Success) {
        Invoke-Expression $fnMatch.Value
        $pfx = Get-BaobaoProcessPrefix 'D:\apps\Foo'
        Check-Eq $pfx 'd:\apps\foo\' 'prefix keeps the trailing separator'
        Check (('d:\apps\foobar\app.exe').StartsWith($pfx) -eq $false) 'sibling dir sharing the prefix does not match'
        Check (('d:\apps\foo\current\app.exe').StartsWith($pfx)) 'executable inside the package dir still matches'
        Check-Eq (Get-BaobaoProcessPrefix 'D:\apps\Foo\') $pfx 'input with a trailing separator is handled'
    }

    # ---------------------------------------------------------------- C15-C17 openbrowser patch
    $obPre = Get-ManifestField 'OpenBrowser.json' 'pre_install'
    $fixture = @'
// minimal stand-in for OpenBrowser main.js (anchors only)
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
function trustedAppIndexUrl() {
  return pathToFileURL(path.join(__dirname, 'index.html')).href;
}
function isTrustedSender(senderUrl) {
  const expected = trustedAppIndexUrl();
  const raw = String(senderUrl || '');
  return raw === expected;
}
module.exports = { isTrustedSender };
'@

    Case 'OpenBrowser pre_install: pristine main.js gets fully patched'
    $c15 = New-CaseDir
    $dir15 = Join-Path $c15 'app'
    $main15 = Join-Path $dir15 'runtime\resources\app\main.js'
    New-TestFile $main15 $fixture
    $r = Invoke-Hook -Text $obPre -Dir $dir15 -OriginalDir $dir15 -PersistDir (Join-Path $c15 'persist')
    Check (-not $r.Threw) "no exception ($($r.Error))"
    $patched = [System.IO.File]::ReadAllText($main15, $utf8)
    Check ($patched.Contains('function normFileUrl(url){')) 'helper injected'
    Check ($patched.Contains('const expected = normFileUrl(trustedAppIndexUrl());')) 'expected-url normalized'
    Check ($patched.Contains('const raw = normFileUrl(senderUrl);')) 'sender-url normalized'
    Check ($patched.Contains('fileURLToPath')) 'import extended'
    Check (-not (Test-Path -LiteralPath ($main15 + '.patchcheck.tmp.js'))) 'temp check file cleaned up'

    Case 'OpenBrowser pre_install: already-patched main.js is left as-is'
    $before = [System.IO.File]::ReadAllText($main15, $utf8)
    $r = Invoke-Hook -Text $obPre -Dir $dir15 -OriginalDir $dir15 -PersistDir (Join-Path $c15 'persist')
    $after = [System.IO.File]::ReadAllText($main15, $utf8)
    Check (-not $r.Threw) 'no exception on re-run'
    Check ($before -eq $after) 'file unchanged (idempotent)'

    Case 'OpenBrowser pre_install: partially patched file is completed'
    $c16 = New-CaseDir
    $dir16 = Join-Path $c16 'app'
    $main16 = Join-Path $dir16 'runtime\resources\app\main.js'
    $partial = $fixture.Replace("const { pathToFileURL } = require('url');", "const { pathToFileURL, fileURLToPath } = require('url');")
    New-TestFile $main16 $partial
    $r = Invoke-Hook -Text $obPre -Dir $dir16 -OriginalDir $dir16 -PersistDir (Join-Path $c16 'persist')
    Check (-not $r.Threw) "no exception ($($r.Error))"
    $patched16 = [System.IO.File]::ReadAllText($main16, $utf8)
    Check ($patched16.Contains('function normFileUrl(url){')) 'missing pieces applied'
    Check ($patched16.Contains('const raw = normFileUrl(senderUrl);')) 'sender-url normalized'

    Case 'OpenBrowser pre_install: broken upstream (anchor missing) -> aborts, file untouched'
    $c17 = New-CaseDir
    $dir17 = Join-Path $c17 'app'
    $main17 = Join-Path $dir17 'runtime\resources\app\main.js'
    $broken = $fixture.Replace('const expected = trustedAppIndexUrl();', 'const expected = computeTrustedUrl();')
    New-TestFile $main17 $broken
    $r = Invoke-Hook -Text $obPre -Dir $dir17 -OriginalDir $dir17 -PersistDir (Join-Path $c17 'persist')
    Check $r.Threw 'threw as expected'
    Check ($r.Error -match 'expected-url statement not found') "clear error ($($r.Error))"
    $untouched = [System.IO.File]::ReadAllText($main17, $utf8)
    Check-Eq $untouched $broken 'file untouched'
}
finally {
    $env:APPDATA = $oldAppData
    $env:LOCALAPPDATA = $oldLocalAppData
    Clear-TestRegKey
    Remove-SandboxTree $sandboxRoot
}

Write-Host ""
Write-Host ("== hook tests: {0} passed, {1} failed ==" -f $script:passed, $script:failed)
if ($script:failed -gt 0) { exit 1 }
exit 0
