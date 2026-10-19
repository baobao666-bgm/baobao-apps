# -*- coding: utf-8 -*-
"""baobao-apps 清单钩子脚本（PowerShell 源码文本库）

所有脚本由本文件组合生成并嵌入 bucket/*.json —— 本文件是唯一源文件，不要手改 JSON。
改完运行：python tools/manifests/make_manifests.py（CI 用 --check 校验两者一致）。

设计约束（对应 PLAN 二.3/二.4/二.5 与 AGENTS.md）：
  - 关键步骤失败必须 throw（不静默吞错）；卸载时宁可中止，也不丢"哪些规则是本包加的"这一依据
  - 优先优雅退出进程，等待最多 15 秒，否则中止并提示用户
  - 数据迁移 = robocopy + 文件清单/大小/哈希逐项核对，核对通过才删源
  - 双非空数据：停止，绝不合并
  - 外来联接（别的工具建的）：不接管、不迁移、不删除，并且**不写穿**它（Typora 的 profile.data
    就属于"写穿"，已改为跳过）；需要硬失败时用 BAOBAO_STRICT_FOREIGN_LINK=1
"""

# ---------------------------------------------------------------------------
# 共享块：优雅退出
# ---------------------------------------------------------------------------
STOP_PROCESS = r'''
function Get-BaobaoProcessPrefix {
    param([string]$AppDir)
    # Only executables INSIDE the package dir may match. Without the trailing
    # separator, "apps\Foo" would also match a sibling such as "apps\FooBar".
    return ($AppDir.TrimEnd('\').ToLowerInvariant() + '\')
}
function Stop-BaobaoProcessGracefully {
    param([string]$AppDir, [string]$Label)
    $prefix = Get-BaobaoProcessPrefix $AppDir
    $targets = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and ($_.Path.ToLowerInvariant().StartsWith($prefix))
    })
    if ($targets.Count -eq 0) { return }
    Write-Host "$Label`: asking $($targets.Count) process(es) to close gracefully..."
    foreach ($p in $targets) {
        try { $null = $p.CloseMainWindow() } catch {}
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and ($_.Path.ToLowerInvariant().StartsWith($prefix))
        })
        if ($left.Count -eq 0) { return }
        Start-Sleep -Milliseconds 500
    }
    $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and ($_.Path.ToLowerInvariant().StartsWith($prefix))
    })
    if ($left.Count -gt 0) {
        throw "$Label is still running (PIDs: $($left.Id -join ', ')). Please close it completely and retry."
    }
}
'''

# ---------------------------------------------------------------------------
# 共享块：联接（junction）帮助函数
# ---------------------------------------------------------------------------
LINK_HELPERS = r'''
function Get-BaobaoLinkInfo {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $it = Get-Item -LiteralPath $Path -Force
    $isLink = ($null -ne $it.LinkType) -or (($it.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    if ($isLink) { return @{ IsLink = $true; Target = (@($it.Target)[0]) } }
    return @{ IsLink = $false; Target = $null }
}
function Get-BaobaoNormPath {
    param([string]$Path)
    if ([string]::IsNullOrEmpty($Path)) { return '' }
    $p = $Path -replace '^\\\\\?\\', ''
    # 8.3 short names (RUNNER~1) vs long names can differ between env vars and
    # filesystem read-backs; canonicalize before comparing.
    try { $p = [System.IO.Path]::GetFullPath($p) } catch {}
    if (Test-Path -LiteralPath $p) {
        try {
            $full = (Get-Item -LiteralPath $p -Force).FullName
            if ($full) { $p = $full }
        } catch {}
    }
    return ($p -replace '^\\\\\?\\', '').TrimEnd('\').ToLowerInvariant()
}
function New-BaobaoJunction {
    param([string]$Link, [string]$Target)
    $parent = Split-Path -Parent $Link
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (-not (Test-Path -LiteralPath $Target)) { New-Item -ItemType Directory -Path $Target -Force | Out-Null }
    New-Item -Path $Link -ItemType Junction -Value $Target -ErrorAction Stop | Out-Null
    if (-not (Test-Path -LiteralPath $Link)) { throw "junction creation failed: $Link -> $Target" }
}
function Remove-BaobaoJunctionOnly {
    param([string]$Link)
    attrib -R /L $Link 2>$null | Out-Null
    try { [System.IO.Directory]::Delete($Link, $false) } catch {}
    if (Test-Path -LiteralPath $Link) { cmd /c rd "$Link" 2>$null | Out-Null }
    return (-not (Test-Path -LiteralPath $Link))
}
function Remove-BaobaoJunctionWithRetry {
    param([string]$Link, [int]$Attempts = 3, [int]$DelayMs = 700)
    # A link usually refuses to go away because a process still holds it; give it a
    # few short retries before the caller decides to abort.
    for ($i = 1; $i -le $Attempts; $i++) {
        if (Remove-BaobaoJunctionOnly $Link) { return $true }
        if ($i -lt $Attempts) { Start-Sleep -Milliseconds $DelayMs }
    }
    return $false
}
function Remove-BaobaoLinkOrEmptyDir {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    if (-not (Remove-BaobaoJunctionOnly $Path)) {
        throw "refusing to delete a non-empty folder: $Path"
    }
}
'''

# ---------------------------------------------------------------------------
# 共享块：迁移（copy -> 核对 -> 由调用方删除源）
# ---------------------------------------------------------------------------
VERIFY_MOVE = r'''
function Get-BaobaoNestedLinks {
    param([string]$Root)
    # The folder being migrated must be a plain tree. robocopy /XJ silently skips
    # junctions / symlinks, so a nested link would disappear during the move while
    # every check below still passed. The root itself is expected to be a real
    # folder, so scanning starts at its children.
    $found = New-Object System.Collections.ArrayList
    $rootItem = Get-Item -LiteralPath $Root -Force
    $stack = New-Object System.Collections.Stack
    $stack.Push($rootItem)
    while ($stack.Count -gt 0) {
        $cur = $stack.Pop()
        foreach ($child in @(Get-ChildItem -LiteralPath $cur.FullName -Force -ErrorAction Stop)) {
            if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                [void]$found.Add($child.FullName)
                continue
            }
            if ($child -is [System.IO.DirectoryInfo]) { $stack.Push($child) }
        }
    }
    return $found
}
function Get-BaobaoFileMap {
    param([string]$Root)
    # Walk the tree instead of doing prefix math on FullName: short (8.3) vs long
    # path forms would otherwise corrupt the relative paths and fail verification.
    $map = @{}
    $rootItem = Get-Item -LiteralPath $Root -Force
    $stack = New-Object System.Collections.Stack
    $stack.Push(@{ Item = $rootItem; Rel = '' })
    while ($stack.Count -gt 0) {
        $frame = $stack.Pop()
        foreach ($child in @(Get-ChildItem -LiteralPath $frame.Item.FullName -Force -ErrorAction Stop)) {
            if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                # Never skip a link quietly: skipping makes the copy look complete.
                throw "verification hit a nested link: $($child.FullName)"
            }
            $rel = $child.Name
            if ($frame.Rel) { $rel = $frame.Rel + '\' + $child.Name }
            if ($child -is [System.IO.DirectoryInfo]) {
                $stack.Push(@{ Item = $child; Rel = $rel })
            } else {
                $map[$rel] = $child.Length
            }
        }
    }
    return $map
}
function Invoke-BaobaoVerifiedMove {
    param([string]$Source, [string]$Target, [string]$Label)
    $links = @(Get-BaobaoNestedLinks $Source)
    if ($links.Count -gt 0) {
        throw ("$Label`: found $($links.Count) nested link(s) in the folder to migrate;`n" +
            "  robocopy /XJ would skip them and the link itself would be lost, so nothing was moved.`n  " +
            ($links -join "`n  ") + "`nResolve these links manually (move the target in, or delete the link), then re-run.")
    }
    $roco = Join-Path $env:SystemRoot 'System32\Robocopy.exe'
    & $roco $Source $Target /E /COPY:DAT /R:2 /W:2 /XJ /NFL /NDL /NJH /NJS /NP | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        throw "$Label`: robocopy failed (exit $rc); source data left untouched."
    }
    $srcMap = Get-BaobaoFileMap $Source
    $dstMap = Get-BaobaoFileMap $Target
    if ($srcMap.Count -ne $dstMap.Count) {
        throw "$Label`: verification failed (file count $($srcMap.Count) vs $($dstMap.Count)); source left untouched."
    }
    foreach ($rel in $srcMap.Keys) {
        if (-not $dstMap.ContainsKey($rel)) {
            throw "$Label`: verification failed (missing $rel); source left untouched."
        }
        if ($dstMap[$rel] -ne $srcMap[$rel]) {
            throw "$Label`: verification failed (size mismatch on $rel); source left untouched."
        }
        $h1 = (Get-FileHash -LiteralPath (Join-Path $Source $rel) -Algorithm SHA256).Hash
        $h2 = (Get-FileHash -LiteralPath (Join-Path $Target $rel) -Algorithm SHA256).Hash
        if ($h1 -ne $h2) {
            throw "$Label`: verification failed (hash mismatch on $rel); source left untouched."
        }
    }
}
'''

# ---------------------------------------------------------------------------
# 通用：AppData 联接 迁移/维护（OpenBrowser / Twinkstar / fingerprint-chromium）
# ---------------------------------------------------------------------------
_GENERIC_JUNCTION_BODY = r'''
$info = Get-BaobaoLinkInfo $jlink
if ($null -eq $info) {
    New-BaobaoJunction -Link $jlink -Target $target
    Write-Host "$label`: user data junction created -> $target"
    return
}
if ($info.IsLink) {
    $tn = Get-BaobaoNormPath $info.Target
    $ok = ($tn -ne '') -and (
        ($tn -eq (Get-BaobaoNormPath $target)) -or
        ($tn -eq (Get-BaobaoNormPath (Join-Path $dir 'UserData'))) -or
        ($tn -eq (Get-BaobaoNormPath (Join-Path $original_dir 'UserData')))
    )
    if ($ok) {
        if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
        Write-Host "$label`: user data junction already correct."
        return
    }
    Write-Host "$label`: $jlink is a junction to '$($info.Target)' which this package does not manage; leaving it untouched."
    return
}

$srcHasData = [bool](Get-ChildItem -LiteralPath $jlink -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
$tgtHasData = $false
if (Test-Path -LiteralPath $target) {
    $tgtHasData = [bool](Get-ChildItem -LiteralPath $target -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
}

if (-not $srcHasData) {
    Remove-BaobaoLinkOrEmptyDir $jlink
    New-BaobaoJunction -Link $jlink -Target $target
    Write-Host "$label`: empty data folder replaced with a junction."
    return
}
if ($tgtHasData) {
    throw ("$label`: BOTH locations contain data; refusing to merge.`n" +
        "  AppData: $jlink`n" +
        "  persist: $target`n" +
        "Resolve manually, then re-run 'scoop update -f {HINT}'.")
}
Invoke-BaobaoVerifiedMove -Source $jlink -Target $target -Label $label
$recheck = Get-BaobaoLinkInfo $jlink
if ($recheck -and $recheck.IsLink) {
    throw "$label`: internal check failed: source turned into a link mid-migration; aborting."
}
Remove-Item -LiteralPath $jlink -Recurse -Force
if (Test-Path -LiteralPath $jlink) {
    throw "$label`: could not remove the migrated source folder $jlink; re-run after freeing locks."
}
New-BaobaoJunction -Link $jlink -Target $target
Write-Host "$label`: user data migrated into the persist folder and linked back."
'''

_JUNCTION_REMOVE_BODY = r'''
if (-not $persist_dir) {
    Write-Host "$label`: persist_dir unavailable; leaving the user data junction untouched."
    return
}
$info = Get-BaobaoLinkInfo $jlink
if ($info -and $info.IsLink) {
    $tn = Get-BaobaoNormPath $info.Target
    $pr = Get-BaobaoNormPath $persist_dir
    if (($pr -ne '') -and $tn.StartsWith($pr + '\')) {
        if (Remove-BaobaoJunctionWithRetry $jlink) {
            Write-Host "$label`: user data junction removed; data stays in the persist folder."
        } else {
            throw ("$label`: could not remove the junction $jlink after 3 attempts." +
                " Uninstall aborted so no dangling link is left behind." +
                ' Close whatever is using it, delete it manually with  rd "' + $jlink + '"  and re-run the uninstall.')
        }
    } else {
        Write-Host "$label`: $jlink points to '$($info.Target)' which is not this package's data; leaving it untouched."
    }
}
'''


# ---------------------------------------------------------------------------
# 通用：桌面快捷方式（仅当清单显式要求时拼接进来）
# ---------------------------------------------------------------------------
SHORTCUT_HELPERS = r'''
function Get-BaobaoDesktopDir {
    # Fixture seam: the hook test suite redirects this into its sandbox so the
    # tests never touch the real desktop.
    if ($env:BAOBAO_DESKTOP_DIR) { return $env:BAOBAO_DESKTOP_DIR }
    return [Environment]::GetFolderPath('Desktop')
}
function Get-BaobaoShortcutTarget {
    param([string]$LinkPath)
    try {
        $ws = New-Object -ComObject WScript.Shell
        return [string]$ws.CreateShortcut($LinkPath).TargetPath
    } catch { return '' }
}
function New-BaobaoDesktopShortcut {
    param([string]$LinkPath, [string]$TargetPath, [string]$WorkDir, [string]$IconPath, [string]$Description, [string]$Label)
    $owner = Get-BaobaoNormPath (Split-Path -Parent $dir)
    if (Test-Path -LiteralPath $LinkPath) {
        $existing = Get-BaobaoNormPath (Get-BaobaoShortcutTarget $LinkPath)
        if ($existing -and $owner -and $existing.StartsWith($owner + '\')) {
            Write-Host "$Label`: desktop shortcut already present."
        } else {
            Write-Host "$Label`: a shortcut already exists at $LinkPath and is not managed by this package; leaving it untouched."
        }
        return
    }
    try {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($LinkPath)
        $sc.TargetPath = $TargetPath
        $sc.WorkingDirectory = $WorkDir
        if ($IconPath -and (Test-Path -LiteralPath $IconPath)) { $sc.IconLocation = $IconPath + ',0' }
        if ($Description) { $sc.Description = $Description }
        $sc.Save()
        if (-not (Test-Path -LiteralPath $LinkPath)) { throw 'the .lnk file was not created' }
        Write-Host "$Label`: desktop shortcut created -> $LinkPath"
    } catch {
        # A missing shortcut must never fail an otherwise good install
        # (WScript.Shell is unavailable in some stripped-down images).
        Write-Host "$Label`: WARNING desktop shortcut could not be created: $($_.Exception.Message)"
    }
}
function Remove-BaobaoDesktopShortcut {
    param([string]$LinkPath, [string]$Label)
    if (-not (Test-Path -LiteralPath $LinkPath)) { return }
    $target = Get-BaobaoShortcutTarget $LinkPath
    if (-not $target) {
        Write-Host "$Label`: $LinkPath is not a readable shortcut; leaving it untouched."
        return
    }
    $t = Get-BaobaoNormPath $target
    $owner = Get-BaobaoNormPath (Split-Path -Parent $dir)
    if (-not ($t -and $owner -and $t.StartsWith($owner + '\'))) {
        Write-Host "$Label`: $LinkPath points outside this package; leaving it untouched."
        return
    }
    try { Remove-Item -LiteralPath $LinkPath -Force }
    catch { Write-Host "$Label`: WARNING could not remove the desktop shortcut: $($_.Exception.Message)"; return }
    if (Test-Path -LiteralPath $LinkPath) {
        Write-Host "$Label`: WARNING the desktop shortcut is still present after removal."
    } else {
        Write-Host "$Label`: desktop shortcut removed."
    }
}
'''

# 桌面快捷方式的目标一律走 apps\<app>\current\...，这样升级换版本后快捷方式不失效。
OPENBROWSER_DESKTOP_LINK = r'''
$scCurrent = Join-Path (Split-Path -Parent $dir) 'current'
New-BaobaoDesktopShortcut -LinkPath (Join-Path (Get-BaobaoDesktopDir) 'OpenBrowser.lnk') `
    -TargetPath (Join-Path $scCurrent 'runtime\OpenBrowser.exe') `
    -WorkDir $scCurrent `
    -IconPath (Join-Path $scCurrent 'runtime\resources\app\assets\logo.ico') `
    -Description 'OpenBrowser' -Label 'OpenBrowser'
'''

OPENBROWSER_DESKTOP_UNLINK = r'''
Remove-BaobaoDesktopShortcut -LinkPath (Join-Path (Get-BaobaoDesktopDir) 'OpenBrowser.lnk') -Label 'OpenBrowser'
'''


def generic_post_install(jlink_expr, label, hint, desktop_link=None):
    body = _GENERIC_JUNCTION_BODY.replace('{HINT}', hint)
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + LINK_HELPERS
        + VERIFY_MOVE
        + (SHORTCUT_HELPERS if desktop_link else '')
        + "\nif (-not $persist_dir) { throw '" + label + ": $persist_dir is not available (scoop too old?).' }\n"
        + "$jlink  = " + jlink_expr + "\n"
        + "$target = Join-Path $persist_dir 'UserData'\n"
        + "$label  = '" + label + "'\n"
        + (desktop_link or '')
        + body
    )


def generic_pre_uninstall(jlink_expr, label, process_label, desktop_unlink=None):
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + STOP_PROCESS
        + LINK_HELPERS
        + (SHORTCUT_HELPERS if desktop_unlink else '')
        + "\nStop-BaobaoProcessGracefully -AppDir (Split-Path -Parent $dir) -Label '" + process_label + "'\n"
        + "$jlink = " + jlink_expr + "\n"
        + "$label = '" + label + "'\n"
        + (desktop_unlink or '')
        + _JUNCTION_REMOVE_BODY
    )


# ---------------------------------------------------------------------------
# OpenBrowser pre_install：优雅退出 + 平铺 + 受限自愈补丁
# ---------------------------------------------------------------------------
def openbrowser_pre_install():
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + STOP_PROCESS
        + r'''
Stop-BaobaoProcessGracefully -AppDir (Split-Path -Parent $dir) -Label 'OpenBrowser'

# flatten the extracted wrapper directory (archive wraps files in OpenBrowser-*)
$sub = Get-ChildItem "$dir" -Directory | Where-Object { $_.Name -match '^OpenBrowser-' }
foreach ($s in $sub) {
    Move-Item "$($s.FullName)/*" $dir -Force
    Remove-Item $s.FullName -Force -Recurse
}

# ---------------------------------------------------------------------------
# Constrained self-heal patch for runtime\resources\app\main.js.
# Rationale: under Scoop, "current" is a junction; Node resolves __dirname to the
# real version dir while the renderer keeps the junction path, so OpenBrowser's
# trusted-IPC check rejects the UI ("untrusted IPC document"), breaking all
# Local-Settings buttons. The patch normalizes BOTH sides of that comparison -
# it keeps the original trust check intact and does not relax security.
#
# Strict mode: EVERY anchor must match (or be verifiably already applied) BEFORE
# anything is written; a structural change upstream aborts the install loudly
# instead of writing a half patch.
# ---------------------------------------------------------------------------
$mf = Join-Path $dir 'runtime\resources\app\main.js'
if (-not (Test-Path $mf)) {
    throw 'OpenBrowser: runtime\resources\app\main.js not found (upstream layout changed); refusing to write a half patch.'
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
$c = [System.IO.File]::ReadAllText($mf, $utf8)

$markerImport   = "const { pathToFileURL, fileURLToPath } = require('url');"
$anchorImport   = "const { pathToFileURL } = require('url');"
$markerFn       = 'function normFileUrl(url){'
$anchorFnBlock  = 'function trustedAppIndexUrl() {'
$fnImpl         = 'function normFileUrl(url){try{return pathToFileURL(fs.realpathSync(fileURLToPath(String(url)))).href}catch(_){return String(url)}}'
$markerExpected = 'const expected = normFileUrl(trustedAppIndexUrl());'
$anchorExpected = 'const expected = trustedAppIndexUrl();'
$markerRaw      = 'const raw = normFileUrl(senderUrl);'
$anchorRaw      = "const raw = String(senderUrl || '');"
$plain          = "return pathToFileURL(path.join(__dirname, 'index.html')).href;"
$v1             = "try { return pathToFileURL(fs.realpathSync(path.join(__dirname, 'index.html')).href; } catch (_) { return pathToFileURL(path.join(__dirname, 'index.html')).href; }"

$already = $c.Contains($markerImport) -and $c.Contains($markerFn) -and $c.Contains($markerExpected) -and $c.Contains($markerRaw)
if ($already) {
    Write-Host 'OpenBrowser: IPC junction patch already fully applied.'
    return
}

$applied = New-Object System.Collections.ArrayList

if ($c.Contains($anchorImport)) {
    $c = $c.Replace($anchorImport, $markerImport)
    [void]$applied.Add('import')
} elseif (-not $c.Contains($markerImport)) {
    throw 'OpenBrowser patch: import anchor not found (main.js structure changed upstream). Aborting; nothing was written.'
}

if ($c.Contains($v1)) {
    $c = $c.Replace($v1, $plain)
    [void]$applied.Add('revert-v1')
}

if ($c.Contains($anchorFnBlock)) {
    if (-not $c.Contains($markerFn)) {
        $c = $c.Replace($anchorFnBlock, $fnImpl + "`n" + $anchorFnBlock)
        [void]$applied.Add('helper')
    }
} elseif (-not $c.Contains($markerFn)) {
    throw 'OpenBrowser patch: trusted-url helper anchor not found (main.js structure changed upstream). Aborting; nothing was written.'
}

if ($c.Contains($anchorExpected)) {
    $c = $c.Replace($anchorExpected, $markerExpected)
    [void]$applied.Add('expected-url')
} elseif (-not $c.Contains($markerExpected)) {
    throw 'OpenBrowser patch: expected-url statement not found (main.js structure changed upstream). Aborting; nothing was written.'
}

if ($c.Contains($anchorRaw)) {
    $c = $c.Replace($anchorRaw, $markerRaw)
    [void]$applied.Add('sender-url')
} elseif (-not $c.Contains($markerRaw)) {
    throw 'OpenBrowser patch: sender-url statement not found (main.js structure changed upstream). Aborting; nothing was written.'
}

foreach ($m in @($markerImport, $markerFn, $markerExpected, $markerRaw)) {
    if (-not $c.Contains($m)) {
        throw 'OpenBrowser patch: post-check failed; nothing was written. Aborting.'
    }
}

$checkFile = $mf + '.patchcheck.tmp.js'
[System.IO.File]::WriteAllText($checkFile, $c, $utf8)
try {
    $nodeExe = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($nodeExe) {
        & $nodeExe.Source --check $checkFile | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw 'OpenBrowser patch: node --check rejected the patched file; nothing was written. Aborting.'
        }
        Write-Host 'OpenBrowser: patched file passed node --check.'
    } else {
        Write-Host 'OpenBrowser: node not found; skipped the optional syntax check.'
    }
} finally {
    if (Test-Path -LiteralPath $checkFile) {
        Remove-Item -LiteralPath $checkFile -Force -ErrorAction SilentlyContinue
    }
}
[System.IO.File]::WriteAllText($mf, $c, $utf8)
Write-Host "OpenBrowser: IPC patch applied ($($applied -join ', '))."
'''
    )


# ---------------------------------------------------------------------------
# fingerprint-chromium pre_install：优雅退出 + 平铺
# ---------------------------------------------------------------------------
def fpc_pre_install():
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + STOP_PROCESS
        + r'''
Stop-BaobaoProcessGracefully -AppDir (Split-Path -Parent $dir) -Label 'fingerprint-chromium'

$sub = Get-ChildItem "$dir" -Directory | Where-Object { $_.Name -match 'ungoogled-chromium_' }
foreach ($s in $sub) {
    Move-Item "$($s.FullName)\*" $dir -Force
    Remove-Item $s.FullName -Force -Recurse
}
'''
    )


# ---------------------------------------------------------------------------
# Typora：post_install（联接迁移 + 注册表记录式修复 + profile.data 最小改写）
# ---------------------------------------------------------------------------
def typora_post_install():
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + STOP_PROCESS
        + LINK_HELPERS
        + VERIFY_MOVE
        + r'''
if (-not $persist_dir) { throw 'Typora: $persist_dir is not available (scoop too old?).' }
$utf8 = New-Object System.Text.UTF8Encoding($false)

# --- A. stop Typora launched from this package -----------------------------
Stop-BaobaoProcessGracefully -AppDir (Split-Path -Parent $dir) -Label 'Typora'

# --- B. %APPDATA%\Typora junction -> <persist>\TyporaData ------------------
# Handles: fresh install, old-design legacy junctions (pointing into the
# version dir), leftover data from a force-update move (_*.old), and plain
# real directories. Never merges two non-empty data sets.
$jlink  = Join-Path $env:APPDATA 'Typora'
$target = Join-Path $persist_dir 'TyporaData'
$appDir = Split-Path -Parent $dir

function Test-BaobaoTyporaOurs {
    param([string]$RawTarget)
    $tn = Get-BaobaoNormPath $RawTarget
    if ($tn -eq '') { return $false }
    if ($tn -eq (Get-BaobaoNormPath $target)) { return $true }
    if ($tn -eq (Get-BaobaoNormPath (Join-Path $dir 'TyporaData'))) { return $true }
    if ($tn -eq (Get-BaobaoNormPath (Join-Path $original_dir 'TyporaData'))) { return $true }
    if ($tn -like '*\apps\typora0.11.18\*\typoradata') { return $true }
    return $false
}

$info = Get-BaobaoLinkInfo $jlink
$tgtHasData = $false
if (Test-Path -LiteralPath $target) {
    $tgtHasData = [bool](Get-ChildItem -LiteralPath $target -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
}

# collect candidate data sources left by legacy layouts
$sources = New-Object System.Collections.ArrayList
if ($info -and (-not $info.IsLink)) {
    if ([bool](Get-ChildItem -LiteralPath $jlink -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        [void]$sources.Add($jlink)
    }
} elseif ($info -and $info.IsLink -and (Test-BaobaoTyporaOurs $info.Target) -and ((Get-BaobaoNormPath $info.Target) -ne (Get-BaobaoNormPath $target))) {
    $rt = @($info.Target)[0]
    if ($rt) {
        $rp = $rt -replace '^\\\\\?\\', ''
        if (Test-Path -LiteralPath $rp) {
            $rpInfo = Get-BaobaoLinkInfo $rp
            if (-not ($rpInfo -and $rpInfo.IsLink)) {
                if ([bool](Get-ChildItem -LiteralPath $rp -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                    [void]$sources.Add($rp)
                }
            }
        }
    }
}
foreach ($sib in @(Get-ChildItem -LiteralPath $appDir -Directory -Force -ErrorAction SilentlyContinue)) {
    if ($sib.Name -eq 'current') { continue }
    $cand = Join-Path $sib.FullName 'TyporaData'
    if (-not (Test-Path -LiteralPath $cand)) { continue }
    $candInfo = Get-BaobaoLinkInfo $cand
    if ($candInfo -and $candInfo.IsLink) { continue }
    if (-not [bool](Get-ChildItem -LiteralPath $cand -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) { continue }
    [void]$sources.Add($cand)
}
$unique = @()
foreach ($s in $sources) {
    $n = Get-BaobaoNormPath $s
    $dup = $false
    foreach ($u in $unique) { if ((Get-BaobaoNormPath $u) -eq $n) { $dup = $true; break } }
    if (-not $dup) { $unique += $s }
}

if ($unique.Count -gt 1) {
    throw ("Typora: multiple legacy data folders found; refusing to guess.`n  " + ($unique -join "`n  "))
}
if ($unique.Count -eq 1) {
    if ($tgtHasData) {
        throw ("Typora: legacy data ($($unique[0])) and persist data ($target) both exist; refusing to merge. " +
            "Resolve manually, then re-run 'scoop update -f typora0.11.18'.")
    }
    Invoke-BaobaoVerifiedMove -Source $unique[0] -Target $target -Label 'Typora'
    Remove-Item -LiteralPath $unique[0] -Recurse -Force
    Write-Host "Typora: user data migrated from $($unique[0])"
    $tgtHasData = $true
}

$needJunction = $true
$foreignLink = $false
if ($info -and $info.IsLink) {
    if ((Get-BaobaoNormPath $info.Target) -eq (Get-BaobaoNormPath $target)) {
        $needJunction = $false
    } elseif (-not (Test-BaobaoTyporaOurs $info.Target)) {
        $foreignLink = $true
        $needJunction = $false
    }
}
if ($foreignLink) {
    # The link and everything behind it belong to someone else: do not migrate,
    # do not delete, and above all do not write through it (section D is skipped).
    Write-Host ("Typora: $jlink points to '$($info.Target)', which this package does not manage." +
        ' The junction and its data are left untouched; profile.data is NOT written, so the' +
        ' update-notice suppression is not applied. Only the registry fix (which stops the' +
        ' expiry popup) is applied, otherwise this build would not start.')
    if ($env:BAOBAO_STRICT_FOREIGN_LINK -eq '1') {
        throw ("Typora: $jlink is a junction this package does not manage." +
            ' BAOBAO_STRICT_FOREIGN_LINK=1 asks the install to stop here; no links, files or registry' +
            ' entries were changed (a running Typora may have been asked to close).')
    }
}
if ($needJunction) {
    if ($info -and $info.IsLink) {
        if (-not (Remove-BaobaoJunctionOnly $jlink)) { throw "Typora: could not remove the old junction $jlink" }
    } elseif ($info -and (-not $info.IsLink)) {
        if (Test-Path -LiteralPath $jlink) { Remove-BaobaoLinkOrEmptyDir $jlink }
    }
    New-BaobaoJunction -Link $jlink -Target $target
    Write-Host "Typora: user data junction ready -> $target"
}

# --- C. registry: recorded, minimal-scope handling --------------------------
$stateFile  = Join-Path $persist_dir '.registry-state.json'
$regPath    = 'SOFTWARE\Typora'
$denySids   = @('S-1-5-11', 'S-1-5-18', 'S-1-5-32-544', 'S-1-5-32-545')
$denyRights = [System.Security.AccessControl.RegistryRights]::ReadKey -bor [System.Security.AccessControl.RegistryRights]::QueryValues -bor [System.Security.AccessControl.RegistryRights]::EnumerateSubKeys -bor [System.Security.AccessControl.RegistryRights]::Notify -bor [System.Security.AccessControl.RegistryRights]::SetValue -bor [System.Security.AccessControl.RegistryRights]::CreateSubKey -bor [System.Security.AccessControl.RegistryRights]::Delete -bor [System.Security.AccessControl.RegistryRights]::WriteKey

function Open-BaobaoAclKey {
    param([string]$Path)
    try {
        return [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Path,
            [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree,
            ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    } catch { return $null }
}

# Only schemaVersion 2 is fully trusted; a version 1 file is migrated on purpose
# (see below). Anything else - unparsable, unknown schema - is NOT the same as "no
# state": treating it that way would re-derive ownership from the live ACL, relabel
# this package's own rules as someone else's, and overwrite the only record.
$state = $null
$stateOk = $false
if (Test-Path -LiteralPath $stateFile) {
    try {
        $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $sn0 = @($state.PSObject.Properties.Name)
        $sv0 = 0
        if ($sn0 -contains 'schemaVersion') { $sv0 = [int]$state.schemaVersion }
        $v1 = ($sn0 -contains 'version') -and ([int]$state.version -eq 1)
        $stateOk = ($sv0 -eq 2) -or $v1
    } catch { $stateOk = $false }
}
if ((-not $stateOk) -and (Test-Path -LiteralPath $stateFile)) {
    throw ("Typora: the registry state file $stateFile could not be parsed or has an unknown schema." +
        ' Install aborted and that file left untouched: it is the only record of which deny rules this' +
        ' package owns, and re-deriving them from the live ACL would mislabel them (a later uninstall' +
        ' would then leave them behind).' +
        ' Restore the file and re-run, or - if you accept that this package will no longer revert' +
        ' HKCU\SOFTWARE\Typora by itself - delete it deliberately first.')
}
$keyExistedBefore = $null
$idatePrev        = $null
$idateKind        = $null
$addedRules       = @()
$preExistingSids  = @()
$deriveFromV1     = $false
if ($state) {
    $snames = @($state.PSObject.Properties.Name)
    $sv = 0
    if ($snames -contains 'schemaVersion') { $sv = [int]$state.schemaVersion }
    if ($sv -eq 2) {
        $keyExistedBefore = [bool]$state.keyExistedBefore
        $idatePrev = [string]$state.idatePrev
        $idateKind = [string]$state.idateKind
        if (($snames -contains 'addedRules') -and $state.addedRules) { $addedRules = @($state.addedRules) }
        if (($snames -contains 'preExistingSids') -and $state.preExistingSids) { $preExistingSids = @($state.preExistingSids) }
    } elseif (($snames -contains 'version') -and ([int]$state.version -eq 1)) {
        # v1 kept the requested SIDs and the old IDate string, but neither the set of
        # rules it really added nor the value type. Keep the identity fields; the rules
        # are re-derived from the live ACL and marked as legacy in this run.
        $keyExistedBefore = [bool]$state.keyExistedBefore
        $idatePrev = [string]$state.idatePrev
        $idateKind = 'String'   # v1 only wrote REG_SZ; Typora's own IDate is a string
        $deriveFromV1 = $true
    }
}

if ($null -eq $keyExistedBefore) {
    $probe = Open-BaobaoAclKey $regPath
    $keyExistedBefore = $null -ne $probe
    if ($probe) { $probe.Close() }
    $idatePrev = '__absent__'
    $idateKind = '__absent__'
    if ($keyExistedBefore) {
        # read the current value through a dedicated read handle (the ACL handle above
        # deliberately lacks KEY_QUERY_VALUE and cannot read values)
        $rk = $null
        try { $rk = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($regPath) } catch {}
        if ($rk) {
            try {
                if (@($rk.GetValueNames()) -contains 'IDate') {
                    $v = $rk.GetValue('IDate', $null)
                    if ($null -eq $v) {
                        $idatePrev = '__absent__'
                        $idateKind = '__absent__'
                    } else {
                        $idatePrev = [string]$v
                        try { $idateKind = [string]$rk.GetValueKind('IDate') } catch { $idateKind = '__unknown__' }
                    }
                }
            } catch {
                $idatePrev = '__unknown__'
                $idateKind = '__unknown__'
            }
            $rk.Close()
        } else {
            $idatePrev = '__unknown__'
            $idateKind = '__unknown__'
        }
    }
}

$keyPresent = $false
try {
    $p = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($regPath, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    if ($p) { $keyPresent = $true; $p.Close() }
} catch {}
if (-not $keyPresent) {
    New-Item -Path 'HKCU:\SOFTWARE\Typora' -Force -ErrorAction Stop | Out-Null
}

$wkey = $null
try { $wkey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($regPath, $true) } catch {}
if ($wkey) {
    try { $wkey.SetValue('IDate', '12/31/2099', [Microsoft.Win32.RegistryValueKind]::String) }
    catch { Write-Host "Typora: IDate left as-is ($($_.Exception.Message))." }
    $wkey.Close()
} else {
    Write-Host 'Typora: could not open the key for writing; IDate left as-is.'
}

$key = $null
try {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($regPath, [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree, ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
} catch {}
if ($null -eq $key) { throw 'Typora: could not open HKCU\SOFTWARE\Typora for permission changes.' }

$sd = $key.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
$existingRules = @($sd.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
$addedNow = @()
foreach ($sidStr in $denySids) {
    $already = $false
    foreach ($rule in $existingRules) {
        if (($rule.AccessControlType -eq 'Deny') -and ($rule.IdentityReference.Value -eq $sidStr) -and ([int]$rule.RegistryRights -eq [int]$denyRights) -and (-not $rule.IsInherited)) { $already = $true; break }
    }
    if ($already) {
        # A rule we already own (recorded in an earlier run) must not be re-labelled
        # as "pre-existing", or a later uninstall would stop removing it.
        # In a v1 migration every matching rule IS ours: v1 recorded exactly these
        # SIDs (a constant) as the ones it added, so reusing that claim is faithful
        # to the old record - the live ACL is not consulted to decide ownership.
        if (-not $deriveFromV1) {
            $ownedBefore = $false
            foreach ($r0 in $addedRules) { if ([string]$r0.sid -eq $sidStr) { $ownedBefore = $true; break } }
            if ((-not $ownedBefore) -and ($preExistingSids -notcontains $sidStr)) { $preExistingSids += $sidStr }
        }
        continue
    }
    $sid = New-Object System.Security.Principal.SecurityIdentifier($sidStr)
    $newRule = New-Object System.Security.AccessControl.RegistryAccessRule($sid, $denyRights, 'ContainerInherit', 'None', 'Deny')
    $sd.AddAccessRule($newRule)
    $addedNow += $sidStr
}

# Provisional record before touching the ACL: if this process dies between
# SetAccessControl and the final write below, the uninstaller still knows which
# rules this package owns. The final write replaces it with the verified shapes.
$pendingSids = @()
foreach ($r in $addedRules) { $pendingSids += [string]$r.sid }
foreach ($s in $addedNow) { $pendingSids += $s }
$pendingSids = @($pendingSids | Select-Object -Unique)
$pendingRules = @()
foreach ($s in $pendingSids) {
    $pendingRules += [pscustomobject]@{ sid = $s; rights = [int]$denyRights; inheritance = 'ContainerInherit'; propagation = 'None' }
}
if (-not (Test-Path -LiteralPath $persist_dir)) { New-Item -ItemType Directory -Path $persist_dir -Force | Out-Null }
$tmpState = $stateFile + '.tmp'
$stateOut = [pscustomobject]@{
    schemaVersion    = 2
    keyExistedBefore = $keyExistedBefore
    idatePrev        = $idatePrev
    idateKind        = $idateKind
    addedRules       = @($pendingRules)
    preExistingSids  = @($preExistingSids | Select-Object -Unique)
    pending          = $true
    recordedAt       = (Get-Date).ToString('o')
}
$stateOut | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tmpState -Encoding UTF8
Move-Item -LiteralPath $tmpState -Destination $stateFile -Force

$key.SetAccessControl($sd)
$key.Close()

# Re-read the ACL and record only the rules that really exist, with their real shape.
$verifyKey = Open-BaobaoAclKey $regPath
if ($null -eq $verifyKey) { throw 'Typora: could not re-read the registry ACL after applying it.' }
$verifyRules = @($verifyKey.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
$verifyKey.Close()

$recordSids = @($pendingSids)
if ($deriveFromV1) { $recordSids = @($denySids) }
$finalRules = @()
foreach ($sidStr in $denySids) {
    $match = $null
    foreach ($rule in $verifyRules) {
        if (($rule.AccessControlType -eq 'Deny') -and ($rule.IdentityReference.Value -eq $sidStr) -and ([int]$rule.RegistryRights -eq [int]$denyRights) -and (-not $rule.IsInherited)) { $match = $rule; break }
    }
    if ($null -eq $match) { throw "Typora: registry deny rule for $sidStr failed to apply." }
    if ($recordSids -contains $sidStr) {
        $finalRules += [pscustomobject]@{
            sid         = $sidStr
            rights      = [int]$match.RegistryRights
            inheritance = [string]$match.InheritanceFlags
            propagation = [string]$match.PropagationFlags
        }
    }
}
$stateOut = [pscustomobject]@{
    schemaVersion    = 2
    keyExistedBefore = $keyExistedBefore
    idatePrev        = $idatePrev
    idateKind        = $idateKind
    addedRules       = @($finalRules)
    preExistingSids  = @($preExistingSids | Select-Object -Unique)
    pending          = $false
    recordedAt       = (Get-Date).ToString('o')
}
$stateOut | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tmpState -Encoding UTF8
Move-Item -LiteralPath $tmpState -Destination $stateFile -Force
Write-Host "Typora: registry handling applied (IDate preset, value kind recorded as $idateKind; $(@($finalRules).Count) rule(s) recorded as added by this package, $(@($preExistingSids).Count) pre-existing rule(s) left untouched)."

# --- D. profile.data: only touch the fields that need fixing -----------------
if ($foreignLink) {
    Write-Host ('Typora: %APPDATA%\Typora is a junction this package does not manage; profile.data was' +
        ' left untouched, so the update-notice suppression is not applied.')
} else {
    $pd = Join-Path $env:APPDATA 'Typora\profile.data'
    $obj = $null
    $parseFailed = $false
    if (Test-Path -LiteralPath $pd) {
        try {
            $hex = ([System.IO.File]::ReadAllText($pd, $utf8)).Trim()
            if (($hex.Length -gt 0) -and (($hex.Length % 2) -eq 0)) {
                $bytes = New-Object byte[] ($hex.Length / 2)
                for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16) }
                $obj = ([System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json -ErrorAction Stop)
            } else {
                $parseFailed = $true
            }
        } catch { $parseFailed = $true }
        if ((-not $parseFailed) -and ($null -eq $obj)) { $parseFailed = $true }
        if ($parseFailed) {
            Write-Host 'Typora: profile.data could not be parsed; leaving it untouched (no field changes applied).'
        }
    }
    if (($null -eq $obj) -and (-not $parseFailed)) {
        $obj = New-Object PSObject
    }

    $skip = $null
    foreach ($u in 'https://typora.io/releases/stable', 'https://typoraio.cn/releases/stable.html') {
        if ($skip) { break }
        try {
            $tmp = Join-Path $env:TEMP 'typora_latest.html'
            & (Join-Path $env:SystemRoot 'System32\curl.exe') -sL --max-time 15 -o $tmp $u 2>$null
            if (Test-Path $tmp) {
                $page = [System.IO.File]::ReadAllText($tmp, [System.Text.Encoding]::UTF8)
                Remove-Item $tmp -Force -ErrorAction SilentlyContinue
                $m = [regex]::Match($page, '<h2>([0-9]+\.[0-9]+\.[0-9]+)</h2>')
                if ($m.Success) { $skip = $m.Groups[1].Value }
            }
        } catch { $skip = $null }
    }
    if ((-not $skip) -and $obj -and ($obj.PSObject.Properties.Name -contains 'skipUpdate')) {
        if (-not [string]::IsNullOrEmpty([string]$obj.skipUpdate)) { $skip = [string]$obj.skipUpdate }
    }
    if (-not $skip) { $skip = '1.14.10' }

    if ($obj) {
        $needWrite = $false
        if (-not ($obj.PSObject.Properties.Name -contains 'enableAutoUpdate')) { $needWrite = $true }
        elseif ($obj.enableAutoUpdate -ne $false) { $needWrite = $true }
        if (-not ($obj.PSObject.Properties.Name -contains 'skipUpdate')) { $needWrite = $true }
        elseif ([string]::IsNullOrEmpty([string]$obj.skipUpdate)) { $needWrite = $true }
        if ($needWrite) {
            $obj | Add-Member -Force -MemberType NoteProperty -Name 'enableAutoUpdate' -Value $false
            $obj | Add-Member -Force -MemberType NoteProperty -Name 'skipUpdate' -Value $skip
            $json = $obj | ConvertTo-Json -Depth 20 -Compress
            $verify = $null
            try { $verify = $json | ConvertFrom-Json -ErrorAction Stop } catch { $verify = $null }
            if (($null -eq $verify) -or ($verify.enableAutoUpdate -ne $false) -or ([string]::IsNullOrEmpty([string]$verify.skipUpdate))) {
                throw 'Typora: profile.data serialization self-check failed; file left untouched.'
            }
            $out = ([System.BitConverter]::ToString([System.Text.Encoding]::UTF8.GetBytes($json))).Replace('-', '').ToLower()
            $tmpPd = $pd + '.tmp'
            [System.IO.File]::WriteAllText($tmpPd, $out, [System.Text.Encoding]::ASCII)
            Move-Item -LiteralPath $tmpPd -Destination $pd -Force
            Write-Host "Typora: profile.data updated (enableAutoUpdate=false, skipUpdate=$skip)."
        } else {
            Write-Host 'Typora: profile.data already correct; left untouched.'
        }
    }
}
'''
    )


# ---------------------------------------------------------------------------
# Typora：pre_uninstall（优雅退出 + 联接移除 + 记录的注册表回退）
# ---------------------------------------------------------------------------
def typora_pre_uninstall():
    return (
        "$ErrorActionPreference = 'Stop'\n"
        + STOP_PROCESS
        + LINK_HELPERS
        + r'''
Stop-BaobaoProcessGracefully -AppDir (Split-Path -Parent $dir) -Label 'Typora'

# --- registry: revert only what this package recorded -----------------------
# This runs BEFORE the junction is removed on purpose. A revert that cannot be
# completed aborts the uninstall, and at that moment the app must still look
# intact; removing the junction first would leave an installed app whose data
# entry point had vanished, which reads to the user as "my settings are gone".
$regPath = 'SOFTWARE\Typora'

function Open-BaobaoAclKey {
    param([string]$Path)
    try {
        return [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Path,
            [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadWriteSubTree,
            ([System.Security.AccessControl.RegistryRights]::ChangePermissions -bor [System.Security.AccessControl.RegistryRights]::ReadPermissions))
    } catch { return $null }
}
function Test-BaobaoRegKeyPresent {
    param([string]$Path)
    # "The key is gone" (nothing left to revert) and "the key is there but we may
    # not read it" must not be confused: only the first one means "done".
    try {
        $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Path)
        if ($null -eq $k) { return $false }
        $k.Close()
        return $true
    } catch { return $true }
}
function Restore-BaobaoRegValue {
    param([Microsoft.Win32.RegistryKey]$Key, [string]$Name, [string]$Prev, [string]$Kind)
    # Write, then read back value *and* type: a silent write failure would leave
    # the package value behind while the log claimed the restore succeeded.
    $map = @{
        'String'       = [Microsoft.Win32.RegistryValueKind]::String
        'ExpandString' = [Microsoft.Win32.RegistryValueKind]::ExpandString
        'DWord'        = [Microsoft.Win32.RegistryValueKind]::DWord
        'QWord'        = [Microsoft.Win32.RegistryValueKind]::QWord
    }
    if (-not $map.ContainsKey($Kind)) { throw "the recorded value kind '$Kind' cannot be restored automatically" }
    $value = $Prev
    if ($Kind -eq 'DWord') { $value = [int]$Prev }
    elseif ($Kind -eq 'QWord') { $value = [long]$Prev }
    $Key.SetValue($Name, $value, $map[$Kind])
    if (@($Key.GetValueNames()) -notcontains $Name) { throw "$Name is missing after the restore" }
    $backKind = [string]$Key.GetValueKind($Name)
    if ($backKind -ne $Kind) { throw "$Name came back as $backKind instead of $Kind" }
    $back = [string]$Key.GetValue($Name, $null)
    if ($back -ne [string]$value) { throw "$Name came back as '$back' instead of '$value'" }
}

$stateFile = $null
if ($persist_dir) { $stateFile = Join-Path $persist_dir '.registry-state.json' }
$state = $null
$stateReadable = $false
if ($stateFile -and (Test-Path -LiteralPath $stateFile)) {
    try { $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop } catch { $state = $null }
    if ($null -ne $state) {
        $snames = @($state.PSObject.Properties.Name)
        if (($snames -contains 'schemaVersion') -and ([int]$state.schemaVersion -eq 2) -and ($snames -contains 'addedRules')) {
            $stateReadable = $true
        }
    }
}

$addedRules = @()
if ($stateReadable -and $state.addedRules) { $addedRules = @($state.addedRules) }

$revertDone = $false
$removed = 0
if (-not $stateFile) {
    Write-Host 'Typora: persist_dir unavailable; skipping the recorded registry revert.'
    $revertDone = $true
} elseif (-not (Test-Path -LiteralPath $stateFile)) {
    # No record at all. Deriving ownership from the live ACL here would be a
    # guess, and a wrong guess silently weakens an ACL that someone else owns.
    Write-Host ("Typora: no registry state file at $stateFile; nothing was reverted." +
        ' If HKCU\SOFTWARE\Typora still denies ReadKey/QueryValues/EnumerateSubKeys/Notify/SetValue/CreateSubKey/Delete/WriteKey' +
        ' for S-1-5-11, S-1-5-18, S-1-5-32-544 and S-1-5-32-545, remove those rules manually.')
    $revertDone = $true
} elseif (-not $stateReadable) {
    throw ("Typora: the registry state file $stateFile is unreadable or from an unknown schema." +
        ' Uninstall aborted and the file kept: it is the only record of which deny rules this package added.' +
        ' Restore the file and re-run the uninstall, or - if you deliberately want the uninstall to leave' +
        " HKCU\SOFTWARE\Typora alone - delete that file first and re-run 'scoop uninstall typora0.11.18'.")
} elseif (-not (Test-BaobaoRegKeyPresent $regPath)) {
    Write-Host 'Typora: HKCU\SOFTWARE\Typora no longer exists; the rules this package added went with it.'
    $revertDone = $true
} else {
    $key = Open-BaobaoAclKey $regPath
    if ($null -eq $key) {
        throw ("Typora: HKCU\SOFTWARE\Typora exists but cannot be opened for permission changes." +
            ' Uninstall aborted and the state file kept, so the recorded deny rules can still be reverted later.' +
            ' Close tools that hold the key (or re-run from an elevated shell) and try again.')
    }
    $sd = $key.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
    $currentRules = @($sd.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    foreach ($rec in $addedRules) {
        # match the exact shape (sid + mask + inheritance + propagation) and only
        # non-inherited rules, so a similar rule from another tool survives
        $matched = $null
        foreach ($rule in $currentRules) {
            if ($rule.AccessControlType -ne 'Deny') { continue }
            if ($rule.IsInherited) { continue }
            if ($rule.IdentityReference.Value -ne [string]$rec.sid) { continue }
            if ([int]$rule.RegistryRights -ne [int]$rec.rights) { continue }
            if ([string]$rule.InheritanceFlags -ne [string]$rec.inheritance) { continue }
            if ([string]$rule.PropagationFlags -ne [string]$rec.propagation) { continue }
            $matched = $rule
            break
        }
        if ($null -ne $matched) {
            # RemoveAccessRuleSpecific returns void (not a bool); count the attempt
            # here and rely on the read-back check below to catch failures.
            $sd.RemoveAccessRuleSpecific($matched)
            $removed++
        }
    }
    if ($removed -gt 0) {
        try { $key.SetAccessControl($sd) }
        catch {
            $key.Close()
            throw ("Typora: could not write the reverted ACL: $($_.Exception.Message)" +
                '. Uninstall aborted and the state file kept; re-run after fixing access.')
        }
    }

    $stillThere = @()
    $verifyKey = Open-BaobaoAclKey $regPath
    if ($null -eq $verifyKey) {
        $key.Close()
        throw ('Typora: could not re-read HKCU\SOFTWARE\Typora after the revert; the recorded rules may still be there.' +
            ' Uninstall aborted and the state file kept so this can be retried.')
    }
    $verifyRules = @($verifyKey.GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    $verifyKey.Close()
    foreach ($rec in $addedRules) {
        foreach ($rule in $verifyRules) {
            if ($rule.AccessControlType -ne 'Deny') { continue }
            if ($rule.IsInherited) { continue }
            if ($rule.IdentityReference.Value -ne [string]$rec.sid) { continue }
            if ([int]$rule.RegistryRights -ne [int]$rec.rights) { continue }
            if ([string]$rule.InheritanceFlags -ne [string]$rec.inheritance) { continue }
            if ([string]$rule.PropagationFlags -ne [string]$rec.propagation) { continue }
            $stillThere += [string]$rec.sid
            break
        }
    }
    if ($stillThere.Count -gt 0) {
        $key.Close()
        throw ('Typora: could not remove every recorded deny rule (still present: ' + (($stillThere | Select-Object -Unique) -join ', ') + ').' +
            ' Uninstall aborted and the state file kept so a re-run can retry.')
    }

    if ($state.keyExistedBefore -eq $false) {
        $key.Close()
        try {
            [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($regPath, $false)
        } catch {
            throw ("Typora: could not delete HKCU\SOFTWARE\Typora, which this package created: $($_.Exception.Message)" +
                '. Uninstall aborted and the state file kept.')
        }
        if (Test-BaobaoRegKeyPresent $regPath) {
            throw 'Typora: HKCU\SOFTWARE\Typora still exists after the delete. Uninstall aborted and the state file kept.'
        }
        Write-Host 'Typora: registry key created by this package was removed.'
        $removed = -1
    } else {
        $key.Close()
        # IDate is the last step and it is the only one that may finish "incomplete"
        # without aborting: every deny rule this package added has already been
        # removed and verified above, so the security-relevant part is done. A
        # failure here usually means a *foreign* deny rule still guards the key
        # (e.g. the user's own Deny for Users) - aborting would leave the app
        # installed and force manual ACL surgery for a cosmetic value. So: report
        # it honestly and keep the record instead of claiming success.
        $idPrev = [string]$state.idatePrev
        $idKind = 'String'
        if ($state.PSObject.Properties.Name -contains 'idateKind') { $idKind = [string]$state.idateKind }
        $kw = $null
        try { $kw = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($regPath, $true) } catch {}
        $idateDone = $false
        if ($null -eq $kw) {
            Write-Host 'Typora: HKCU\SOFTWARE\Typora cannot be reopened for writing; IDate was left as-is.'
        } else {
            try {
                if ($idPrev -eq '__unknown__') {
                    Write-Host 'Typora: the pre-install IDate could not be read at install time; leaving the current value as-is.'
                    $idateDone = $true
                } elseif ($idPrev -eq '__absent__') {
                    $kw.DeleteValue('IDate', $false)
                    if (@($kw.GetValueNames()) -contains 'IDate') { throw 'IDate is still present after the delete' }
                    Write-Host 'Typora: IDate removed (it did not exist before this package).'
                    $idateDone = $true
                } else {
                    Restore-BaobaoRegValue -Key $kw -Name 'IDate' -Prev $idPrev -Kind $idKind
                    Write-Host "Typora: IDate restored ($idKind)."
                    $idateDone = $true
                }
            } catch {
                Write-Host "Typora: IDate could NOT be restored: $($_.Exception.Message)"
            }
            $kw.Close()
        }
        if (-not $idateDone) {
            Write-Host ("Typora: the previous IDate was NOT restored. Recorded value: '$idPrev' (kind: $idKind)." +
                " The state file stays at $stateFile so this can be retried; set the value back manually if you prefer." +
                ' Every deny rule this package added was removed successfully.')
            $revertDone = $false
        } else {
            $revertDone = $true
        }
    }
}

# The record is cleared only after a verified revert: a failing revert either
# throws (rule removal could not be completed) or sets $revertDone = $false (only
# IDate stayed behind), and both keep the file. It also has to be cleared here
# rather than left to scoop: `uninstall -p` deletes the persist folder wholesale,
# which would take this record with it.
if ($revertDone -and $stateFile -and (Test-Path -LiteralPath $stateFile)) {
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $stateFile) { throw "Typora: could not clear the state file $stateFile." }
    if ($removed -lt 0) {
        Write-Host 'Typora: registry revert finished (key deleted with its rules); state file cleared.'
    } else {
        Write-Host "Typora: registry revert finished ($removed recorded rule(s) removed); state file cleared."
    }
}

# --- remove our %APPDATA%\Typora junction (data stays in persist) -----------
$jlink = Join-Path $env:APPDATA 'Typora'
$info = Get-BaobaoLinkInfo $jlink
if ($info -and $info.IsLink) {
    $tn = Get-BaobaoNormPath $info.Target
    $ours = $false
    if ($persist_dir -and ($tn -eq (Get-BaobaoNormPath (Join-Path $persist_dir 'TyporaData')))) { $ours = $true }
    if ($tn -like '*\apps\typora0.11.18\*\typoradata') { $ours = $true }
    if ($ours) {
        if (Remove-BaobaoJunctionWithRetry $jlink) {
            Write-Host 'Typora: user data junction removed; data stays in the persist folder.'
        } else {
            throw ('Typora: could not remove the junction ' + $jlink + ' after 3 attempts.' +
                ' Uninstall aborted so no dangling link is left behind.' +
                ' Close whatever is using it, delete it manually with  rd "' + $jlink + '"  and re-run the uninstall.')
        }
    } else {
        Write-Host "Typora: $jlink points to '$($info.Target)' which is not this package's data; leaving it untouched."
    }
}
'''
    )


# ---------------------------------------------------------------------------
# 对外接口：全部脚本
# ---------------------------------------------------------------------------
SCRIPTS = {
    'OpenBrowser.pre_install': openbrowser_pre_install,
    'OpenBrowser.post_install': lambda: generic_post_install(
        "Join-Path $env:APPDATA 'openbrowser'", 'OpenBrowser', 'OpenBrowser', OPENBROWSER_DESKTOP_LINK),
    'OpenBrowser.pre_uninstall': lambda: generic_pre_uninstall(
        "Join-Path $env:APPDATA 'openbrowser'", 'OpenBrowser', 'OpenBrowser', OPENBROWSER_DESKTOP_UNLINK),
    'Twinkstar.post_install': lambda: generic_post_install("Join-Path $env:LOCALAPPDATA 'Twinkstar'", 'Twinkstar', 'Twinkstar'),
    'Twinkstar.pre_uninstall': lambda: generic_pre_uninstall("Join-Path $env:LOCALAPPDATA 'Twinkstar'", 'Twinkstar', 'Twinkstar'),
    'fingerprint-chromium.pre_install': fpc_pre_install,
    'fingerprint-chromium.post_install': lambda: generic_post_install("Join-Path $env:LOCALAPPDATA 'Chromium\\User Data'", 'fingerprint-chromium', 'fingerprint-chromium'),
    'fingerprint-chromium.pre_uninstall': lambda: generic_pre_uninstall("Join-Path $env:LOCALAPPDATA 'Chromium\\User Data'", 'fingerprint-chromium', 'fingerprint-chromium'),
    'typora0.11.18.post_install': typora_post_install,
    'typora0.11.18.pre_uninstall': typora_pre_uninstall,
}
