# 与现实同步的校验（只读）：CATALOG 新鲜度、检索索引新鲜度、路径可达性、笔记标签合规
# 用法：pwsh -File tools/check-freshness.ps1        （全绿 0，有问题 1）
# 注意：保存为 UTF-8 with BOM

param(
    [string]$Root = (Join-Path $env:USERPROFILE '.agents\skills\user-memory'),
    [string]$Vault = ''
)

# 路径统一从 memory.config.json 取，别写死（见 README「配置」）
if (-not $Vault) {
    $mk = Join-Path $PSScriptRoot '..\memory.config.json'
    if (Test-Path -LiteralPath $mk) {
        try {
            $mc = Get-Content -LiteralPath $mk -Raw -Encoding UTF8 | ConvertFrom-Json
            $Vault = $mc.paths.workspace
            $script:CodeRepo = $mc.paths.codeRepo
            $script:AlgoRepo = $mc.paths.algoRepo
        } catch { }
    }
}

try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$refsDir = Join-Path $Root 'references'
$issues = @()
$warns = @()

# 1) CATALOG 是否落后于模块
$catalog = Join-Path $refsDir 'CATALOG.md'
if (-not (Test-Path -LiteralPath $catalog)) {
    $issues += 'CATALOG.md 不存在：跑 tools/catalog.py'
} else {
    $catTime = (Get-Item -LiteralPath $catalog).LastWriteTime
    $newer = @(Get-ChildItem $refsDir -File -Filter *.md |
        Where-Object { $_.Name -notin @('CATALOG.md', '变更日志.md') -and $_.LastWriteTime -gt $catTime })
    if ($newer.Count -gt 0) {
        $warns += ('CATALOG 落后：' + $newer.Count + ' 个模块比它新，跑 tools/catalog.py（如 ' + $newer[0].Name + '）')
    }
}

# 2) memdb 索引是否落后于 md
$db = Join-Path $Root 'tools\memdb.sqlite3'
if (-not (Test-Path -LiteralPath $db)) {
    $warns += 'memdb.sqlite3 不存在：跑 memdb.py index'
} else {
    $dbTime = (Get-Item -LiteralPath $db).LastWriteTime
    $newerMd = @(Get-ChildItem $refsDir -File -Filter *.md | Where-Object { $_.Name -ne 'CATALOG.md' -and $_.LastWriteTime -gt $dbTime })
    if ($newerMd.Count -gt 0) {
        $warns += ('检索索引落后：' + $newerMd.Count + ' 个模块比 memdb 新，跑 memdb.py index（如 ' + $newerMd[0].Name + '）')
    }
}

# 3) 关键路径可达性（换设备/挪目录后最容易失效）
$paths = @()
foreach ($p in @($Vault, $script:CodeRepo, $script:AlgoRepo, $Root, (Join-Path $env:USERPROFILE '.dsh\profiles\desktop'))) {
    if ($p) { $paths += $p }
}
foreach ($p in $paths) {
    if (-not (Test-Path -LiteralPath $p)) { $issues += ('路径失效：' + $p + '（记忆里还在引用）') }
}

# 4) 笔记标签是否都在白名单里
$whitelistFile = Join-Path $refsDir '02-学习习惯与笔记偏好.md'
if (Test-Path -LiteralPath $whitelistFile) {
    $wlText = Get-Content -LiteralPath $whitelistFile -Raw -Encoding UTF8
    $allowed = @([regex]::Matches($wlText, '\|\s*`([^`]+)`\s*\|') | ForEach-Object { $_.Groups[1].Value })
    if ($allowed.Count -gt 0 -and $Vault -and (Test-Path -LiteralPath $Vault)) {
        $bad = @()
        foreach ($f in (Get-ChildItem $Vault -Recurse -Filter *.md -File |
                        Where-Object { $_.FullName -notmatch '\\\.' })) {
            $raw = Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($raw -match '(?s)^---\r?\n(.*?)\r?\n---') {
                $fm = $Matches[1]
                foreach ($ln in ($fm -split "`n")) {
                    if ($ln -match '^\s*-\s*([^\s#][^\r\n]*?)\s*$') {
                        $t = $Matches[1].Trim().Trim('"', "'")
                        if ($t -and $allowed -notcontains $t) { $bad += ($t + ' <- ' + $f.Name) }
                    }
                }
            }
        }
        if ($bad.Count -gt 0) {
            $warns += ('笔记标签越界 ' + $bad.Count + ' 处：' + (($bad | Select-Object -Unique) -join '; '))
        }
    }
}

Write-Output ('=== 同步校验 ' + (Get-Date).ToString('yyyy-MM-dd HH:mm') + ' ===')
if ($issues.Count -eq 0 -and $warns.Count -eq 0) {
    Write-Output '✅ 全部同步：目录、索引、路径、标签都对得上'
    exit 0
}
if ($issues.Count) {
    Write-Output ('❌ 必须修 (' + $issues.Count + ')')
    $issues | ForEach-Object { Write-Output ('  - ' + $_) }
}
if ($warns.Count) {
    Write-Output ('⚠️ 建议看 (' + $warns.Count + ')')
    $warns | ForEach-Object { Write-Output ('  - ' + $_) }
}
exit 1
