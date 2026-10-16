# TEMPORARY diagnostics for the Twinkstar shortcut failure on the CI runner.
# Tests WScript.Shell Save() across {powershell.exe, pwsh, pwsh -STA} x {ascii, chinese} names.
# Delete this file + its workflow step once the root cause is fixed.
# ASCII-only file.
[CmdletBinding()]
param(
    [string]$Scratch = (Join-Path $env:TEMP 'shortcut-diag')
)

$ErrorActionPreference = 'Continue'

Write-Host "== environment =="
Write-Host "PSVersion        : $($PSVersionTable.PSVersion)"
Write-Host "apartment        : $([System.Threading.Thread]::CurrentThread.GetApartmentState())"
$pwshCmd = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if ($pwshCmd) { Write-Host "pwsh available   : $($pwshCmd.Source)" } else { Write-Host 'pwsh available   : NO' }
Write-Host "output encoding  : $([Console]::OutputEncoding.WebName)"
$chcp = (& cmd /c chcp) 2>&1
Write-Host "chcp             : $chcp"

if (Test-Path -LiteralPath $Scratch) { Remove-Item -LiteralPath $Scratch -Recurse -Force }
New-Item -ItemType Directory -Path $Scratch -Force | Out-Null
$targetDir = Join-Path $Scratch 'target'
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
$targetExe = Join-Path $targetDir 'dummy.exe'
[System.IO.File]::WriteAllText($targetExe, 'MZ dummy')

$runner = Join-Path $Scratch 'runner.ps1'
$runnerCode = @'
param([string]$Lnk, [string]$Target)
try {
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($Lnk)
    $s.TargetPath = $Target
    $s.WorkingDirectory = (Split-Path -Parent $Target)
    $s.Save()
    Write-Output "SAVE_OK $Lnk"
} catch {
    Write-Output ("SAVE_FAIL " + $_.Exception.GetType().FullName + " :: " + $_.Exception.Message)
}
'@
[System.IO.File]::WriteAllText($runner, $runnerCode, (New-Object System.Text.UTF8Encoding($false)))

$shells = @(
    @{ Name = 'powershell.exe'; Exe = 'powershell.exe' },
    @{ Name = 'pwsh'; Exe = 'pwsh.exe' },
    @{ Name = 'pwsh-STA'; Exe = 'pwsh.exe' }
)
$chineseName = ([char]0x661F) + ([char]0x613F) + ([char]0x6D4F) + ([char]0x89C8) + ([char]0x5668) + '.lnk'
$names = @(
    @{ Label = 'ascii'; File = 'PlainName.lnk' },
    @{ Label = 'chinese'; File = $chineseName }
)

foreach ($shell in $shells) {
    if (($shell.Exe -eq 'pwsh.exe') -and (-not $pwshCmd)) { continue }
    foreach ($n in $names) {
        $caseDir = Join-Path $Scratch (($shell.Name -replace ' ', '_') + '-' + $n.Label)
        New-Item -ItemType Directory -Path $caseDir -Force | Out-Null
        $lnk = Join-Path $caseDir $n.File
        Write-Host "== case shell=$($shell.Name) name=$($n.Label)"
        $spawnArgs = @('-NoProfile')
        if ($shell.Name -like '*STA*') { $spawnArgs += '-STA' }
        $spawnArgs += @('-ExecutionPolicy', 'Bypass', '-File', $runner, $lnk, $targetExe)
        $procOut = & $shell.Exe @spawnArgs 2>&1 | Out-String
        Write-Host ("   result: " + $procOut.Trim())
        Write-Host "   lnk exists: $(Test-Path -LiteralPath $lnk)"
    }
}

Remove-Item -LiteralPath $Scratch -Recurse -Force -ErrorAction SilentlyContinue
Write-Host '== diagnostics done'
exit 0
