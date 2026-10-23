# 打包成可发给别人的版本：个人信息清零、路径换成占位符、机器状态剔除
# 用法：
#   pwsh -File tools\pack.ps1                    # 产出 dist\user-memory-<版本>-<日期>.zip
#   pwsh -File tools\pack.ps1 -NoZip             # 只生成目录，不压包
#   pwsh -File tools\pack.ps1 -Out D:\share\m.zip
# 判定依据是每个模块 frontmatter 的 distribute:  keep | blank | reset | drop
# 注意：保存为 UTF-8 with BOM

param(
    [string]$Out = '',
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $PSScriptRoot
$Refs = Join-Path $Root 'references'
$Tools = Join-Path $Root 'tools'
$Tpl = Join-Path $Root 'templates'
$CfgFile = Join-Path $Root 'memory.config.json'

# ---------- 配置 ----------
if (-not (Test-Path -LiteralPath $CfgFile)) {
    Write-Output '❌ 缺 memory.config.json，先跑 setup.ps1'
    exit 1
}
$cfg = Get-Content -LiteralPath $CfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = (Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw -Encoding UTF8).Trim()
if (-not $ver) { $ver = '0.0.0' }

$script:map = @()
foreach ($k in @('workspace', 'codeRepo', 'algoRepo', 'javaVault', 'home')) {
    $real = $cfg.paths.$k
    $ph = $cfg.placeholders.$k
    if ($real -and $ph) { $script:map += , @($real, $ph) }
}
# 额外的私人名词（项目名、账号、公司名等），写在 config.pack.scrub 里
if ($cfg.pack.scrub) {
    foreach ($p in $cfg.pack.scrub.PSObject.Properties) {
        if ($p.Name -and $p.Value) { $script:map += , @($p.Name, $p.Value) }
    }
}
# 长的先替，避免 <笔记库> 被 home 先切碎
$script:map = @($script:map | Sort-Object { $_.Item1.Length } -Descending)

function Sanitize([string]$text) {
    foreach ($pair in $script:map) {
        if ($pair[0] -and $pair[0].Length -ge 2) { $text = $text.Replace($pair[0], $pair[1]) }
    }
    return $text
}

function Get-FrontField([string]$text, [string]$name, [string]$fallback = '') {
    if ($text -match ('(?m)^' + [regex]::Escape($name) + ':\s*(\S+)\s*$')) { return $Matches[1] }
    return $fallback
}

function Split-Doc([string]$text) {
    if ($text -match '(?s)^(---\r?\n.*?\r?\n---\r?\n)(.*)$') {
        return @{ fm = $Matches[1]; body = $Matches[2] }
    }
    return @{ fm = ''; body = $text }
}

function Get-Head([string]$body) {
    # 标题行 + 紧随的 > 引言（以及夹在中间的空行）
    $lines = $body -split "`r?`n"
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match '^#\s') { $start = $i; break } }
    if ($start -lt 0) { return @{ head = ''; rest = $body } }
    $end = $start
    for ($j = $start + 1; $j -lt $lines.Count; $j++) {
        if ($lines[$j] -match '^\s*>\s?') { $end = $j; continue }
        if ($lines[$j].Trim() -eq '' -and $end -eq $j - 1) { $end = $j; continue }
        break
    }
    return @{
        head = (($lines[0..$end]) -join "`n")
        rest = (($lines[($end + 1)..($lines.Count - 1)]) -join "`n")
    }
}

function ConvertTo-Blank([string]$text) {
    $doc = Split-Doc $text
    $h = Get-Head $doc.body
    $note = "`n> 这个模块是空的：按自己的情况往里加条目，一条只记一个事实，格式见 ``_模板.md``。`n"
    return ($doc.fm + $h.head + $note)
}

function ConvertTo-Reset([string]$text) {
    $doc = Split-Doc $text
    $lines = $doc.body -split "`r?`n"
    $cut = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\|\s*:?-{3,}') { $cut = $i; break }
    }
    if ($cut -ge 0) {
        return (($doc.fm + (($lines[0..$cut]) -join "`n")) + "`n")
    }
    $h = Get-Head $doc.body
    return ($doc.fm + $h.head + "`n> 已清空：这里留你自己的记录。`n")
}

# ---------- 输出目录 ----------
$stamp = (Get-Date).ToString('yyyyMMdd')
$name = 'user-memory-' + $ver + '-' + $stamp
$distRoot = Join-Path $Root 'dist'
if (-not (Test-Path -LiteralPath $distRoot)) { New-Item -ItemType Directory -Path $distRoot -Force | Out-Null }
$target = Join-Path $distRoot $name
if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
New-Item -ItemType Directory -Path $target -Force | Out-Null

# ---------- 复制（剔除机器状态） ----------
$exDirs = @($cfg.pack.excludeDirs)
$exFiles = @($cfg.pack.excludeFiles) + @('memory.config.json')
$stat = @{ keep = 0; blank = 0; reset = 0; drop = 0 }

Get-ChildItem -LiteralPath $Root -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($Root.Length + 1)
    if ($rel -match '^dist\\') { return }
    foreach ($d in $exDirs) {
        if ($rel -match ('(^|\\)' + [regex]::Escape($d) + '(\\|$)')) { return }
    }
    if ($exFiles -contains $_.Name) { return }
    if ($_.Name -match '\.(sqlite3|pyc|zip)$') { return }
    $dest = Join-Path $target $rel
    $dir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
}

# ---------- 逐模块处理 ----------
function Save-Text([string]$path, [string]$text) {
    # 目标目录偶尔被杀软/同步盘短暂占用，重试几次
    for ($i = 0; $i -lt 8; $i++) {
        try {
            [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
            return
        } catch { Start-Sleep -Milliseconds 350 }
    }
    throw ('写不了文件：' + $path)
}

$refTarget = Join-Path $target 'references'
Get-ChildItem -LiteralPath $refTarget -File -Filter *.md |
    Where-Object { $_.Name -notin @('CATALOG.md', '_模板.md') } |
    ForEach-Object {
        $file = $_
        $raw = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        $mode = Get-FrontField $raw 'distribute' 'keep'
        switch ($mode) {
            'drop' {
                for ($i = 0; $i -lt 8; $i++) {
                    try { [System.IO.File]::Delete($file.FullName); break } catch { Start-Sleep -Milliseconds 350 }
                }
                $stat.drop++
                return
            }
            'blank' { $packed = ConvertTo-Blank $raw; $stat.blank++ }
            'reset' { $packed = ConvertTo-Reset $raw; $stat.reset++ }
            default { $packed = $raw; $stat.keep++ }
        }
        Save-Text $file.FullName (Sanitize $packed)
    }

# ---------- SKILL.md / 模板 / 说明也脱敏 ----------
foreach ($rel in @('SKILL.md', 'README.md', 'setup.ps1', 'templates\AGENTS.md', 'references\_模板.md', 'config.example.json')) {
    $p = Join-Path $target $rel
    if (Test-Path -LiteralPath $p) {
        $t = Get-Content -LiteralPath $p -Raw -Encoding UTF8
        Save-Text $p (Sanitize $t)
    }
}

# 工具脚本也过一遍脱敏（里面有示例关键词）
Get-ChildItem -LiteralPath (Join-Path $target 'tools') -File |
    Where-Object { $_.Extension -in '.py', '.ps1' } |
    ForEach-Object {
        $src = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8
        $clean = Sanitize $src
        if ($clean -ne $src) { Save-Text $_.FullName $clean }
    }

# ---------- 被 drop 的模块从 SKILL.md 里摘掉（路由表行 + 显式标记块） ----------
$dropped = @(
    (Get-ChildItem -LiteralPath $Refs -File -Filter *.md |
        Where-Object { (Get-FrontField (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8) 'distribute') -eq 'drop' } |
        ForEach-Object { $_.BaseName })
)
# 引用可能写成完整文件名（`02-学习习惯与笔记偏好`）也可能只写编号（`02`），两种都要认
$droppedSpecs = @()
foreach ($d in $dropped) {
    $droppedSpecs += $d
    $id = (Get-FrontField (Get-Content -LiteralPath (Join-Path $Refs ($d + '.md')) -Raw -Encoding UTF8) 'id') -replace '"', ''
    if ($id -and $id -ne $d) { $droppedSpecs += $id }
}
$skillPath = Join-Path $target 'SKILL.md'
if (Test-Path -LiteralPath $skillPath) {
    $lines = Get-Content -LiteralPath $skillPath -Encoding UTF8
    $kept = @()
    $inBlock = $false
    foreach ($ln in $lines) {
        if ($ln -match 'dist:drop-begin') { $inBlock = $true; continue }
        if ($ln -match 'dist:drop-end') { $inBlock = $false; continue }
        if ($inBlock) { continue }
        $hit = $false
        foreach ($d in $droppedSpecs) {
            if ($ln -match ('`' + [regex]::Escape($d) + '`')) { $hit = $true; break }
        }
        if (-not $hit) { $kept += $ln }
    }
    Save-Text $skillPath ($kept -join "`n")
}

# ---------- 重建 CATALOG ----------
$py = $cfg.paths.python
if (-not $py) { $py = 'python' }
$packCfg = Join-Path $target 'memory.config.json'
# 包里永远用模板配置，别人的机器上没有你的路径
Copy-Item -LiteralPath (Join-Path $Root 'config.example.json') -Destination $packCfg -Force
if ($cfg.pack.regenerateCatalog) {
    Push-Location $target
    try { & $py (Join-Path $target 'tools\catalog.py') | Out-Null } catch { } finally { Pop-Location }
}

# ---------- 写版本与清单 ----------
Save-Text (Join-Path $target 'VERSION') ($ver + "`n")
$manifest = @(
    ('打包版本 v' + $ver),
    ('生成时间 ' + (Get-Date).ToString('yyyy-MM-dd HH:mm')),
    ('保留 keep ' + $stat.keep + ' / 清空 blank ' + $stat.blank + ' / 重置 reset ' + $stat.reset + ' / 不发 drop ' + $stat.drop),
    ('不发：' + (($dropped) -join '、'))
) -join "`n"
Save-Text (Join-Path $target 'PACKINFO.txt') $manifest

# ---------- 压包 ----------
if ($NoZip) {
    Write-Output ('✅ 生成目录 ' + $target)
} else {
    if (-not $Out) { $Out = Join-Path $distRoot ($name + '.zip') }
    if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Force }
    Compress-Archive -Path ("$target\*") -DestinationPath $Out -CompressionLevel Optimal
    Write-Output ('✅ 打包完成 ' + $Out)
}
Write-Output ('   保留 ' + $stat.keep + ' · 清空 ' + $stat.blank + ' · 重置 ' + $stat.reset + ' · 不发 ' + $stat.drop)
