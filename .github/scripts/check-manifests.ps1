# Syntax-check the PowerShell hook scripts embedded in bucket manifests.
# Extracts script fields (pre_install/post_install/pre_uninstall/post_uninstall/installer/uninstaller)
# from every manifest and parses them with the PowerShell AST parser.
# Works on Windows PowerShell 5.1 and PowerShell 7 (CI). ASCII-only file on purpose.
[CmdletBinding()]
param(
    [string]$BucketDir = (Join-Path $PSScriptRoot '../../bucket')
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$scriptFields = @('pre_install', 'post_install', 'pre_uninstall', 'post_uninstall', 'installer', 'uninstaller')
$failCount = 0

function Get-ScriptText($value) {
    if ($null -eq $value) { return $null }
    $v = $value
    # installer/uninstaller objects carry their code in .script
    if (($v -isnot [string]) -and ($v -is [psobject]) -and ($null -ne $v.PSObject.Properties['script'])) {
        $v = $v.script
    }
    if ($null -eq $v) { return $null }
    if ($v -is [string]) { return $v }
    return (@($v) -join "`r`n")
}

Get-ChildItem -Path $BucketDir -Filter '*.json' -File | ForEach-Object {
    $file = $_
    $text = [System.IO.File]::ReadAllText($file.FullName, $utf8)
    try {
        $manifest = $text | ConvertFrom-Json
    } catch {
        Write-Host "FAIL $($file.Name): invalid JSON: $($_.Exception.Message)"
        $script:failCount++
        return
    }

    foreach ($field in $scriptFields) {
        $code = Get-ScriptText $manifest.$field
        if ([string]::IsNullOrWhiteSpace($code)) { continue }
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors -and $parseErrors.Count -gt 0) {
            foreach ($pe in $parseErrors) {
                Write-Host ("FAIL {0} [{1}] line {2}: {3}" -f $file.Name, $field, $pe.Extent.StartLineNumber, $pe.Message)
            }
            $script:failCount++
        }
    }
    Write-Host "checked $($file.Name)"
}

if ($failCount -gt 0) {
    Write-Host "PowerShell syntax check FAILED ($failCount finding(s))."
    exit 1
}
Write-Host 'All embedded PowerShell scripts parse cleanly.'
exit 0
